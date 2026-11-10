"""SQLite access: schema, connections, history queries and backups."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  ts     INTEGER PRIMARY KEY,
  temp_c REAL NOT NULL CHECK (temp_c BETWEEN -55 AND 125)
) STRICT
"""

DAY = 86400
# range -> (window seconds, bucket seconds). Bucket 1 keeps raw readings.
RANGES = {
    "24h": (DAY, 1),
    "7d": (7 * DAY, 1800),
    "30d": (30 * DAY, 3600),
    "1y": (365 * DAY, DAY),
    "all": (None, None),
}
KEEP_BACKUPS = 14


def db_paths(data_dir):
    """The only two databases that exist. `mode` selects one of these keys."""
    data_dir = Path(data_dir)
    return {"real": data_dir / "turtlewatch.db", "demo": data_dir / "demo.db"}


@contextmanager
def connect(path, readonly=False):
    """A private connection for one operation. Never shared between threads."""
    if readonly:
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    else:
        conn = sqlite3.connect(path, timeout=5)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path):
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(SCHEMA)


def insert_reading(path, ts, temp_c):
    with connect(path) as conn:
        conn.execute("INSERT INTO readings (ts, temp_c) VALUES (?, ?)", (ts, temp_c))


def latest(path):
    """(ts, temp_c) of the newest reading, or None."""
    with connect(path, readonly=True) as conn:
        return conn.execute(
            "SELECT ts, temp_c FROM readings ORDER BY ts DESC LIMIT 1"
        ).fetchone()


def history(path, range_key, tz_minutes, end, raw_step):
    """Aggregated points and raw-row statistics for one window ending at `end`.

    tz_minutes is the viewer's UTC offset, positive east, so day buckets
    start at the viewer's local midnight.
    """
    span, bucket = RANGES[range_key]
    tz = tz_minutes * 60
    with connect(path, readonly=True) as conn:
        if span is None:
            first = conn.execute("SELECT MIN(ts) FROM readings").fetchone()[0]
            start = end if first is None else min(first, end)
            bucket = DAY if end - start <= 730 * DAY else 7 * DAY
        else:
            start = end - span
        window = {"start": start, "end": end}
        points = conn.execute(
            """
            SELECT ((ts + :tz) / :bucket) * :bucket - :tz AS t,
                   AVG(temp_c), MIN(temp_c), MAX(temp_c), COUNT(*)
            FROM readings
            WHERE ts >= :start AND ts <= :end
            GROUP BY t ORDER BY t
            """,
            {**window, "tz": tz, "bucket": bucket},
        ).fetchall()
        count, low, high, avg = conn.execute(
            """
            SELECT COUNT(*), MIN(temp_c), MAX(temp_c), AVG(temp_c)
            FROM readings WHERE ts >= :start AND ts <= :end
            """,
            window,
        ).fetchone()

    def r(value):
        return None if value is None else round(value, 2)

    return {
        "start": start,
        "end": end,
        "bucket": 0 if bucket == 1 else bucket,
        "step": raw_step if bucket == 1 else bucket,
        "points": [[t, r(a), r(lo), r(hi), n] for t, a, lo, hi, n in points],
        "stats": {"count": count, "min_c": r(low), "max_c": r(high), "avg_c": r(avg)},
    }


def backup(src, backups_dir, keep=KEEP_BACKUPS):
    """Consistent snapshot through SQLite's backup API; keeps the newest `keep`."""
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = backups_dir / f"turtlewatch-{time.strftime('%Y%m%d-%H%M%S')}.db"
    partial = dest.with_suffix(".partial")
    with connect(src, readonly=True) as source:
        target = sqlite3.connect(partial)
        try:
            source.backup(target)
        finally:
            target.close()
    partial.replace(dest)
    for old in sorted(backups_dir.glob("turtlewatch-*.db"))[:-keep]:
        old.unlink()
    return dest


def snapshot_due(backups_dir, max_age=DAY):
    snapshots = list(Path(backups_dir).glob("turtlewatch-*.db"))
    if not snapshots:
        return True
    return time.time() - max(s.stat().st_mtime for s in snapshots) > max_age
