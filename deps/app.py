"""deps: stand-ins for the services Boxoffice depends on but does not own.

  /pay    payment provider with finite capacity: when it is slow, a queue forms
  /price  pricing engine (Boxoffice v1 calls it while holding a row lock)
  /recs   recommendations, nice to have

Faults change at runtime through /admin/faults, so no redeploy is needed.
Run exactly one replica: all state lives in memory.
"""
import asyncio
import os
import random
import uuid

if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="deps")

DEFAULTS = {
    "pay_latency_ms": 150,
    "pay_fail_rate": 0.0,
    "pay_capacity": 30,  # charges the provider works on at once; the rest wait in line
    "price_latency_ms": 20,
    "recs_latency_ms": 40,
    "recs_fail_rate": 0.0,
}
faults = dict(DEFAULTS)
pay_slots = asyncio.Semaphore(DEFAULTS["pay_capacity"])
stats = {"pay_calls": 0, "charges": 0, "duplicate_charges": 0, "pay_waiting": 0}
charged = set()
by_key = {}  # Idempotency-Key -> result, or None while the first request is still running
revert_task = None


@app.post("/pay")
async def pay(body: dict, request: Request):
    stats["pay_calls"] += 1
    key = request.headers.get("Idempotency-Key")
    if key in by_key:
        if by_key[key] is None:
            return JSONResponse({"error": "a request with this key is in progress"}, status_code=409)
        return by_key[key]
    if key:
        by_key[key] = None
    stats["pay_waiting"] += 1
    async with pay_slots:
        stats["pay_waiting"] -= 1
        # A long queue makes the provider slower too (cache churn, GC, lock contention).
        # That is what lets an overload outlive whatever triggered it.
        slowdown = 1 + stats["pay_waiting"] / max(1, int(faults["pay_capacity"]))
        # Work continues even if the caller has already timed out and gone away.
        await asyncio.sleep(faults["pay_latency_ms"] * slowdown / 1000)
        if random.random() < faults["pay_fail_rate"]:
            by_key.pop(key, None)
            return JSONResponse({"error": "provider error"}, status_code=503)
    rid = body["reservation_id"]
    if rid in charged:
        stats["duplicate_charges"] += 1
    charged.add(rid)
    stats["charges"] += 1
    result = {"charge_id": str(uuid.uuid4())}
    if key:
        by_key[key] = result
    return result


@app.get("/price")
async def price(event: int, seats: int):
    await asyncio.sleep(faults["price_latency_ms"] / 1000)
    return {"cents": 4500 * seats}


@app.get("/recs")
async def recs():
    await asyncio.sleep(faults["recs_latency_ms"] / 1000)
    if random.random() < faults["recs_fail_rate"]:
        return JSONResponse({"error": "recommendations down"}, status_code=503)
    return {"items": [{"event_id": 2, "why": "fans of Arctic Lights also bought"}]}


def apply(changes):
    global pay_slots
    faults.update(changes)
    if "pay_capacity" in changes:
        pay_slots = asyncio.Semaphore(int(faults["pay_capacity"]))


async def revert_after(seconds, previous):
    await asyncio.sleep(seconds)
    apply(previous)


@app.get("/admin/faults")
async def get_faults():
    return {"faults": faults, "stats": stats}


@app.post("/admin/faults")
async def set_faults(body: dict):
    """Body: any subset of the fault keys. Add "for_s": N to put those keys back after N seconds."""
    global revert_task
    for_s = body.pop("for_s", None)
    unknown = set(body) - set(DEFAULTS)
    if unknown:
        return JSONResponse({"error": f"unknown keys: {sorted(unknown)}"}, status_code=400)
    previous = {k: faults[k] for k in body}
    apply(body)
    if revert_task:
        revert_task.cancel()
        revert_task = None
    if for_s:
        revert_task = asyncio.create_task(revert_after(float(for_s), previous))
    return {"faults": faults, "reverts_in_s": for_s}


@app.post("/admin/reset")
async def reset():
    apply(dict(DEFAULTS))
    stats.update(pay_calls=0, charges=0, duplicate_charges=0, pay_waiting=0)
    charged.clear()
    by_key.clear()
    return {"faults": faults, "stats": stats}
