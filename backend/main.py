"""Turtlewatch command line: serve, seed, backup."""

import argparse
import fcntl
import logging
import os
import signal
import sys
import time
from pathlib import Path

import db
import demo

log = logging.getLogger("turtlewatch")
HERE = Path(__file__).resolve().parent


def env_int(name, default, minimum):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        sys.exit(f"{name} must be a whole number, got {raw!r}")
    if value < minimum:
        sys.exit(f"{name} must be at least {minimum}, got {value}")
    return value


def env_thresholds():
    """TEMP_THRESHOLDS_F: five ascending °F values, e.g. 68,70,74,79,82."""
    from server import DEFAULT_THRESHOLDS_F
    raw = os.environ.get("TEMP_THRESHOLDS_F", "").strip()
    if not raw:
        return DEFAULT_THRESHOLDS_F
    try:
        values = tuple(float(v) for v in raw.split(","))
    except ValueError:
        values = ()
    if len(values) != 5 or any(b <= a for a, b in zip(values, values[1:])):
        sys.exit(f"TEMP_THRESHOLDS_F must be five ascending numbers in °F "
                 f"(for example 68,70,74,79,82), got {raw!r}")
    return values


def data_dir():
    return Path(os.environ.get("DATA_DIR", "data")).resolve()


def interrupt(signum, frame):
    # Same path as Ctrl-C, which is how Waitress is documented to stop.
    raise KeyboardInterrupt


def serve():
    import waitress
    from collector import Collector, start_guarded
    from server import create_app

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    data = data_dir()
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write-test"
        probe.touch()
        probe.unlink()
    except OSError:
        log.error("%s is not writable by uid %d. On the host run: sudo chown -R %d:%d data",
                  data, os.getuid(), os.getuid(), os.getgid())
        return 1

    lock = open(data / ".collector.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("Another Turtlewatch collector already uses %s. Exiting.", data)
        return 1

    host = os.environ.get("SHELLY_HOST", "").strip()
    interval = env_int("POLL_INTERVAL_SECONDS", 300, 10)
    component_id = env_int("SHELLY_TEMPERATURE_ID", None, 0)
    port = env_int("PORT", 8000, 1)
    thresholds = env_thresholds()
    static_dir = next((d for d in (HERE / "static", HERE.parent / "web" / "dist") if d.is_dir()),
                      HERE / "static")

    paths = db.db_paths(data)
    collector = Collector(paths["real"], data / "backups", host, component_id, interval)
    app = create_app(data, static_dir, lambda: collector.status, interval, thresholds)

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    log.info("Turtlewatch starting: port %d, sensor %s, poll every %d s, data %s, "
             "thresholds %s °F", port, host or "not configured", interval, data,
             ",".join(f"{t:g}" for t in thresholds))
    thread = start_guarded(collector.run)
    try:
        waitress.serve(app, host="0.0.0.0", port=port, threads=4)
    except KeyboardInterrupt:
        pass
    collector.stop.set()
    thread.join(timeout=20)
    log.info("Turtlewatch stopped")
    return 0


def fmt_time(ts):
    return "-" if ts is None else time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(ts))


def seed(args):
    path = db.db_paths(data_dir())["demo"]  # the only database this command can reach
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.clear:
        print(f"Deleted {demo.clear(path)} demo readings from {path}")
    elif args.days is not None:
        if args.days < 1:
            sys.exit("--days must be at least 1")
        started = time.perf_counter()
        added = demo.seed(path, args.days)
        print(f"Added {added} demo readings covering {args.days} days "
              f"in {time.perf_counter() - started:.1f} s")
    info = demo.status(path)
    print(f"{path}: {info['count']} readings, {fmt_time(info['first'])} to "
          f"{fmt_time(info['last'])}, {info['bytes'] / 1024:.0f} KB")
    return 0


def backup():
    data = data_dir()
    print(f"Snapshot written: {db.backup(db.db_paths(data)['real'], data / 'backups')}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Turtlewatch")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the web server and the collector")
    seeder = commands.add_parser("seed", help="manage simulated demo data")
    action = seeder.add_mutually_exclusive_group(required=True)
    action.add_argument("--days", type=int, help="generate this many days up to now")
    action.add_argument("--clear", action="store_true", help="delete all demo data")
    action.add_argument("--status", action="store_true", help="show what demo data exists")
    commands.add_parser("backup", help="write a consistent snapshot of the real database")
    args = parser.parse_args()
    if args.command == "serve":
        return serve()
    if args.command == "seed":
        return seed(args)
    return backup()


if __name__ == "__main__":
    sys.exit(main())
