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


def create_app(data_dir, static_dir, get_sensor_status, poll_interval=300):
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
        reading = None
        row = db.latest(paths[mode])
        if row:
            ts, temp_c = row
            # Demo time ends at the last demo reading, so demo is never stale.
            age = 0 if mode == "demo" else max(0, int(time.time()) - ts)
            reading = {"ts": ts, "temp_c": round(temp_c, 2), "age": age,
                       "stale": age > stale_after}
        return jsonify(mode=mode, reading=reading, sensor=get_sensor_status())

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
