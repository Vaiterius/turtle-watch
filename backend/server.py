"""Flask app: two read-only JSON endpoints and the built frontend.

The API never writes to a database and never contacts the Shelly.
"""

import logging
import re
import time

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import db

log = logging.getLogger("turtlewatch.server")
TZ_PATTERN = re.compile(r"-?\d{1,3}")
# Water-temperature health limits in °F, ascending: too cold below the first, caution
# below the second, acceptable overnight below the third, optimal through the fourth,
# above optimal through the fifth, too warm above it. Defaults for an adult red-eared slider.
DEFAULT_THRESHOLDS_F = (68.0, 70.0, 74.0, 79.0, 82.0)
EXCURSION_LOOKBACK = 7 * db.DAY
CONFIRM_READINGS = 2  # consecutive readings to start a sustained warning, and to clear one


def excursion(rows, thresholds_f, max_gap):
    """The current cooling excursion, or None.

    Only the newest gap-free run of readings counts: a gap (missing readings, sensor
    offline) ends any excursion, because nothing is known about the water meanwhile.
    A warning starts after CONFIRM_READINGS consecutive readings below the second
    threshold, or at once below the first, and clears after CONFIRM_READINGS
    consecutive readings at or above the second threshold.
    """
    cold, cool = thresholds_f[0], thresholds_f[1]
    first = len(rows) - 1
    while first > 0 and rows[first][0] - rows[first - 1][0] <= max_gap:
        first -= 1
    started = low_since = last_low = None
    lows = oks = 0
    for ts, temp_c in rows[max(first, 0):]:
        temp_f = round(temp_c * 9 / 5 + 32, 1)  # rated as displayed, like the dashboard
        if temp_f < cool:
            lows, oks, last_low = lows + 1, 0, ts
            low_since = low_since or ts
            if started is None and (lows >= CONFIRM_READINGS or temp_f < cold):
                started = low_since
        else:
            lows, oks, low_since = 0, oks + 1, None
            if started is not None and oks >= CONFIRM_READINGS:
                started = None
    if started is None:
        return None
    latest_f = round(rows[-1][1] * 9 / 5 + 32, 1)
    return {
        "start": started,
        "last_below": last_low,
        "level": "danger" if latest_f < cold else "caution",
        "recovering": latest_f >= cool,
    }


def create_app(data_dir, static_dir, get_sensor_status, poll_interval=300,
               thresholds_f=DEFAULT_THRESHOLDS_F):
    app = Flask(__name__, static_folder=None)
    paths = db.db_paths(data_dir)
    for path in paths.values():
        db.init_db(path)
    stale_after = max(600, 2 * poll_interval)
    raw_step = max(300, poll_interval)

    def mode_arg():
        mode = request.args.get("mode", "real")
        if mode not in paths:
            abort(400, "invalid mode")
        return mode

    @app.get("/api/status")
    def status():
        mode = mode_arg()
        reading = cold_spell = None
        row = db.latest(paths[mode])
        if row:
            rows = db.recent(paths[mode], row[0], EXCURSION_LOOKBACK)
            cold_spell = excursion(rows, thresholds_f, raw_step * 1.5)
            ts, temp_c = row
            # Demo time ends at the last demo reading, so demo is never stale.
            age = 0 if mode == "demo" else max(0, int(time.time()) - ts)
            reading = {"ts": ts, "temp_c": round(temp_c, 2), "age": age,
                       "stale": age > stale_after}
        return jsonify(mode=mode, reading=reading, sensor=get_sensor_status(),
                       thresholds_f=list(thresholds_f), excursion=cold_spell)

    @app.get("/api/history")
    def history():
        mode = mode_arg()
        range_key = request.args.get("range", "24h")
        if range_key not in db.RANGES:
            abort(400, "invalid range")
        tz = request.args.get("tz", "0")
        if not TZ_PATTERN.fullmatch(tz) or not -840 <= int(tz) <= 840:
            abort(400, "invalid tz")
        end = int(time.time())
        if mode == "demo":
            row = db.latest(paths[mode])
            end = row[0] if row else end
        result = db.history(paths[mode], range_key, int(tz), end, raw_step)
        return jsonify(mode=mode, range=range_key, **result)

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/<path:name>")
    def static_file(name):
        if name.startswith("api/"):
            abort(404)
        return send_from_directory(static_dir, name)

    @app.after_request
    def cache_headers(response):
        if request.path.startswith("/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.errorhandler(HTTPException)
    def http_error(e):
        message = e.description if e.code == 400 else e.name.lower()
        return jsonify(error=message), e.code

    @app.errorhandler(Exception)
    def unexpected_error(e):
        log.exception("Unhandled error for %s", request.path)
        return jsonify(error="internal error"), 500

    return app
