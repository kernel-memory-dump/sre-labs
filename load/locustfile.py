"""Fans buying tickets. SHAPE picks the traffic pattern; CLIENT_RETRIES and
CLIENT_BACKOFF describe how the mobile app retries a failed checkout.

Locust is a closed-loop generator: each user waits for a response before sending
the next request, so it backs off on its own when the service slows down.
Real internet traffic keeps arriving. Expect real incidents to be worse."""
import os
import random
import time

from locust import FastHttpUser, LoadTestShape, between, task

SHAPE = os.getenv("SHAPE", "trickle")
CLIENT_RETRIES = int(os.getenv("CLIENT_RETRIES", "3"))
CLIENT_BACKOFF = os.getenv("CLIENT_BACKOFF", "0") == "1"  # honour Retry-After, else back off with jitter
HOT_EVENT = 1


class Fan(FastHttpUser):
    wait_time = between(0.5, 2)

    @task(8)
    def browse(self):
        self.client.get("/events")

    @task(2)
    def buy(self):
        event = HOT_EVENT if random.random() < 0.7 else random.randint(2, 5)
        seats = 6 if random.random() < 0.02 else random.choice([1, 2, 2, 3, 4])  # ~2% group bookings
        r = self.client.post("/reserve", json={"event_id": event, "seats": seats})
        if r.status_code != 200:
            return
        rid = r.json()["reservation_id"]
        for attempt in range(1 + CLIENT_RETRIES):
            c = self.client.post("/checkout", json={"reservation_id": rid})
            if c.status_code < 500:
                break
            if CLIENT_BACKOFF:
                retry_after = c.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else random.uniform(0, 2 ** attempt))


class Shape(LoadTestShape):
    """(users, spawn rate) over time; returning None ends the run."""

    def tick(self):
        t = self.get_run_time()
        if SHAPE == "off":
            return None
        if SHAPE == "trickle":  # background traffic so SLO windows have data
            return (10, 5)
        if SHAPE == "steady":
            return (100, 20) if t < 900 else None
        if SHAPE == "ramp":  # +50 users a minute up to 1000
            return (min(1000, 50 * (int(t // 60) + 1)), 50) if t < 1260 else None
        if SHAPE == "onsale":  # 20x for three minutes, starting at t=120
            if t >= 600:
                return None
            return (2000, 400) if 120 <= t < 300 else (100, 100)
        if SHAPE == "brownout":  # steady load long enough to watch a recovery
            return (150, 30) if t < 1200 else None
        raise ValueError(f"unknown SHAPE {SHAPE}")
