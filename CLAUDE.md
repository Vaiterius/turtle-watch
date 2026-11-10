# Turtlewatch

Self-hosted water-temperature monitor for an outdoor turtle tank. A Raspberry Pi 4 polls a Shelly Pill (DS18B20 probe) every five minutes, stores readings in SQLite, and serves a dashboard on the home LAN from one Docker container.

## Status

As of 2026-11-10: built and tested on the development machine, including the Docker image and an emulated ARM64 build. Not yet deployed to the Pi, and never tested against a real Shelly Pill. The application code is not committed to git.

## Commands

- Backend tests: `.venv/bin/python -m unittest discover -s backend` (the venv was made with `uv venv`; plain `python3 -m venv` is not installed on this machine).
- Frontend tests and build: `cd web && npm test && npm run build`.
- Run locally: `.venv/bin/python backend/main.py serve` (serves `web/dist` on :8000).
- Image: `docker compose up -d --build`.

## Source of truth

`docs/PLAN.md` holds the architecture and every design decision. Read it before changing anything. If the code and the plan disagree, raise it with the user.

## Decisions the user made

Do not revert these, even though the original brief said otherwise:

- Backend framework: Flask.
- Styling: Tailwind CSS.

## Invariants

- One container, one process, one collector. Never use Flask's development server, debug mode or reloader, or a multi-worker server; each starts a second collector.
- Never fabricate, zero-fill or interpolate a reading. A failed poll stores nothing, and gaps stay gaps.
- Real and demo data live in separate databases: `data/turtlewatch.db` and `data/demo.db`. Seeding touches only demo. The collector touches only real.
- The API is read-only, never takes a file path from a request, and never contacts the Shelly.
- The app only reads from the Shelly. It never changes device settings.
- Nothing about the real Pill is verified until it has been tested on the hardware. Never report otherwise.

## Dependencies

- Python: Flask and Waitress, pinned with their transitive set in `backend/requirements.txt`. Everything else is standard library: `sqlite3`, `urllib`, `argparse`, `logging`, `unittest`.
- Frontend: React, Vite, Chart.js, `tailwindcss` and `@tailwindcss/vite`.
- No Flask extensions, component libraries, chart wrappers, date libraries, state managers or test frameworks. Ask before adding anything.

## Conventions

- Timestamps are UTC epoch seconds. Temperatures are stored and served in Celsius and converted to Fahrenheit only in the frontend.
- Tailwind class names are complete static strings, never built by concatenation.
- Colours come from the theme tokens in `web/src/index.css`. The chart reads the same CSS variables.

## Git

Do not commit or push unless the user asks.
