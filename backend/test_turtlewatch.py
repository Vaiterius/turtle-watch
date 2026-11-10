"""Backend tests. Run: python -m unittest discover -s backend"""

import hashlib
import json
import os
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import db
import demo
import shelly
from collector import Collector
from server import create_app, excursion

BACKEND = Path(__file__).resolve().parent
T0 = 1_750_000_000 // db.DAY * db.DAY  # a UTC midnight


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TempDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.paths = db.db_paths(self.dir)
        for path in self.paths.values():
            db.init_db(path)

    def insert(self, rows, mode="real"):
        with db.connect(self.paths[mode]) as conn:
            conn.executemany("INSERT INTO readings VALUES (?, ?)", rows)


class MockShelly:
    """A local HTTP server standing in for the Pill. `behaviour` is swappable."""

    def __init__(self, components=None):
        self.components = components if components is not None else [
            ("sys", {}), ("wifi", {}), ("temperature:100", {"id": 100, "tC": 24.4, "tF": 75.9})]
        self.behaviour = "ok"
        self.page_size = 50
        self.calls = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                params = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method = self.path.rsplit("/", 1)[-1]
                mock.calls.append((method, params))
                if mock.behaviour == "timeout":
                    time.sleep(1.0)
                    return
                if mock.behaviour == "500":
                    return self.reply(500, b"{}")
                if mock.behaviour == "garbage":
                    return self.reply(200, b"<html>not json")
                if method == "Temperature.GetStatus":
                    status = dict(mock.components).get(f"temperature:{params['id']}")
                    return self.reply(200 if status else 500, json.dumps(status or {}).encode())
                offset = params.get("offset", 0)
                page = mock.components[offset:offset + mock.page_size]
                self.reply(200, json.dumps({
                    "components": [{"key": k, "status": s} for k, s in page],
                    "cfg_rev": 1, "offset": offset, "total": len(mock.components)}).encode())

            def reply(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.handle_error = lambda *args: None  # clients that timed out hang up
        self.host = f"127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AggregationTests(TempDirCase):
    def test_buckets_and_partial_first_bucket(self):
        # 30-minute buckets over 7 days; the window starts mid-bucket.
        end = T0 + 7 * db.DAY + 600
        rows = [(end - 7 * db.DAY + i * 300, 20 + i) for i in range(12)]
        self.insert(rows)
        h = db.history(self.paths["real"], "7d", 0, end, 300)
        self.assertEqual(h["bucket"], 1800)
        # First bucket holds only the readings inside the window (ts >= start).
        self.assertEqual(h["points"][0], [T0, 21.5, 20.0, 23.0, 4])
        self.assertEqual(h["points"][1], [T0 + 1800, 26.5, 24.0, 29.0, 6])
        self.assertEqual(sum(p[4] for p in h["points"]), 12)

    def test_stats_come_from_raw_rows_not_buckets(self):
        end = T0 + db.DAY
        # Bucket A has four readings of 10, bucket B a single reading of 30.
        rows = [(end - 7200 + i * 300, 10.0) for i in range(4)] + [(end - 1800, 30.0)]
        self.insert(rows)
        h = db.history(self.paths["real"], "7d", 0, end, 300)
        raw_mean = statistics.fmean(t for _, t in rows)
        bucket_mean = statistics.fmean(p[1] for p in h["points"])
        self.assertAlmostEqual(h["stats"]["avg_c"], raw_mean, places=2)
        self.assertNotAlmostEqual(bucket_mean, raw_mean, places=1)
        self.assertEqual((h["stats"]["min_c"], h["stats"]["max_c"], h["stats"]["count"]),
                         (10.0, 30.0, 5))

    def test_gaps_are_not_filled(self):
        end = T0 + db.DAY
        self.insert([(end - 20 * 3600, 20.0), (end - 3600, 21.0)])
        h = db.history(self.paths["real"], "30d", 0, end, 300)
        self.assertEqual(len(h["points"]), 2)  # 18 empty hourly buckets stay absent
        for point in h["points"]:
            self.assertNotIn(None, point)
            self.assertGreater(point[4], 0)

    def test_tz_sign_puts_day_buckets_on_local_midnight(self):
        # 23:30 Pacific standard time on the local day that starts at T0 + 8 h UTC.
        local_midnight = T0 + 8 * 3600
        late_evening = local_midnight + 23 * 3600 + 1800
        self.insert([(late_evening, 20.0)])
        h = db.history(self.paths["real"], "1y", -480, late_evening + 60, 300)
        self.assertEqual(h["points"][0][0], local_midnight)
        flipped = db.history(self.paths["real"], "1y", 480, late_evening + 60, 300)
        self.assertNotEqual(flipped["points"][0][0], local_midnight)

    def test_range_buckets(self):
        end = T0 + 900 * db.DAY
        self.insert([(end - 100 * db.DAY, 20.0), (end - 10, 21.0)])
        expect = {"24h": (0, 300), "7d": (1800, 1800), "30d": (3600, 3600),
                  "1y": (db.DAY, db.DAY), "all": (db.DAY, db.DAY)}
        for key, (bucket, step) in expect.items():
            h = db.history(self.paths["real"], key, 0, end, 300)
            self.assertEqual((h["bucket"], h["step"]), (bucket, step), key)
        self.assertEqual(db.history(self.paths["real"], "24h", 0, end, 300)["points"],
                         [[end - 10, 21.0, 21.0, 21.0, 1]])  # raw keeps exact timestamps
        self.insert([(end - 800 * db.DAY, 19.0)])
        weekly = db.history(self.paths["real"], "all", 0, end, 300)
        self.assertEqual(weekly["bucket"], 7 * db.DAY)
        self.assertEqual(weekly["start"], end - 800 * db.DAY)

    def test_empty_database(self):
        h = db.history(self.paths["real"], "all", 0, T0, 300)
        self.assertEqual(h["points"], [])
        self.assertEqual(h["stats"], {"count": 0, "min_c": None, "max_c": None, "avg_c": None})

    def test_schema_rejects_bad_rows(self):
        for row in [(T0, None), (T0, 500.0), (T0, "warm")]:
            with self.assertRaises(sqlite3.Error):
                self.insert([row])


class ShellyParseTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(shelly.parse_status({"id": 0, "tC": 24.4, "tF": 75.9}), 24.4)
        self.assertEqual(shelly.parse_status({"id": 0, "tC": 20, "errors": []}), 20.0)

    def test_invalid_never_becomes_a_reading(self):
        bad = [
            ({"id": 0, "tC": None, "tF": None}, "no valid reading"),
            ({"id": 0, "tC": None, "errors": ["read"]}, "sensor error: read"),
            ({"id": 0, "tC": 21.0, "errors": ["out_of_range"]}, "sensor error: out_of_range"),
            ({"id": 0, "tC": 300}, "out of range"),
            ({"id": 0, "tC": float("nan")}, "out of range"),
            ({"id": 0, "tC": "24.4"}, "invalid response"),
            ({"id": 0, "tC": True}, "invalid response"),
            ({"id": 0}, "no valid reading"),
            ([], "invalid response"),
            (None, "invalid response"),
        ]
        for status, message in bad:
            with self.assertRaises(shelly.ShellyError, msg=status) as ctx:
                shelly.parse_status(status)
            self.assertEqual(str(ctx.exception), message)


class ShellyRpcTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockShelly()
        self.addCleanup(self.mock.close)

    def read(self, component_id=None):
        return shelly.read_temperature(self.mock.host, component_id, timeout=0.3)

    def test_discovers_any_id(self):
        for cid in (0, 100, 200):
            self.mock.components = [("sys", {}), (f"temperature:{cid}", {"id": cid, "tC": 22.5})]
            self.assertEqual(self.read(), (cid, 22.5))

    def test_discovery_across_pages(self):
        self.mock.components = [(f"script:{i}", {}) for i in range(7)] + [
            ("temperature:201", {"id": 201, "tC": 19.0})]
        self.mock.page_size = 3
        self.assertEqual(self.read(), (201, 19.0))
        self.assertEqual([p["offset"] for _, p in self.mock.calls], [0, 3, 6])

    def test_nested_temperature_is_not_a_component(self):
        self.mock.components = [("switch:0", {"temperature": {"tC": 55.0}}),
                                ("temperature", {"tC": 55.0})]
        with self.assertRaisesRegex(shelly.ShellyError, "no temperature component"):
            self.read()

    def test_multiple_components_need_configuration(self):
        self.mock.components = [("temperature:100", {"tC": 20.0}),
                                ("temperature:101", {"tC": 30.0})]
        with self.assertRaisesRegex(shelly.ShellyError, "multiple temperature components: 100, 101"):
            self.read()
        self.assertEqual(self.read(101), (101, 30.0))
        self.assertEqual(self.mock.calls[-1], ("Temperature.GetStatus", {"id": 101}))

    def test_failures(self):
        for behaviour, message in [("timeout", "timeout"), ("500", "http 500"),
                                   ("garbage", "invalid response")]:
            self.mock.behaviour = behaviour
            with self.assertRaises(shelly.ShellyError) as ctx:
                self.read()
            self.assertEqual(str(ctx.exception), message)
        with self.assertRaisesRegex(shelly.ShellyError, "unreachable"):
            shelly.read_temperature(f"127.0.0.1:{free_port()}", timeout=0.3)


class CollectorTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.mock = MockShelly()
        self.addCleanup(self.mock.close)

    def collector(self, host=None):
        return Collector(self.paths["real"], self.dir / "backups",
                         self.mock.host if host is None else host,
                         timeout=0.3, retry_delay=0.01)

    def count(self, mode="real"):
        with db.connect(self.paths[mode], readonly=True) as conn:
            return conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    def test_failures_store_nothing_then_recovery_stores_a_row(self):
        c = self.collector()
        for behaviour in ("500", "garbage", "timeout"):
            self.mock.behaviour = behaviour
            self.assertFalse(c.poll_once())
            self.assertEqual(c.status["state"], "error")
        self.mock.behaviour = "ok"
        self.mock.components = [("temperature:100", {"id": 100, "tC": None, "errors": ["read"]})]
        self.assertFalse(c.poll_once())
        self.assertEqual(c.status["error"], "sensor error: read")
        self.assertIsNone(c.status["last_success"])
        self.assertEqual(self.count(), 0)

        self.mock.components = [("temperature:100", {"id": 100, "tC": 24.4})]
        self.assertTrue(c.poll_once())
        self.assertEqual(c.status["state"], "ok")
        self.assertIsNone(c.status["error"])
        ts, temp = db.latest(self.paths["real"])
        self.assertEqual(temp, 24.4)
        self.assertLessEqual(abs(ts - time.time()), 5)

    def test_unreachable_host_and_retries(self):
        c = self.collector(f"127.0.0.1:{free_port()}")
        self.assertFalse(c.poll_once())
        self.assertEqual(c.status["error"], "unreachable")
        self.assertEqual(self.count(), 0)
        self.mock.behaviour = "500"
        self.collector().poll_once()
        self.assertEqual(len(self.mock.calls), 3)  # three attempts per cycle

    def test_unconfigured_collector_is_idle(self):
        c = self.collector("")
        self.assertFalse(c.poll_once())
        self.assertEqual(c.status["state"], "unconfigured")
        self.assertEqual(self.mock.calls, [])

    def test_polling_continues_while_demo_is_viewed(self):
        demo.seed(self.paths["demo"], 2, now=T0)
        c = self.collector()
        app = create_app(self.dir, self.dir, lambda: c.status)
        client = app.test_client()
        self.assertTrue(c.poll_once())
        demo_before = self.count("demo")
        for _ in range(3):
            self.assertEqual(client.get("/api/status?mode=demo").status_code, 200)
            self.assertEqual(client.get("/api/history?mode=demo&range=7d").status_code, 200)
        time.sleep(1.1)  # readings are keyed by the second
        self.assertTrue(c.poll_once())
        self.assertEqual(self.count("real"), 2)
        self.assertEqual(self.count("demo"), demo_before)
        self.assertEqual(client.get("/api/status?mode=demo").json["sensor"]["state"], "ok")
        self.assertEqual(len([m for m, _ in self.mock.calls]), 2)  # no extra Shelly calls

    def test_daily_snapshot_and_backup_integrity(self):
        self.insert([(T0 + i, 20.0) for i in range(500)])
        backups = self.dir / "backups"
        self.assertTrue(db.snapshot_due(backups))
        writer = sqlite3.connect(self.paths["real"])
        writer.execute("INSERT INTO readings VALUES (?, ?)", (T0 + 10_000, 21.0))  # open write
        snapshot = db.backup(self.paths["real"], backups)
        writer.commit()
        writer.close()
        self.assertFalse(db.snapshot_due(backups))
        with db.connect(snapshot, readonly=True) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 500)

    def test_old_snapshots_are_pruned(self):
        backups = self.dir / "backups"
        backups.mkdir()
        for i in range(20):
            (backups / f"turtlewatch-20200101-0000{i:02d}.db").touch()
        db.backup(self.paths["real"], backups, keep=14)
        names = sorted(p.name for p in backups.iterdir())
        self.assertEqual(len(names), 14)
        self.assertNotIn("turtlewatch-20200101-000000.db", names)


class DemoTests(TempDirCase):
    def rows(self, mode="demo"):
        with db.connect(self.paths[mode], readonly=True) as conn:
            return conn.execute("SELECT ts, temp_c FROM readings ORDER BY ts").fetchall()

    def test_deterministic_and_idempotent(self):
        added = demo.seed(self.paths["demo"], 30, now=T0)
        first = self.rows()
        self.assertEqual(added, len(first))
        self.assertEqual(demo.seed(self.paths["demo"], 30, now=T0), len(first))  # replaced, not duplicated
        self.assertEqual(self.rows(), first)
        other = self.dir / "other.db"
        demo.seed(other, 30, now=T0)
        with db.connect(other, readonly=True) as conn:
            self.assertEqual(conn.execute("SELECT ts, temp_c FROM readings ORDER BY ts").fetchall(),
                             first)
        # A later seed regenerates its window; history before the calm final days is unchanged.
        demo.seed(self.paths["demo"], 40, now=T0 + db.DAY)
        settled = {r for r in first if r[0] < T0 - demo.QUIET - demo.TAIL}
        self.assertTrue(settled <= set(self.rows()))
        self.assertEqual(len({ts for ts, _ in self.rows()}), len(self.rows()))

    def test_shape_of_generated_data(self):
        demo.seed(self.paths["demo"], 365, now=T0)
        rows = self.rows()
        self.assertTrue(all(ts % 300 == 0 for ts, _ in rows))
        self.assertTrue(all(5 < temp < 40 for _, temp in rows))
        gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
        self.assertEqual(min(gaps), 300)
        self.assertGreater(sum(1 for g in gaps if g > 300), 20)       # dropped readings
        self.assertGreater(max(gaps), db.DAY)                          # a long outage
        self.assertGreater(len(rows), 365 * 288 * 0.9)
        temps_f = [t * 9 / 5 + 32 for _, t in rows]
        optimal = sum(1 for f in temps_f if 74 <= f <= 79) / len(temps_f)
        self.assertGreater(optimal, 0.7)                               # mostly a healthy tank
        self.assertTrue(any(f > 82 for f in temps_f))                  # heat waves
        self.assertTrue(all(55 < f < 95 for f in temps_f))
        # A broken heater keeps the water below 70 °F for more than a day at a time.
        longest = run = 0
        for (a, fa), (b, fb) in zip(zip([r[0] for r in rows], temps_f), zip([r[0] for r in rows[1:]], temps_f[1:])):
            run = run + (b - a) if fa < 70 and fb < 70 and b - a <= 450 else 0
            longest = max(longest, run)
        self.assertGreater(longest, db.DAY)
        # Episodes recur all year: every 30-day block has one or two.
        for block in range(T0 // db.DAY // 30 - 11, T0 // db.DAY // 30):
            self.assertIn(len(demo._block_episodes(block)), (1, 2))
        # The newest two days are calm, so Demo opens on a healthy reading.
        recent = [f for (ts, _), f in zip(rows, temps_f) if ts > T0 - demo.QUIET]
        self.assertTrue(all(70 <= f <= 82 for f in recent))
        self.assertTrue(74 <= temps_f[-1] <= 79)

    def test_seed_and_clear_never_touch_production(self):
        self.insert([(T0, 20.0), (T0 + 300, 20.5)])
        digest = lambda: hashlib.sha256(self.paths["real"].read_bytes()).hexdigest()
        before = digest()
        demo.seed(self.paths["demo"], 10, now=T0)
        self.assertEqual(digest(), before)
        self.assertGreater(demo.clear(self.paths["demo"]), 0)
        self.assertEqual(digest(), before)
        self.assertEqual(self.rows("demo"), [])
        self.assertEqual(self.rows("real"), [(T0, 20.0), (T0 + 300, 20.5)])
        self.assertEqual(demo.status(self.paths["demo"])["count"], 0)

    def test_a_year_of_demo_data_queries_fast(self):
        demo.seed(self.paths["demo"], 365, now=T0)
        started = time.perf_counter()
        h = db.history(self.paths["demo"], "1y", -480, T0, 300)
        elapsed = time.perf_counter() - started
        print(f"\n  1Y over {h['stats']['count']} rows: {elapsed * 1000:.0f} ms, "
              f"{len(h['points'])} points", end="")
        self.assertLessEqual(len(h["points"]), 367)
        self.assertLess(elapsed, 2.0)


class ApiTests(TempDirCase):
    def setUp(self):
        super().setUp()
        (self.dir / "static" / "assets").mkdir(parents=True)
        (self.dir / "static" / "index.html").write_text("<!doctype html><title>Turtlewatch</title>")
        (self.dir / "static" / "assets" / "app.js").write_text("console.log(1)")
        (self.dir / "secret.txt").write_text("secret")
        self.sensor = {"state": "ok", "last_attempt": 1, "last_success": 1, "error": None}
        app = create_app(self.dir, self.dir / "static", lambda: self.sensor)
        self.client = app.test_client()

    def test_modes_read_only_their_own_database(self):
        now = int(time.time())
        self.insert([(now - 60, 20.0)], "real")
        self.insert([(T0, 30.0), (T0 + 300, 31.0)], "demo")
        real = self.client.get("/api/status?mode=real").json
        self.assertEqual((real["mode"], real["reading"]["temp_c"]), ("real", 20.0))
        self.assertFalse(real["reading"]["stale"])
        demo_status = self.client.get("/api/status?mode=demo").json
        self.assertEqual(demo_status["reading"],
                         {"ts": T0 + 300, "temp_c": 31.0, "age": 0, "stale": False})
        self.assertEqual(demo_status["sensor"], self.sensor)  # always the real collector
        self.assertEqual(self.client.get("/api/status").json["mode"], "real")

        real_history = self.client.get("/api/history?mode=real&range=24h").json
        self.assertEqual([p[1] for p in real_history["points"]], [20.0])
        demo_history = self.client.get("/api/history?mode=demo&range=24h&tz=-480").json
        self.assertEqual([p[1] for p in demo_history["points"]], [30.0, 31.0])
        self.assertEqual(demo_history["end"], T0 + 300)  # demo time ends at its last reading
        self.assertEqual(set(demo_history), {"mode", "range", "start", "end", "bucket", "step",
                                             "points", "stats"})

    def test_stale_threshold(self):
        now = int(time.time())
        self.insert([(now - 500, 20.0)])
        self.assertFalse(self.client.get("/api/status").json["reading"]["stale"])
        self.insert([(now - 700, 20.0)])
        with db.connect(self.paths["real"]) as conn:
            conn.execute("DELETE FROM readings WHERE ts = ?", (now - 500,))
        reading = self.client.get("/api/status").json["reading"]
        self.assertTrue(reading["stale"])
        self.assertGreaterEqual(reading["age"], 700)

    def test_empty_is_not_an_error(self):
        response = self.client.get("/api/status?mode=demo")
        self.assertEqual((response.status_code, response.json["reading"]), (200, None))
        history = self.client.get("/api/history?mode=demo&range=all").json
        self.assertEqual((history["points"], history["stats"]["count"]), ([], 0))

    def test_validation_and_json_errors(self):
        cases = {
            "/api/status?mode=prod": 400,
            "/api/status?mode=../../etc/passwd": 400,
            "/api/history?range=2h": 400,
            "/api/history?tz=abc": 400,
            "/api/history?tz=900": 400,
            "/api/history?tz=1;DROP TABLE readings": 400,
            "/api/nope": 404,
            "/missing.js": 404,
            "/../secret.txt": 404,
            "/..%2fsecret.txt": 404,
            "/assets/../../secret.txt": 404,
        }
        for url, code in cases.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, code, url)
            self.assertIn("error", response.json, url)
            self.assertNotIn(b"secret", response.data.replace(b"secret.txt", b""), url)
        for method in (self.client.post, self.client.put, self.client.delete):
            response = method("/api/status")
            self.assertEqual((response.status_code, "error" in response.json), (405, True))

    def test_static_files_and_cache_headers(self):
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn(b"Turtlewatch", index.data)
        self.assertEqual(index.headers["Cache-Control"], "no-cache")
        asset = self.client.get("/assets/app.js")
        self.assertIn("immutable", asset.headers["Cache-Control"])
        self.assertEqual(self.client.get("/api/status").headers["Cache-Control"], "no-store")

    def test_api_connection_cannot_write(self):
        fresh = self.dir / "fresh.db"
        db.init_db(fresh)  # WAL database with no -wal or -shm file left behind
        with db.connect(fresh, readonly=True) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 0)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO readings VALUES (1, 20.0)")


def f_to_c(f):
    return (f - 32) * 5 / 9


class ExcursionTests(TempDirCase):
    LIMITS = (68, 70, 74, 79, 82)

    def run_of(self, temps_f, start=T0, step=300):
        return [(start + i * step, f_to_c(f)) for i, f in enumerate(temps_f)]

    def check(self, temps_f, expected, limits=LIMITS):
        result = excursion(self.run_of(temps_f), limits, 450)
        self.assertEqual(result and (result["start"], result["level"], result["recovering"]),
                         expected, temps_f)
        return result

    def test_rules(self):
        self.check([], None)
        self.check([75, 69], None)                                   # one low reading: wait
        self.check([75, 69, 69.5], (T0 + 300, "caution", False))     # two in a row: warn
        self.check([75, 67.5], (T0 + 300, "danger", False))          # below 68: at once
        self.check([69, 69, 67], (T0, "danger", False))
        self.check([69, 69, 67, 69], (T0, "caution", False))         # back above 68
        self.check([69, 69, 71], (T0, "caution", True))              # one good reading: recovering
        self.check([69, 69, 71, 72], None)                           # two good readings: cleared
        self.check([69, 69, 71, 69], (T0, "caution", False))         # relapse keeps the start
        self.check([69, 69, 71, 72, 69], None)                       # new dip needs two again
        self.check([69.96, 69.96], None)                             # 70.0 as displayed
        self.check([72, 72], (T0, "caution", False), limits=(60, 73, 74, 79, 82))

    def test_duration_and_gaps(self):
        result = self.check([75] + [69] * 6, (T0 + 300, "caution", False))
        self.assertEqual(result["last_below"] - result["start"], 25 * 60)   # "about 25 minutes"
        # A gap (sensor offline) ends the excursion: the readings after it start afresh.
        rows = self.run_of([69] * 6) + self.run_of([69], start=T0 + 6 * 300 + 3600)
        self.assertIsNone(excursion(rows, self.LIMITS, 450))
        rows.append((rows[-1][0] + 300, f_to_c(69)))
        self.assertEqual(excursion(rows, self.LIMITS, 450)["start"], T0 + 6 * 300 + 3600)

    def test_api_reports_each_modes_own_excursion(self):
        self.insert(self.run_of([69, 69], start=T0), "demo")
        now = int(time.time()) // 300 * 300
        self.insert(self.run_of([76, 76], start=now - 300), "real")
        client = create_app(self.dir, self.dir, lambda: {}).test_client()
        self.assertEqual(client.get("/api/status?mode=demo").json["excursion"],
                         {"start": T0, "last_below": T0 + 300, "level": "caution", "recovering": False})
        self.assertIsNone(client.get("/api/status?mode=real").json["excursion"])


class ThresholdTests(TempDirCase):
    def test_status_carries_thresholds(self):
        app = create_app(self.dir, self.dir, lambda: {}, thresholds_f=(60, 65, 70, 75, 80))
        self.assertEqual(app.test_client().get("/api/status?mode=demo").json["thresholds_f"],
                         [60, 65, 70, 75, 80])
        default = create_app(self.dir, self.dir, lambda: {}).test_client()
        self.assertEqual(default.get("/api/status").json["thresholds_f"], [68, 70, 74, 79, 82])

    def test_env_parsing(self):
        import main
        cases = {"": (68.0, 70.0, 74.0, 79.0, 82.0), " 66, 69.5,73,80,84 ": (66, 69.5, 73, 80, 84)}
        for raw, expected in cases.items():
            with unittest.mock.patch.dict(os.environ, {"TEMP_THRESHOLDS_F": raw}):
                self.assertEqual(main.env_thresholds(), expected)
        for raw in ("68,70,74,79", "68,70,74,79,82,90", "70,68,74,79,82", "68,68,74,79,82", "a,b,c,d,e"):
            with unittest.mock.patch.dict(os.environ, {"TEMP_THRESHOLDS_F": raw}):
                with self.assertRaises(SystemExit, msg=raw):
                    main.env_thresholds()


class ProcessTests(unittest.TestCase):
    def serve(self, data_dir, port):
        env = {**os.environ, "DATA_DIR": str(data_dir), "PORT": str(port), "SHELLY_HOST": ""}
        return subprocess.Popen([sys.executable, str(BACKEND / "main.py"), "serve"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def wait_for_status(self, port):
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as r:
                    return json.load(r)
            except OSError:
                time.sleep(0.1)
        self.fail("server did not start")

    def test_serve_then_sigterm_exits_cleanly_and_lock_blocks_a_second_collector(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = free_port()
            first = self.serve(tmp, port)
            try:
                status = self.wait_for_status(port)
                self.assertEqual(status["sensor"]["state"], "unconfigured")
                second = self.serve(tmp, free_port())
                output, _ = second.communicate(timeout=10)
                self.assertEqual(second.returncode, 1)
                self.assertIn("Another Turtlewatch collector", output)
                first.send_signal(signal.SIGTERM)
                self.assertEqual(first.wait(timeout=10), 0)
            finally:
                first.kill()
                first.communicate()

    def test_collector_death_takes_the_process_down(self):
        code = ("import time, collector\n"
                "collector.start_guarded(lambda: 1 / 0)\n"
                "time.sleep(10)\n")
        result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, timeout=8,
                                capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_seed_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "DATA_DIR": tmp}
            run = lambda *args: subprocess.run(
                [sys.executable, str(BACKEND / "main.py"), "seed", *args],
                env=env, capture_output=True, text=True, timeout=60)
            self.assertIn("Added", run("--days", "3").stdout)
            self.assertIn("readings", run("--status").stdout)
            self.assertIn("Deleted", run("--clear").stdout)
            self.assertFalse((Path(tmp) / "turtlewatch.db").exists())  # seed never creates it
            self.assertNotEqual(run().returncode, 0)


if __name__ == "__main__":
    unittest.main()
