"""Boxoffice: a ticket-selling API built to fail in instructive ways.

Every behaviour a lab needs to change is an environment variable ("knob"), so a
lab flips it with `az containerapp update --set-env-vars ...`. That also creates
a new revision, which is exactly what a deploy is: a change to a live system.
"""
import os
import random
import threading
import time
import uuid

if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    # Must run before FastAPI, requests and psycopg2 are imported so they get instrumented.
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor()

import psycopg2
import psycopg2.pool
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace


def knob(name, default, cast=str):
    return cast(os.getenv(name, default))


RELEASE = knob("RELEASE", "v1")
DEPS_URL = knob("DEPS_URL", "http://localhost:8001").rstrip("/")
LEGACY_200_ERRORS = knob("LEGACY_200_ERRORS", "1") == "1"  # Lab 01
DB_POOL = knob("DB_POOL", "5", int)                        # Lab 04
DB_WAIT_S = knob("DB_WAIT_S", "5", float)
RESERVE_MODE = knob("RESERVE_MODE", "v1")                  # Lab 04: v1 | v2
PAY_TIMEOUT_S = knob("PAY_TIMEOUT_S", "2", float)          # Lab 05
PAY_ATTEMPTS = knob("PAY_ATTEMPTS", "3", int)
PAY_BACKOFF = knob("PAY_BACKOFF", "none")                  # none | jitter
PAY_IDEMPOTENCY = knob("PAY_IDEMPOTENCY", "0") == "1"
RECS_TIMEOUT_S = knob("RECS_TIMEOUT_S", "30", float)       # Lab 06
RECS_BULKHEAD = knob("RECS_BULKHEAD", "0", int)            # 0 = no isolation, no fallback
CACHE_EVENTS_S = knob("CACHE_EVENTS_S", "0", float)        # Lab 06/07
SHED = knob("SHED", "0") == "1"                            # Lab 07
MAX_INFLIGHT = knob("MAX_INFLIGHT", "64", int)
GROUP_BUG = knob("GROUP_BUG", "0") == "1"                  # Lab 08
DB_PREPING = knob("DB_PREPING", "0") == "1"                # Lab 10

tracer = trace.get_tracer("boxoffice")


class Overloaded(Exception):
    """We refused work on purpose. Mapped to 503 + Retry-After."""


class UpstreamFailed(Exception):
    """A dependency still failed after our retries. Mapped to 502."""


pool = psycopg2.pool.ThreadedConnectionPool(1, DB_POOL, os.environ["DATABASE_URL"])
pool_slots = threading.BoundedSemaphore(DB_POOL)


class db:
    """Borrow a pooled connection. Waiting for one is its own span, so pool
    starvation shows up in a trace instead of hiding inside request latency."""

    def __enter__(self):
        with tracer.start_as_current_span("db.pool.wait"):
            if not pool_slots.acquire(timeout=DB_WAIT_S):
                raise Overloaded("no database connection free")
        try:
            self.conn = pool.getconn()
            if DB_PREPING:
                self._ping()
        except Exception:
            pool_slots.release()
            raise
        return self.conn

    def _ping(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg2.Error:
            pool.putconn(self.conn, close=True)
            self.conn = pool.getconn()

    def __exit__(self, exc_type, exc, tb):
        broken = isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))
        try:
            if exc_type is None:
                self.conn.commit()
            elif not broken:
                self.conn.rollback()
        finally:
            pool.putconn(self.conn, close=broken or bool(self.conn.closed))
            pool_slots.release()
        return False


app = FastAPI(title="boxoffice")
inflight = 0  # only touched on the event loop, so it needs no lock


@app.middleware("http")
async def admission(request: Request, call_next):
    global inflight
    trace.get_current_span().set_attribute("app.release", RELEASE)
    # Checkout may use the whole budget; browsing is shed first.
    limit = MAX_INFLIGHT if request.url.path == "/checkout" else int(MAX_INFLIGHT * 0.75)
    if SHED and inflight >= limit:
        return JSONResponse({"error": "busy, retry later"}, status_code=503, headers={"Retry-After": "2"})
    inflight += 1
    try:
        return await call_next(request)
    finally:
        inflight -= 1


@app.exception_handler(Overloaded)
async def on_overloaded(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=503, headers={"Retry-After": "2"})


@app.exception_handler(UpstreamFailed)
async def on_upstream_failed(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/healthz")
async def healthz():
    """Liveness: runs on the event loop, so it answers even when every worker thread is stuck."""
    return {"ok": True, "release": RELEASE}


@app.get("/readyz")
def readyz():
    """Readiness: needs a worker thread and a database connection."""
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {"ready": True}


_events_cache = {"at": 0.0, "body": None}


@app.get("/events")
def events():
    if CACHE_EVENTS_S and time.time() - _events_cache["at"] < CACHE_EVENTS_S:
        return _events_cache["body"]
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, seats_left FROM events ORDER BY id")
        rows = [{"id": r[0], "name": r[1], "seats_left": r[2]} for r in cur.fetchall()]
    body = {"events": rows, "recommended": recommendations()}
    _events_cache.update(at=time.time(), body=body)
    return body


recs_slots = threading.BoundedSemaphore(RECS_BULKHEAD) if RECS_BULKHEAD else None


def recommendations():
    """Nice to have. v1 treats it as essential and waits as long as it takes."""
    if recs_slots and not recs_slots.acquire(blocking=False):
        return []
    try:
        r = requests.get(f"{DEPS_URL}/recs", timeout=RECS_TIMEOUT_S)
        r.raise_for_status()
        return r.json()["items"]
    except requests.RequestException:
        if recs_slots:
            return []
        raise UpstreamFailed("recommendations unavailable")
    finally:
        if recs_slots:
            recs_slots.release()


def quote(event_id, seats):
    r = requests.get(f"{DEPS_URL}/price", params={"event": event_id, "seats": seats}, timeout=5)
    r.raise_for_status()
    return r.json()["cents"]


@app.post("/reserve")
def reserve(body: dict):
    event_id, seats = int(body["event_id"]), int(body.get("seats", 2))
    if GROUP_BUG and seats > 4:
        raise RuntimeError("group pricing table missing")  # the bad release in Lab 08
    with db() as conn, conn.cursor() as cur:
        if RESERVE_MODE == "v1":
            cur.execute("SELECT seats_left FROM events WHERE id = %s FOR UPDATE", (event_id,))
            left = cur.fetchone()[0]
            price = quote(event_id, seats)  # a remote call while holding the row lock
            if left < seats:
                return JSONResponse({"error": "sold out"}, status_code=409)
            cur.execute("UPDATE events SET seats_left = seats_left - %s WHERE id = %s", (seats, event_id))
        else:
            price = quote(event_id, seats)
            cur.execute(
                "UPDATE events SET seats_left = seats_left - %s WHERE id = %s AND seats_left >= %s RETURNING seats_left",
                (seats, event_id, seats),
            )
            if cur.fetchone() is None:
                return JSONResponse({"error": "sold out"}, status_code=409)
        rid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO reservations (id, event_id, seats, price_cents) VALUES (%s, %s, %s, %s)",
            (rid, event_id, seats, price),
        )
    return {"reservation_id": rid, "price_cents": price}


@app.post("/checkout")
def checkout(body: dict):
    rid = body["reservation_id"]
    try:
        charge_id = pay(rid)
    except UpstreamFailed as exc:
        if LEGACY_200_ERRORS:
            # The original authors wanted the mobile app to "show a friendly message".
            return {"status": "failed", "reason": str(exc)}
        raise
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE reservations SET paid = true, charge_id = %s WHERE id = %s", (charge_id, rid))
    return {"status": "paid", "charge_id": charge_id}


def pay(rid):
    headers = {"Idempotency-Key": rid} if PAY_IDEMPOTENCY else {}
    for attempt in range(1, PAY_ATTEMPTS + 1):
        try:
            r = requests.post(f"{DEPS_URL}/pay", json={"reservation_id": rid}, headers=headers, timeout=PAY_TIMEOUT_S)
            if r.status_code < 500 and r.status_code != 409:
                r.raise_for_status()
                return r.json()["charge_id"]
        except (requests.Timeout, requests.ConnectionError):
            pass
        if attempt < PAY_ATTEMPTS and PAY_BACKOFF == "jitter":
            time.sleep(random.uniform(0, min(2.0, 0.1 * 2 ** attempt)))
    raise UpstreamFailed("payment provider did not confirm the charge")
