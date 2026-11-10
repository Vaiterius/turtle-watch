"""Deterministic simulated readings for demo.db. Never touches the real database."""

import math
import random
import time
from functools import lru_cache

import db

SEED = 20261110
STEP = 300
SLOTS = db.DAY // STEP


@lru_cache(maxsize=8)
def _drift(day):
    return random.Random(SEED * 1_000_003 + day).uniform(-1.5, 1.5)


def _long_outage(day):
    """A rare outage of a day or two, once per 120-day block."""
    start = random.Random(SEED + day // 120).randrange(120)
    return day % 120 in (start, start + 1)


def generate_day(day):
    """All 288 slots of one UTC day as (ts, temp_c or None). Same output every call."""
    rng = random.Random(SEED * 7_000_003 + day)
    day_start = day * db.DAY
    utc_offset = time.localtime(day_start).tm_gmtoff
    drift_today, drift_next = _drift(day), _drift(day + 1)

    event = None
    if rng.random() < 1 / 20:  # cold snap (water change, storm) or heat spike
        size = rng.uniform(-4.0, -2.0) if rng.random() < 0.7 else rng.uniform(1.5, 3.0)
        event = (rng.randrange(150), size)
    outage = None
    if rng.random() < 1 / 12:
        start = rng.randrange(SLOTS)
        outage = range(start, start + rng.randrange(4, 73))  # 20 minutes to 6 hours
    all_missing = _long_outage(day)

    readings = []
    for slot in range(SLOTS):
        ts = day_start + slot * STEP
        jitter = rng.gauss(0, 0.04)
        dropped = rng.random() < 0.005
        if all_missing or dropped or (outage and slot in outage):
            readings.append((ts, None))
            continue
        days = ts / db.DAY
        seasonal = 6.0 * math.cos(2 * math.pi * ((days % 365.2425) - 205) / 365.2425)
        local_hour = ((ts + utc_offset) % db.DAY) / 3600
        daily = 1.6 * math.cos(2 * math.pi * (local_hour - 16.5) / 24)
        drift = drift_today + (drift_next - drift_today) * slot / SLOTS
        temp = 21.0 + seasonal + daily + drift + jitter
        if event and slot >= event[0]:
            temp += event[1] * math.exp(-(slot - event[0]) / 30)
        readings.append((ts, round(temp, 2)))
    return readings


def generate(start, end):
    for day in range(start // db.DAY, end // db.DAY + 1):
        for ts, temp in generate_day(day):
            if start <= ts <= end and temp is not None:
                yield ts, temp


def seed(path, days, now=None):
    """Fill the last `days` days up to now. Re-running never duplicates a timestamp."""
    end = int(now if now is not None else time.time()) // STEP * STEP
    db.init_db(path)
    with db.connect(path) as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO readings (ts, temp_c) VALUES (?, ?)",
            generate(end - days * db.DAY, end),
        )
        return conn.total_changes - before


def clear(path):
    db.init_db(path)
    with db.connect(path) as conn:
        return conn.execute("DELETE FROM readings").rowcount


def status(path):
    db.init_db(path)
    with db.connect(path, readonly=True) as conn:
        count, first, last = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM readings").fetchone()
    return {"count": count, "first": first, "last": last, "bytes": path.stat().st_size}
