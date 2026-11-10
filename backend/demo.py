"""Deterministic simulated readings for demo.db. Never touches the real database.

Simulates a heated tank kept around 76-77 °F with mild night dips and small blips,
punctuated once or twice a month by a multi-day episode: a heat wave, a storm
or cold spell, a broken heater (the tank cools until a replacement arrives), or a
broken sensor (no readings for days).
"""

import math
import random
import time
from functools import lru_cache

import db

SEED = 20261110
STEP = 300
SLOTS = db.DAY // STEP
HOUR = 3600
BLOCK_DAYS = 30           # episodes are scheduled per 30-day block
TAIL = db.DAY             # longest recovery after an episode ends
QUIET = 2 * db.DAY        # the newest two days of a seed stay calm, so Demo opens healthy

# kind: ((shortest, longest) in days, (smallest, largest) change in °C)
EPISODES = {
    "heatwave": ((2, 4), (1.8, 3.2)),
    "storm": ((1, 3), (-3.8, -2.0)),
    "heater": ((2, 5), (-7.5, -5.5)),
    "sensor": ((1.5, 4), (0, 0)),
}
# A fixed rotation keeps the yearly mix balanced; a random pick of ~28 a year skews badly.
ROTATION = ("heatwave", "storm", "heater", "storm", "heatwave", "sensor", "storm", "heater")


@lru_cache(maxsize=8)
def _drift(day):
    return random.Random(SEED * 1_000_003 + day).uniform(-0.6, 0.6)


@lru_cache(maxsize=16)
def _block_episodes(block):
    """(kind, start_ts, end_ts, change_c) for one 30-day block, one per segment, never overlapping."""
    rng = random.Random(SEED * 31 + block)
    count = rng.choice((1, 1, 2))
    segment = BLOCK_DAYS * db.DAY / count
    found = []
    for i in range(count):
        kind = ROTATION[(block * 3 + i) % len(ROTATION)]
        days, change = EPISODES[kind]
        length = rng.uniform(*days) * db.DAY
        start = block * BLOCK_DAYS * db.DAY + i * segment + rng.uniform(0, segment - length - TAIL)
        found.append((kind, int(start), int(start + length), rng.uniform(*change)))
    return tuple(found)


def _episode_effect(ts, local_hour, quiet_from):
    """(°C to add, reading missing?) from any episode affecting ts."""
    block = ts // db.DAY // BLOCK_DAYS
    offset, missing = 0.0, False
    for kind, start, end, change in _block_episodes(block - 1) + _block_episodes(block):
        if end + TAIL > quiet_from or not start <= ts <= end + TAIL:
            continue
        hours_in, hours_after = (ts - start) / HOUR, (ts - end) / HOUR
        if kind == "sensor":
            missing = missing or ts <= end
        elif kind == "heatwave":  # builds over half a day, strongest in the afternoon
            level = min(1, hours_in / 12) if ts <= end else max(0, 1 - hours_after / 24)
            offset += change * level * (0.75 + 0.25 * math.cos(2 * math.pi * (local_hour - 16) / 24))
        elif kind == "storm":  # cold rain drops it within the hour
            level = min(1, hours_in / 1.5) if ts <= end else max(0, 1 - hours_after / 18)
            offset += change * level
        else:  # heater: drifts down toward ambient, then the replacement warms it back up
            reached = 1 - math.exp(-(min(ts, end) - start) / HOUR / 8)
            level = reached if ts <= end else reached * max(0, 1 - hours_after / 10)
            offset += change * level
    return offset, missing


def generate_day(day, quiet_from=math.inf):
    """All 288 slots of one UTC day as (ts, temp_c or None). Same output every call."""
    rng = random.Random(SEED * 7_000_003 + day)
    day_start = day * db.DAY
    utc_offset = time.localtime(day_start).tm_gmtoff
    drift_today, drift_next = _drift(day), _drift(day + 1)

    blip = None
    if rng.random() < 1 / 40:  # a few hours' dip (water change) or warm spike
        size = rng.uniform(-6.5, -3.5) if rng.random() < 0.7 else rng.uniform(2.0, 3.5)
        blip = (rng.randrange(150), size)
    outage = None
    if rng.random() < 1 / 12:
        start = rng.randrange(SLOTS)
        outage = range(start, start + rng.randrange(4, 73))  # 20 minutes to 6 hours

    readings = []
    for slot in range(SLOTS):
        ts = day_start + slot * STEP
        jitter = rng.gauss(0, 0.04)
        dropped = rng.random() < 0.005
        local_hour = ((ts + utc_offset) % db.DAY) / 3600
        episode, sensor_down = _episode_effect(ts, local_hour, quiet_from)
        if sensor_down or dropped or (outage and slot in outage):
            readings.append((ts, None))
            continue
        days = ts / db.DAY
        seasonal = 0.5 * math.cos(2 * math.pi * ((days % 365.2425) - 205) / 365.2425)
        daily = 0.7 * math.cos(2 * math.pi * (local_hour - 16.5) / 24)
        drift = drift_today + (drift_next - drift_today) * slot / SLOTS
        temp = 24.8 + seasonal + daily + drift + jitter + episode
        if blip and slot >= blip[0] and ts < quiet_from:
            temp += blip[1] * math.exp(-(slot - blip[0]) / 30)
        readings.append((ts, round(temp, 2)))
    return readings


def generate(start, end, quiet_from=math.inf):
    for day in range(start // db.DAY, end // db.DAY + 1):
        for ts, temp in generate_day(day, quiet_from):
            if start <= ts <= end and temp is not None:
                yield ts, temp


def seed(path, days, now=None):
    """Regenerate the last `days` days up to now; returns the number of readings written.

    The window is replaced, not topped up, so the calm final two days always line up
    with the newest reading. Demo data older than the window is left alone.
    """
    end = int(now if now is not None else time.time()) // STEP * STEP
    start = end - days * db.DAY
    db.init_db(path)
    with db.connect(path) as conn:
        conn.execute("DELETE FROM readings WHERE ts BETWEEN ? AND ?", (start, end))
        before = conn.total_changes
        conn.executemany(
            "INSERT INTO readings (ts, temp_c) VALUES (?, ?)",
            generate(start, end, quiet_from=end - QUIET),
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
