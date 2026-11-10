"""The collector: polls the Shelly on a fixed schedule and stores valid readings."""

import logging
import os
import threading
import time

import db
import shelly

log = logging.getLogger("turtlewatch.collector")

ATTEMPTS = 3


class Collector:
    def __init__(self, db_path, backups_dir, host, component_id=None, interval=300,
                 timeout=5, retry_delay=5):
        self.db_path = db_path
        self.backups_dir = backups_dir
        self.host = host
        self.component_id = component_id
        self.interval = interval
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.stop = threading.Event()
        self.failures = 0
        # Replaced whole, never mutated, so API threads always read a consistent snapshot.
        self.status = {
            "state": "starting" if host else "unconfigured",
            "last_attempt": None,
            "last_success": None,
            "error": None,
        }

    def _read(self):
        """One poll cycle: up to ATTEMPTS tries. Returns Celsius or raises ShellyError."""
        for attempt in range(ATTEMPTS):
            try:
                component_id, temp_c = shelly.read_temperature(
                    self.host, self.component_id, self.timeout)
                if self.status["state"] != "ok":
                    log.info("Shelly OK: temperature:%s = %.2f C", component_id, temp_c)
                return temp_c
            except shelly.ShellyError:
                if attempt == ATTEMPTS - 1 or self.stop.wait(self.retry_delay):
                    raise

    def poll_once(self):
        """Store one reading if, and only if, the sensor returned a valid one."""
        if not self.host:
            return False
        attempt_ts = int(time.time())
        try:
            temp_c = self._read()
            ts = int(time.time())
            db.insert_reading(self.db_path, ts, temp_c)
        except Exception as e:
            if isinstance(e, shelly.ShellyError):
                error = str(e)
            else:
                error = "database error"
                log.exception("Could not store reading")
            self.failures += 1
            if self.failures == 1 or error != self.status["error"]:
                log.warning("Poll failed: %s (host %s)", error, self.host)
            self.status = {**self.status, "state": "error", "last_attempt": attempt_ts,
                           "error": error}
            return False
        if self.failures:
            log.info("Sensor recovered after %d failed poll(s)", self.failures)
        self.failures = 0
        self.status = {"state": "ok", "last_attempt": attempt_ts, "last_success": ts,
                       "error": None}
        return True

    def run(self):
        if not self.host:
            log.info("SHELLY_HOST is not set: collector idle (demo-only)")
        next_due = time.monotonic()
        while not self.stop.is_set():
            try:
                if self.poll_once() and db.snapshot_due(self.backups_dir):
                    log.info("Daily snapshot: %s", db.backup(self.db_path, self.backups_dir))
            except Exception:
                log.exception("Unexpected error in poll cycle")
            next_due += self.interval
            now = time.monotonic()
            if next_due < now:  # the host was suspended or a cycle overran
                next_due = now + self.interval
            self.stop.wait(next_due - now)


def start_guarded(target):
    """Run target in a thread; if it ever dies, take the whole process down.

    sys.exit would end only this thread and leave a healthy-looking web server
    with no collector, so Docker would never restart it.
    """
    def guard():
        try:
            target()
        except BaseException:
            log.critical("Collector thread died", exc_info=True)
            os._exit(1)

    thread = threading.Thread(target=guard, name="collector", daemon=True)
    thread.start()
    return thread
