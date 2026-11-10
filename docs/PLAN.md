# Turtlewatch: architecture and implementation plan

**Status:** approved and implemented on 2026-11-10. Verified on the development machine, including Docker and an emulated ARM64 build; not yet run on the Pi or against a real Pill.

Turtlewatch is a self-hosted water-temperature monitor for an outdoor turtle tank. A Shelly Pill with a waterproof DS18B20 probe sits on the home Wi-Fi. A Raspberry Pi 4 polls it every five minutes, stores the readings in SQLite, and serves a dashboard to a PC or phone on the same network. The sensor hardware has not arrived, so everything must be buildable and testable with simulated data.

One Raspberry Pi. One Docker container. One Python process. One React frontend. Local SQLite. Five-minute polling. Fully isolated demo mode.

## Fixed requirements

These come from the brief and are not open for redesign.

| Item | Value |
|---|---|
| Application name | Turtlewatch |
| Repository and Pi directory | `turtle-watch`, `~/turtle-watch` |
| Compose service | `turtlewatch` |
| Port | 8000 |
| Local URL | `http://turtlewatch.local:8000`, with an IP fallback |
| Production database | `data/turtlewatch.db` |
| Demo database | `data/demo.db` |
| Poll interval | 300 s, set by `POLL_INTERVAL_SECONDS` |
| Stale threshold | last successful reading older than 10 minutes |
| Units | stored in Celsius with UTC timestamps, displayed in Fahrenheit |
| Dashboard refresh | about every 30 s, and it must never trigger a Shelly poll |
| History ranges | 24H, 7D, 30D, 1Y, All |
| Out of scope | Home Assistant, cloud, public hosting, authentication, other automation |

Normal operation must not need an internet connection.

Priorities, in order: reliability, simplicity, performance, maintainability, design.

## Decisions that override the brief

Two were made by the user after the first plan and must not be reverted:

| Brief | Decision | Made by |
|---|---|---|
| Backend preferably standard library | **Flask** | User |
| Vanilla CSS, no CSS frameworks | **Tailwind CSS** | User |

The rest are departures I recommend. Each needs the user's approval along with the plan.

| Brief | Plan | Why |
|---|---|---|
| (follows from Flask) | **Waitress** as the WSGI server, a second Python dependency | Flask's own server is for development only; see section 1 |
| Separate reading ID, timestamp and index | `ts INTEGER PRIMARY KEY` is all three | One B-tree, half the writes, range scans need no extra index |
| Four endpoints | Two: `/api/status` and `/api/history` | Current and status change together; history and stats share one window |
| Health check as recovery | Health check is for visibility only | Compose never restarts an unhealthy container; recovery comes from the process exiting |
| Chart.js time axis | Linear axis on epoch seconds, labels via `Intl` | Avoids a date adapter and a date library |
| Backup command | Command plus an automatic daily snapshot, newest 14 kept | Unattended for years; this is an addition beyond the brief |
| Demo relative to the wall clock | Demo windows end at the last demo reading | The 24H view stays populated days after seeding |

## 1. Architecture and technology decisions

| Layer | Choice | Version today |
|---|---|---|
| Frontend | React with Vite | React 19.3, Vite 8.3 |
| Styling | Tailwind CSS through `@tailwindcss/vite` | 4.3 |
| Charts | Chart.js | 4.6 |
| Backend | Python with Flask | Python 3.14, Flask 3.1 |
| WSGI server | Waitress | 3.0 |
| Database | SQLite through the standard `sqlite3` module | |
| Sensor | Shelly local HTTP RPC through the standard `urllib` | |
| Packaging | Multi-stage Docker image, Docker Compose | |
| Host | Raspberry Pi OS, 64-bit | |

### Process model

Turtlewatch is one Python process:

- The main thread runs Waitress, which serves the Flask app on four worker threads.
- A background thread runs the collector loop that polls the Shelly.
- If the collector thread ever dies, a guard around its loop logs the error and calls `os._exit(1)`. A plain `sys.exit` would end only that thread and leave a healthy-looking web server with no collector.
- If Waitress fails, the main thread exits non-zero.
- Either way, Docker restarts the container.

### Why Waitress

The constraint is that one process owns exactly one collector.

- Flask's built-in server is ruled out by Flask's own documentation for production use.
- Flask's debug mode and reloader are ruled out because the reloader starts a second process, and with it a second collector.
- Multi-worker servers such as Gunicorn are ruled out for the same reason unless pinned to one worker and wired with hooks.
- Waitress is pure Python with no dependencies outside the standard library, runs as a single process with worker threads, and is listed in Flask's deployment documentation.

### What Flask is and is not used for

- Used for routing, query-string parsing, JSON responses, error handling and serving the built frontend.
- Not used: templates, sessions, cookies, blueprints or any Flask extension.
- Everything else stays on the standard library: `sqlite3`, `urllib`, `threading`, `argparse`, `logging`, `unittest`.

### Frontend dependencies

Runtime: `react`, `react-dom`, `chart.js`. Build: `vite`, `@vitejs/plugin-react`, `tailwindcss`, `@tailwindcss/vite`.

Not added: TypeScript, ESLint, `react-chartjs-2`, component libraries, state managers, date libraries or a JS test framework.

Tailwind is a build-time tool. It writes a static CSS file during `vite build`; nothing is loaded from a CDN, so offline operation holds.

## 2. Docker and Docker Compose design

One service, `turtlewatch`:

- **Basics:** `build: .`, `restart: unless-stopped`, port `${PORT:-8000}:8000`, volume `./data:/app/data`.
- **Privileges:** runs as `${PUID:-1000}:${PGID:-1000}` with `read_only: true`, `tmpfs: /tmp`, `cap_drop: [ALL]` and `no-new-privileges`. No host networking and no Docker socket.
- **Configuration:** read from `.env` with defaults in `compose.yaml`, so the app starts demo-only with no `.env` at all.
- **Logging:** `json-file` driver, three files of 1 MB.
- **Health check:** `wget` against `/api/status` every 60 s. That endpoint returns 200 while the Shelly is down, so a missing sensor never marks the container unhealthy.

Docker creates a missing bind-mount directory as root, which a non-root container cannot write to. Two measures cover that:

- `data/.gitkeep` is committed, so the directory exists and belongs to the user after a clone.
- At startup the app checks that `data/` is writable and, if not, exits with the exact `chown` command to run.

## 3. Production image and build strategy

**Stage 1, `node:24-alpine`:** copies `web/package.json` and `web/package-lock.json`, runs `npm ci`, copies the rest of `web/`, runs `vite build`.

**Stage 2, `python:3.14-alpine`:**

- Installs `tzdata`.
- Runs `pip install --no-cache-dir -r requirements.txt`.
- Copies `backend/*.py` (tests excluded) to `/app` and the built assets to `/app/static`.
- Sets `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`.
- Runs as `USER 1000:1000` with `CMD ["python", "main.py", "serve"]`.

The final image has Python, Flask and Waitress, and no Node.

**Architecture.** Both base images are multi-arch and the image builds natively on the Pi, so there is no registry and no cross-compiling. Checked on the package registries:

- The native build packages are Vite's bundler (Rolldown), Lightning CSS and Tailwind's engine. All three publish `linux-arm64-musl` binaries.
- The React plugin's peer packages are all optional and are not installed.
- MarkupSafe, the only compiled Python dependency, publishes a `musllinux` `aarch64` wheel for Python 3.14.

**Reproducibility.**

- `web/package-lock.json` locks the frontend. It is generated from a clean install so that it contains the ARM64 entries for every native build package.
- `backend/requirements.txt` pins Flask, Waitress and the full transitive set. It is generated once during implementation with `pip freeze` from a clean virtual environment and committed to the repository, not written by hand. The Docker build only installs from it.

**Size and speed.** The image measures 82.5 MB (the estimate was 70–80 MB). The first build on a Pi 4 is estimated at a few minutes. Both are estimates until measured.

**Assets.** Hashed files under `/assets` are served with long-lived immutable caching; `index.html` is served with `no-cache`. Gzip is skipped: the JavaScript bundle is an estimated 350–400 KB and Tailwind's output is a few kilobytes, which is fine on a LAN.

## 4. Shelly Pill communication and polling

### Verified against the official documentation

- RPC over HTTP accepts `GET /rpc/<Method>?…` or `POST /rpc/<Method>` with a JSON body of parameters. For the POST form the reply is documented as the bare result object.
- `Temperature.GetStatus` returns `{id, tC, tF, errors?}`. `tC` is `null` when no valid value could be obtained, and `errors` may contain `read` or `out_of_range`.
- Components are keyed `temperature:<id>`. `Shelly.GetComponents` lists a device's components with optional status, paged by `offset` and `total`.
- The Pill is model `S3SN-0U53X`, with 2.4 GHz Wi-Fi only. It reads DS18B20 probes in `onewire` mode, which is set with `Pill.SetConfig`. It supports up to five probes.

### Not documented, so unverified until the hardware arrives

- Which component ID the probe gets. An ID of 100 is documented only for the Sensor Add-on, which does not list the Pill as a supported device.
- Whether a probe appears automatically once the Pill is in `onewire` mode.
- Whether device authentication is off by default.
- What an RPC error looks like over HTTP.

### Design

- **RPC form.** Every call is `POST /rpc/<Method>` with a JSON body of parameters. This is the form whose bare-result reply is documented, and it carries the array parameter that `Shelly.GetComponents` needs without URL quoting.
- **Component selection.** If `SHELLY_TEMPERATURE_ID` is set, each poll calls `Temperature.GetStatus` for that ID. Otherwise it pages through `Shelly.GetComponents` with status included and takes the single top-level key of the exact form `temperature:<int>`. Finding none, or more than one, is an error that names what was found. Nothing is cached, so this works for ID 0, 100 or 200+.
- **Read-only.** The app never changes device settings. Putting the Pill in `onewire` mode is a one-time manual step documented in the README.
- **Validation.** A reading is stored only if `tC` is a finite number between −55 and 125 °C and `errors` is absent or empty. Timeouts, refused connections, non-200 replies, invalid JSON and `null` values store nothing.
- **Timing.** 5 s timeout, up to three attempts per cycle spaced 5 s apart, on a fixed-rate schedule driven by the monotonic clock. The first poll runs at startup.
- **Timestamps.** Taken from the Pi's clock at the moment of a successful read, as UTC epoch seconds.
- **No overlap.** One sequential loop cannot overlap itself.
- **No duplicates.** An exclusive `flock` on `data/.collector.lock` is taken before the collector starts; a second process fails to get it and exits.
- **No sensor configured.** With `SHELLY_HOST` empty the collector idles and reports `unconfigured`. This is the demo-only mode.
- **Address.** `SHELLY_HOST` must be an IP address reserved in the router, because a container cannot resolve `.local` names.

### Collector status

The collector publishes an immutable status snapshot that the API reads:

- `state`: `starting`, `ok`, `error` or `unconfigured`.
- `last_attempt` and `last_success`: epoch seconds or `null`.
- `error`: a short message from a fixed vocabulary written by this code, such as `timeout`, `unreachable`, `invalid response`, `sensor error: read`, `no temperature component` or `multiple temperature components: 100, 101`. Raw exception text is logged but never sent to the browser.

### Logging

One line at startup with the configuration. One line per state change: first failure with its reason, and recovery with the number of missed polls. No line per successful poll and no access log. Waitress does not log requests by default.

## 5. SQLite schema and historical aggregation

```sql
CREATE TABLE IF NOT EXISTS readings (
  ts     INTEGER PRIMARY KEY,  -- UTC epoch seconds; also the row ID and the only index
  temp_c REAL NOT NULL CHECK (temp_c BETWEEN -55 AND 125)
) STRICT;
```

Both database files use this schema and are created at startup if missing.

- **Journal.** WAL, with SQLite's default full synchronous setting and a 5 s busy timeout. Readers and the writer never block each other, and each reading is durable once committed.
- **Connections.** None are shared between threads. Every operation opens its own connection and closes it. API requests open theirs read-only.
- **Queries.** All SQL is parameterised. No value from a request is ever interpolated into SQL or into a file path.

### Aggregation

One query serves every range:

```sql
SELECT ((ts + :tz) / :bucket) * :bucket - :tz AS t,
       AVG(temp_c), MIN(temp_c), MAX(temp_c), COUNT(*)
FROM readings
WHERE ts >= :start AND ts <= :end
GROUP BY t ORDER BY t;
```

Raw readings use a bucket of 1 second inside the query, so each row is its own group and keeps its exact timestamp. The API reports this case as `bucket: 0`.

| Range | Window | Bucket | Points, at most |
|---|---|---|---|
| 24H | 24 hours | raw readings | 288 |
| 7D | 7 days | 30 minutes | 336 |
| 30D | 30 days | 1 hour | 720 |
| 1Y | 365 days | 1 day | 366 |
| All | first reading to now | 1 day up to two years of data, then 1 week | about 730 |

- **What a "day" means.** Midnight to midnight in the browser's time zone.
- **The `tz` convention.** `tz` is the browser's UTC offset in minutes, positive east of UTC, so US Pacific standard time is `-480`. JavaScript's `getTimezoneOffset()` has the opposite sign, so the frontend sends its negation. The server validates an integer between −840 and 840 and multiplies it by 60; `:tz` in the query is in seconds.
- **Statistics.** A separate `COUNT`, `MIN`, `MAX`, `AVG` query over the same raw rows and the same window. They are never derived from chart buckets.
- **Gaps.** Only buckets that contain readings are returned. No bucket is ever filled with a zero, a null or an interpolated value.
- **Expected step.** The response carries the expected spacing between points: the bucket size, or `max(300, poll interval)` for raw readings. The chart uses it to break the line.

### Size and speed

Estimated growth is 2–2.5 MB per year, so indefinite retention needs no pruning. Estimated query time on the Pi is about 0.1 s for one year and under 1 s for five years of the All range. Both will be measured.

## 6. Demo mode and strict data isolation

Two database files is the simplest reliable isolation, so the brief's layout stands: `data/turtlewatch.db` for real readings and `data/demo.db` for simulated ones.

### Isolation rules

- `mode` selects a path from a fixed two-entry map, and does nothing else.
- The seeder only ever receives the demo path. There is no command-line option for a path.
- The collector only ever receives the production path.
- The API opens both read-only, so it cannot write to either.
- Mode lives in the query string and the browser's `localStorage`. The server holds no mode state, so switching modes cannot affect collection.

### Demo time

Demo windows end at the last demo reading, not at the wall clock. The 24H view stays full however long ago the data was seeded. Demo readings are never marked stale; the status shows Demo.

### Generator

The simulated temperature is a deterministic function of the timestamp with a fixed seed, built from:

- a seasonal swing peaking in late July;
- a daily cycle peaking in the late afternoon, local time;
- slow drift over several days;
- small per-reading jitter;
- occasional unusual events, such as a cold snap or a heat spike with gradual recovery.

Missing data is also deterministic: isolated dropped readings, occasional outages of 20 minutes to 6 hours, and a rare outage of more than a day.

The local time used for the daily cycle comes from the container's `TZ` setting.

### Seeding

Timestamps sit on a five-minute grid aligned to the epoch. Rows go in with `INSERT OR IGNORE` through `executemany` in one transaction. Re-running a seed therefore never duplicates a timestamp and tops the data up to now.

```bash
python main.py seed --days 30
python main.py seed --days 365
python main.py seed --clear
python main.py seed --status
```

All four run through `docker compose exec turtlewatch …` in the existing container. `--clear` deletes all demo rows. `--status` prints the row count, first and last timestamp and file size.

## 7. Backend API

Flask serves two JSON endpoints and the built frontend from the same origin.

```
GET /api/status?mode=real|demo

{"mode": "real",
 "reading": {"ts": 1762732500, "temp_c": 24.31, "age": 142, "stale": false},
 "sensor":  {"state": "ok", "last_attempt": 1762732500,
             "last_success": 1762732500, "error": null}}
```

```
GET /api/history?mode=real|demo&range=24h|7d|30d|1y|all&tz=<minutes>

{"mode": "demo", "range": "7d",
 "start": 1762128000, "end": 1762732800,
 "bucket": 1800, "step": 1800,
 "points": [[1762128000, 24.12, 23.9, 24.4, 6], …],
 "stats": {"count": 1987, "min_c": 21.4, "max_c": 26.9, "avg_c": 24.03}}
```

- **Points.** Each is `[ts, avg, min, max, count]`. `bucket` is 0 for raw readings.
- **Empty data.** `reading` is `null`, `points` is empty and `stats.count` is 0. That is a 200, not an error.
- **Sensor block.** Always describes the real collector, in both modes.
- **Age and staleness.** `age` is the seconds from the reading to the end of the window. That end is now in Real mode and the last demo reading in Demo mode, so in Demo `age` is always 0 and `stale` is always false. A real reading is stale when its age exceeds `max(600, 2 × poll interval)` seconds. The server computes both, so a wrong clock on the phone cannot change them.
- **`tz`.** UTC offset in minutes, positive east of UTC. See section 5.
- **Units.** The API returns Celsius as stored. The frontend converts to Fahrenheit.
- **Defaults.** `mode` defaults to `real`, `range` to `24h` and `tz` to 0.
- **Errors.** Flask returns HTML error pages by default, so explicit handlers turn every error into `{"error": "…"}`: 400 for an invalid `mode`, `range` or `tz`; 404 for unknown paths; 405 for other methods; 500 for anything unexpected, with the detail logged and not returned.
- **Caching.** API responses are sent with `Cache-Control: no-store`.
- **Static files.** Flask serves the build directory, and Werkzeug's safe path joining rejects traversal.
- **No write, admin or seeding endpoints.** The API never contacts the Shelly.

## 8. Frontend design and component structure

### Layout

The visual direction is "Instrument", chosen by the user on 2026-11-10: square edges, condensed numerals, and colour used only where it carries meaning. The layout is landscape, also chosen by the user on 2026-11-10, so the whole dashboard fits one screen without scrolling: verified at 1440×900 and 1440×780, including the tallest state (sustained cooling). The page is up to about 1520 px wide.

- **Header**, full width: the wordmark "🐢 turtlewatch" (the emoji character itself, lowercase name, the emoji lifted so the turtle centres on the word), the °F/°C switch and the light/dark switch.
- **Left column**, 320–440 px: the connection status, a large reading with tabular numerals sized to the window height, the time of the last reading, the health rating, and the health scale drawn from `thresholds_f` with a marker at the reading.
- **Right column**: the chart, with 24H, 7D, 30D, 1Y and All tabs, filling the remaining height.
- **Second row**: turtle facts under the left column, and Min, Avg and Max under the chart. The rule above the facts is level with the chart's bottom edge.
- **Status strip**, full width along the bottom: whether the sensor is reachable and why not, the last successful reading and the last poll attempt, in four equal columns, then the "Show demo data" checkbox. The checkbox renders even while status is loading or the API is unreachable, so Demo can always be left.
- **Below 1024 px wide** it is one column in reading order: reading and health, chart, statistics, facts, status.

### Status pill

| Condition, checked in this order | Pill |
|---|---|
| Demo mode | Demo |
| The collector's last attempt failed | Offline |
| No reading stored | No data |
| The reading is stale | Stale |
| Otherwise | Online |

Offline is checked before No data, so a sensor that fails on its first day shows the failure and its reason, not a wait message. A stale reading is dimmed and labelled as not current.

The status is a square indicator on a dark chip in both themes: neon green for Online, neon red for Offline, amber for Stale and Demo, grey for No data. The neon colours sit on the chip, never on the light background, where they would be unreadable. The Sensor row in System status uses the same chip.

### Temperature health

Added after the build at the user's request. The latest reading is rated against five ascending °F limits from `TEMP_THRESHOLDS_F` (default `68,70,74,79,82`, for an adult red-eared slider). The server validates them and returns them as `thresholds_f` in `/api/status`.

| Water | Status | Colour |
|---|---|---|
| 74–79 °F | Optimal | green |
| 70–73.9 °F | Acceptable overnight | yellow-green |
| 68–69.9 °F | Caution: too cool | yellow |
| below 68 °F | Too cold | red, shown as a tinted panel |
| 79.1–82 °F | Above optimal | yellow |
| above 82 °F | Too warm | red, shown as a tinted panel |

- The same limits apply in Real and Demo, day and night, and never change with the season.
- The reading is classified as displayed, rounded to 0.1 °F.
- Health is separate from connection status. When the reading is stale or the sensor is failing, the health badge says "at the last reading".
- The graph shades the optimal band, and the y-axis always includes it.
- **Sustained cooling.** `/api/status` adds `excursion`: `null`, or `{start, last_below, level, recovering}`. It is computed from the newest gap-free run of readings in the last 7 days of that mode's database, by `server.excursion()`. It starts after 2 consecutive readings below the second limit, or at once below the first, and clears after 2 consecutive readings at or above the second. A gap longer than 1.5 poll intervals ends it. No new tables.
- **Units.** A °F/°C display switch, stored in `localStorage`. Storage stays °C and the limits stay °F.
- **Demo data.** The generator simulates a heated tank averaging about 76.5 °F. Each 30-day block gets 1–2 multi-day episodes from a fixed rotation: heat wave, storm or cold spell, broken heater, broken sensor. Seeding regenerates its window (delete, then insert) and keeps the final two days calm, so Demo opens on a healthy reading.

### Tailwind

- **Setup.** `tailwindcss` and `@tailwindcss/vite`, the plugin added in `vite.config.js`, and `@import "tailwindcss";` in `web/src/index.css`. The documented Vite setup needs no `tailwind.config.js` and no PostCSS config.
- **Tokens.** Colours and the fonts are defined once in an `@theme` block in `index.css`: background, surface, hairline, text, muted, accent, warning, the four health tones and their lighter scale fills, the status chip and its four indicator colours. The default palette is removed so only these exist. Every colour is a 6-digit hex, because the chart parses them.
- **Light and dark.** The light colours are the base tokens. `:root[data-theme="dark"]` redefines them with the dark palette; no `dark:` variants are used. The theme follows the operating system, so it turns dark when the computer does at sunset, and is light when the system states no preference. The header switch overrides it and is saved in `localStorage`, but only while it differs from the system: the override is dropped when the system switches while the page is open, when the page loads and the system already agrees with it, or when the user picks the system's own theme. The page then follows the system again. An inline script in `index.html` sets `data-theme` before the first paint.
- **Fonts.** Barlow (400, 500, 600) for text and Barlow Condensed (500, 600) for numerals and headings, latin subset, self-hosted as woff2 under `web/src/fonts` with their SIL Open Font License. Vite bundles them into `assets/`, so the dashboard still needs no internet access.
- **Class names.** Always complete static strings. Tailwind scans source as plain text and cannot see names built by concatenation. Computed widths and positions, such as the health scale's, are inline styles.
- **Hand-written CSS.** Limited to `index.css`: the import, the font faces, the tokens, and the theme and demo overrides below.

### One set of colours for the UI and the chart

Chart.js draws on a canvas and cannot use utility classes. Tailwind emits its theme tokens as CSS custom properties on `:root`, so the chart reads the same variables with `getComputedStyle`. The tokens are declared static so that every one is emitted.

In Demo mode the root element carries `data-mode="demo"`, and one CSS rule under that selector redefines the accent variable as the warning amber. Utilities pick it up through the cascade, and the chart re-reads the variables when the mode or theme changes. The Tailwind docs do not describe this exact pattern, so it is checked in the first build; the fallback is switching between two explicit accent classes.

### Colour and motion

Light theme: a pale grey-blue ground with ink text, and the ink as the accent. Dark theme: a green-black ground with pale text, and the pale text as the accent. Amber marks stale and demo in both. Every state word is colour-coded wherever it appears, always next to a word or icon so colour is never the only signal. All text colours meet 4.5:1 contrast. Chart animations are off. The only transitions are short colour fades.

### Chart

- **Modules.** Only the line controller, line and point elements, linear scale, filler and tooltip are registered.
- **Series.** A line for the average. On aggregated ranges, a faint band between minimum and maximum.
- **Axis.** Linear on epoch seconds, spanning the response's `start` to `end` so missing data at either edge shows as empty space. Ticks sit on local-time boundaries suited to the range, labelled with `Intl.DateTimeFormat`.
- **Gaps.** The line breaks wherever two points are more than 1.5 expected steps apart. A reading isolated between two gaps is drawn as a dot so it stays visible.
- **Tooltips.** Time, temperature and, on aggregated ranges, the minimum to maximum and the number of readings.
- **Demo.** The line takes the amber accent, and a persistent "Demo data, simulated" label shows.

### States

- **Loading:** placeholders in place of numbers.
- **Empty:** a message naming the next step. In Demo it is the seed command. In Real it is "set `SHELLY_HOST`" when no sensor is configured, the failure reason when the sensor is unreachable, or "waiting for the first reading" at startup.
- **API unreachable:** a banner, with the last data kept and dimmed, retrying on the normal schedule.
- **Offline or stale:** described under Status pill.

### Data flow

- Status is fetched every 30 s. Fetching pauses while the tab is hidden and resumes at once when it is shown.
- History is fetched only when the mode or range changes, or when status reports a newer reading.
- A response that arrives after the mode or range has changed is discarded.
- The mode, unit and theme override are saved in `localStorage`. Real is the default mode.

### Files

- `App.jsx`: state, fetching, layout and the small presentational pieces.
- `HistoryChart.jsx`: the Chart.js lifecycle.
- `util.js`: fetch helper, Fahrenheit conversion, time formatting and axis ticks.
- `index.css`: Tailwind import and tokens.

## 9. Persistent storage and backups

- **Location.** Everything persistent lives in `./data` on the host, mounted at `/app/data`. The container's root filesystem is read-only, so nothing can land in the container layer by mistake. Rebuilding, recreating or updating the container never touches it.
- **Backup command.** `python main.py backup` uses SQLite's online backup API to write a consistent snapshot to `data/backups/turtlewatch-YYYYMMDD-HHMMSS.db` while the app keeps running. Copying the live file is never used.
- **Automatic snapshots.** After a successful poll, if the newest snapshot is more than a day old, the collector takes one. The newest 14 are kept and older ones deleted.
- **Off-device copy.** Snapshots sit on the same SD card as the database, so the README includes an `scp` line to copy them to the PC. Docker is not a backup.
- **Restore.** Stop the container, copy a snapshot over `turtlewatch.db`, remove any `-wal` and `-shm` files, start the container.

## 10. Raspberry Pi deployment and configuration

### Host setup

- **Operating system.** Raspberry Pi OS 64-bit. Confirm with `uname -m` (`aarch64`) and `dpkg --print-architecture` (`arm64`). Both matter, because a 64-bit kernel can run a 32-bit system.
- **Hostname.** Set it to `turtlewatch` when flashing with Raspberry Pi Imager, or later with `sudo raspi-config` under System Options, Hostname, followed by a reboot. The preinstalled `avahi-daemon` then advertises `turtlewatch.local`. Check it with `systemctl is-active avahi-daemon`.
- **Fallback.** `hostname -I` on the Pi gives the address for `http://<ip>:8000`. Some Android versions do not resolve `.local`.
- **Router.** Reserve DHCP addresses for both the Pi and the Pill.
- **Clock.** Leave time synchronisation on. See section 14.
- **Boot.** `sudo systemctl enable docker` makes Docker start at boot, and `restart: unless-stopped` brings the container up with it.

### Configuration

`.env` is git-ignored; `.env.example` is committed.

```env
# Shelly Pill address. Reserve it in the router. Leave empty to run demo-only.
SHELLY_HOST=
# Only needed if the Pill reports more than one temperature component.
SHELLY_TEMPERATURE_ID=
POLL_INTERVAL_SECONDS=300
PORT=8000
# Used for log timestamps and the demo data's daily cycle.
TZ=America/Los_Angeles
# Only if the Pi user is not UID/GID 1000.
# PUID=1000
# PGID=1000
```

These are example values, not verified network settings.

### Commands

```bash
# first install, on the Pi
uname -m && dpkg --print-architecture        # want aarch64 and arm64
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
sudo systemctl enable docker                 # start Docker at boot
# log out and back in so the docker group applies
git clone <your-remote> ~/turtle-watch && cd ~/turtle-watch
cp .env.example .env && nano .env
docker compose up -d --build

# start and stop
docker compose up -d
docker compose stop

# rebuild and update
git pull && docker compose up -d --build

# logs
docker compose logs -f --tail=100

# health
docker compose ps
curl -s localhost:8000/api/status

# demo data
docker compose exec turtlewatch python main.py seed --days 30
docker compose exec turtlewatch python main.py seed --days 365
docker compose exec turtlewatch python main.py seed --clear
docker compose exec turtlewatch python main.py seed --status

# back up
docker compose exec turtlewatch python main.py backup

# restore
docker compose stop
cp data/backups/<snapshot>.db data/turtlewatch.db
rm -f data/turtlewatch.db-wal data/turtlewatch.db-shm
docker compose start

# roll back a bad deployment
git checkout <previous-commit> && docker compose up -d --build
```

A failed build leaves the old container running, because Compose builds before it recreates. Rollback never touches data, because the schema has no migrations.

### One-time Pill setup

Join the Pill to the Wi-Fi, put it in `onewire` mode from its web interface or with `Pill.SetConfig`, then check that `http://<pill-ip>/rpc/Shelly.GetComponents` lists a `temperature:<id>` component. The exact steps are confirmed when the hardware arrives.

## 11. Reliability considerations

| Event | Behaviour |
|---|---|
| Shelly unreachable, timeout, invalid JSON, `tC: null` or sensor error | Nothing stored; the gap stays a gap; status shows Offline |
| Network returns | The next cycle succeeds and logs the recovery |
| Error inside one poll cycle | Caught, logged, reported as the sensor error; the loop continues |
| Collector thread dies | A guard calls `os._exit(1)`; Docker restarts the container; first poll runs at startup |
| Waitress fails | Same |
| Pi reboot or power loss | Docker starts the container at boot; commits are atomic, so at most the in-flight reading is lost |
| `SIGTERM` or `SIGINT` | A handler stops Waitress the way Ctrl-C does, then stops the collector; exit within about 5 s |
| Second collector started | Fails the file lock and exits |
| Dashboard read during a write | Neither blocks, under WAL |
| `data/` not writable | Exits at startup with the fix |
| Database damaged | Errors surface in the log and the dashboard; restore from a snapshot |
| Logging | One line per state change; 3 MB cap |
| Database growth | About 2–2.5 MB per year, estimated |
| Bad deployment | Old container keeps running on build failure; git rollback otherwise |

Waitress's documentation does not describe signal handling, and the app runs as PID 1, where unhandled signals are ignored. The app therefore installs its own handlers, and a test proves a clean exit.

## 12. Testing and acceptance criteria

### Automated tests

Backend tests use `unittest` and Flask's test client. Frontend tests use `node --test` on the pure functions in `util.js`. No test dependencies are added.

- Aggregation per bucket, including a partial first bucket.
- The `tz` sign: with `tz=-480`, a reading at 23:30 Pacific falls in that local day's bucket, and the bucket starts at 08:00 UTC.
- Statistics equal to a direct calculation over the raw rows, in a case where an unweighted average of buckets would differ.
- No filled gaps: empty buckets are absent from the output.
- Bucket selection for all five ranges, including the switch from daily to weekly.
- Shelly parsing: valid readings, `tC: null`, `errors` present, out of range, wrong types, and a bare `temperature` nested inside another component being ignored.
- Shelly discovery for IDs 0, 100 and 200, across a multi-page listing, with none and with several components, and with a configured ID.
- Failure paths against a local mock Shelly: timeout, refused connection, HTTP 500 and garbage. Each stores nothing, and a later success stores a row.
- Seeding: deterministic, idempotent, on the five-minute grid, containing gaps, within a plausible range.
- Isolation: the production file's hash is unchanged by seed and clear, and each mode returns only its own data.
- The API cannot write: a write through its connection fails.
- API validation: invalid `mode`, `range` and `tz` return 400; unknown paths 404; other methods 405; path traversal 404; all errors are JSON.
- Staleness at the threshold.
- The collector lock: a second acquisition fails.
- Backup: a snapshot taken during writes passes `PRAGMA integrity_check` and has the expected rows.
- Whole process: start `main.py serve` on a spare port, wait for `/api/status`, send `SIGTERM`, expect exit code 0 within 10 s.
- Collector death: a subprocess whose collector loop raises exits with code 1.
- Performance: a year of demo data (about 105,000 rows) queried for 1Y, timed.
- Frontend: Fahrenheit conversion, time formatting, tick generation, and the `tz` value being the negation of `getTimezoneOffset()`.

### Acceptance

| Item | Verified where |
|---|---|
| Demo works without hardware | Tests and local run |
| All five ranges render | Tests for the data; browser check on a local run |
| A year of demo data loads efficiently | Timed test here; Pi timing on the Pi |
| Aggregation and statistics accurate | Tests |
| Gaps remain visible | Tests for the data; browser check |
| Demo and real never mix | Tests |
| Clearing demo leaves production untouched | Tests |
| Switching modes does not interrupt polling | Test with the mock Shelly |
| Network failures create no readings | Tests |
| Stale clearly indicated | Test for the flag; browser check with a scratch database |
| Data survives container recreation | Docker here if enabled, otherwise the Pi |
| Permissions as non-root | Docker here if enabled, otherwise the Pi |
| Restarts after a crash | Done on the development machine two ways: `docker compose exec turtlewatch kill -TERM 1`, and `kill -9` on the host PID from a privileged `--pid=host` helper container. On the Pi, `sudo kill -9` on the container's host PID |
| `docker compose stop` exits with code 0 inside the grace period, not 137 | Docker here if enabled, otherwise the Pi |
| Starts after a Pi reboot | Pi only |
| Reachable from another LAN device | Pi only |
| No Node in production | Docker here if enabled; true by construction of stage 2 |
| Image works on the Pi's architecture | Emulated `linux/arm64` build and smoke run here if enabled; the Pi for certain |
| Lockfile has `linux-arm64` entries for every native build package | Checked in the lockfile here; proven by the emulated build |
| Clean shutdown on `SIGTERM` outside a container | Test |
| Demo accent override reaches utilities and the chart | Browser check on the first build |
| Theme switch and system theme reach utilities and the chart | Browser check |
| Shelly integration | Mocked here; real sensor only |

Docker became available on the development machine before the build, so every row marked "Docker here if enabled" was checked there. The backend tests were also run inside the production image. Rows marked for the Pi or the real sensor remain untested.

The crash check never uses `docker stop` or `docker kill`. Docker treats those as a manual stop and skips the restart policy, so the process is made to exit by other means.

Hardware integration is never reported as verified before it has been tested on the physical Pill.

## 13. Minimal repository structure

```
turtle-watch/
├── backend/
│   ├── main.py               # CLI: serve, seed, backup; signals; wiring
│   ├── collector.py          # poll loop, status snapshot, daily snapshot
│   ├── shelly.py             # RPC calls, discovery, validation
│   ├── db.py                 # schema, connections, history and stats, backup
│   ├── demo.py               # generator and seeding
│   ├── server.py             # Flask app: API, errors, static files
│   ├── requirements.txt      # Flask, Waitress and transitive pins
│   └── test_turtlewatch.py
├── web/
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.js        # React and Tailwind plugins; dev proxy for /api
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── HistoryChart.jsx
│       ├── util.js
│       ├── util.test.js
│       ├── index.css         # Tailwind import, font faces, light and dark theme tokens
│       └── fonts/            # Barlow and Barlow Condensed woff2, with OFL.txt
├── data/
│   └── .gitkeep
├── docs/
│   └── PLAN.md
├── Dockerfile
├── compose.yaml
├── .env.example
├── .dockerignore
├── .gitignore
├── CLAUDE.md
└── README.md                 # deployment and maintenance
```

- **`.gitignore`:** `data/*` except `.gitkeep`, `.env`, `.venv/`, `web/node_modules/`, `web/dist/`, `__pycache__/`.
- **`.dockerignore`:** `.git`, `data/`, `.env`, `.venv/`, `web/node_modules/`, `web/dist/`, `docs/`, `backend/test_*.py`, `__pycache__/`.

### Local development

No Docker and no hardware are needed:

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python backend/main.py seed --days 365
.venv/bin/python backend/main.py serve      # API on :8000; collector idle without SHELLY_HOST
cd web && npm install && npm run dev        # Vite on :5173, proxying /api to :8000
```

The same `serve` command runs in development and production. Flask's debug mode is never used.

## 14. Technical uncertainties and tradeoffs

- **Probe ID and binding.** Discovery removes the need to know the ID, but whether the probe shows up at all in `onewire` mode needs the real device.
- **Clock.** The Pi 4 has no real-time clock. After a power cut without internet, timestamps are wrong until NTP synchronises. Collection works offline; correct time after a power loss does not. An RTC module fixes it.
- **Daily buckets.** They use the browser's current UTC offset, so they sit one hour off local midnight for the part of the year on the other side of a daylight-saving change.
- **All-time queries.** These are full scans. If they become slow after many years, a rollup table is the upgrade. It is not built now.
- **No reverse proxy.** Flask's documentation recommends one in front of Waitress. That advice targets internet-facing sites. This app is for a trusted LAN only and must never be port-forwarded.
- **Browser minimums.** Tailwind 4 needs Chrome 111, Safari 16.4 or Firefox 128, or newer. An iPhone must be on iOS 16.4 or later.
- **SD card.** It is the weakest part of the system. Local snapshots do not survive a dead card, so the off-device copy matters.
- **Pi architecture.** Not yet checked on the actual Pi.
- **Python dependencies.** Flask brings six transitive packages. They are pinned, and updates are a deliberate rebuild.
- **Not built:** Shelly authentication, alerts, multiple probes, gzip, automatic repair of a damaged database.

## Build and hand-off

After explicit approval, the whole application is implemented in one continuous pass, with no milestones and no approval stops between features. The pass covers the repository structure, backend, frontend, Dockerfile, Compose file, tests, a production frontend build, the Docker image where Docker is available, an end-to-end check of the demo workflow, and the README.

The pass ends with a report covering:

- what was implemented;
- the tests run and their results;
- the exact Docker Compose deployment commands;
- how to open the dashboard;
- how to seed and clear demo data;
- how to read logs and restart;
- how to back up SQLite;
- the remaining hardware-specific configuration;
- anything that could not be tested or completed.

## Sources

Shelly:

- [The Pill by Shelly, API device page](https://shelly-api-docs.shelly.cloud/gen2/Devices/Gen3/ThePill/)
- [Pill component](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Pill)
- [Temperature component](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Temperature)
- [Shelly service: GetStatus, GetComponents, GetDeviceInfo](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Shelly)
- [RPC channels](https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels)
- [Authentication](https://shelly-api-docs.shelly.cloud/gen2/General/Authentication)
- [Sensor Add-on](https://shelly-api-docs.shelly.cloud/gen2/Addons/ShellySensorAddon) (does not list the Pill, so none of its methods are used)
- [The Pill, knowledge base](https://kb.shelly.cloud/knowledge-base/the-pill-by-shelly)

Flask and Waitress:

- [Flask: deploying to production](https://flask.palletsprojects.com/en/stable/deploying/)
- [Flask: Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/)
- [Flask: handling application errors](https://flask.palletsprojects.com/en/stable/errorhandling/)
- [Waitress: usage](https://docs.pylonsproject.org/projects/waitress/en/stable/usage.html)
- [Waitress: arguments](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html)
- [Waitress: logging](https://docs.pylonsproject.org/projects/waitress/en/stable/logging.html)

Tailwind CSS:

- [Installing with Vite](https://tailwindcss.com/docs/installation/using-vite)
- [Compatibility](https://tailwindcss.com/docs/compatibility)
- [Theme variables](https://tailwindcss.com/docs/theme)
- [Dark mode](https://tailwindcss.com/docs/dark-mode)
- [Detecting classes in source files](https://tailwindcss.com/docs/detecting-classes-in-source-files)

Raspberry Pi:

- [Raspberry Pi Avahi and mDNS](https://scivision.dev/raspberry-pi-wifi-avahi)

Not confirmed:

- Two Shelly community threads returned 403, so a claim that Pill component IDs start at 200 is unverified.
- The hostname and `.local` behaviour come from a search summary, not from a Pi.
- Docker's treatment of `docker stop` and `docker kill` as manual stops is from memory. The crash checks avoided both and Docker restarted the container each time.
