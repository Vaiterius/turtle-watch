# Turtlewatch

Water-temperature monitoring for an outdoor turtle tank. A Raspberry Pi polls a Shelly Pill with a DS18B20 probe every five minutes, stores the readings in SQLite, and serves a dashboard on your home network. One Docker container, no cloud, no accounts.

The design and the reasons behind it are in [docs/PLAN.md](docs/PLAN.md).

> The Shelly integration has only been tested against mocked responses. See [Hardware bring-up](#hardware-bring-up) for what to check when the Pill arrives.

## Install on the Raspberry Pi

Use 64-bit Raspberry Pi OS and set the hostname to `turtlewatch` (in Raspberry Pi Imager, or `sudo raspi-config` → System Options → Hostname, then reboot).

```bash
uname -m && dpkg --print-architecture        # want: aarch64 and arm64
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
sudo systemctl enable docker                 # start Docker at boot
# log out and back in so the docker group applies

git clone <your-remote> ~/turtle-watch && cd ~/turtle-watch
cp .env.example .env && nano .env            # set SHELLY_HOST and TZ
docker compose up -d --build
```

Open `http://turtlewatch.local:8000` from a PC or phone on the same network. If `.local` does not resolve (some Android phones), run `hostname -I` on the Pi and use `http://<that-ip>:8000`.

In your router, reserve a fixed address for the Pi and for the Pill. `SHELLY_HOST` must be an IP address; a container cannot resolve `.local` names.

If your Pi user is not UID 1000 (check with `id`), set `PUID` and `PGID` in `.env`.

## Everyday commands

Run these in `~/turtle-watch`.

| Task | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose stop` |
| Update | `git pull && docker compose up -d --build` |
| Logs | `docker compose logs -f --tail=100` |
| Health | `docker compose ps`, or `curl -s localhost:8000/api/status` |
| Restart | `docker compose restart` |
| Back up now | `docker compose exec turtlewatch python main.py backup` |

The container restarts by itself after a crash or a reboot. A missing sensor does not make it unhealthy; the dashboard shows Offline and collection resumes when the sensor returns.

## Demo data

The dashboard has a Real/Demo switch. Demo data lives in its own database, `data/demo.db`, and never touches real readings.

```bash
docker compose exec turtlewatch python main.py seed --days 30
docker compose exec turtlewatch python main.py seed --days 365
docker compose exec turtlewatch python main.py seed --status
docker compose exec turtlewatch python main.py seed --clear
```

The demo is a heated tank around 76–77 °F with one or two multi-day episodes a month: heat waves, storms or cold spells, a broken heater (cold for days until a replacement), and now and then a broken sensor (no readings for days). Seeding is repeatable: running it again regenerates the chosen window up to now, with the same episodes on the same dates, and the newest two days are always calm.

Set `TZ` in `.env` before you seed. The simulated daily cycle peaks in the late afternoon of that time zone; if you change `TZ` later, clear the demo data and seed again.

## Backups and restore

Everything persistent is in `./data`. Rebuilding or recreating the container never touches it.

- A snapshot is written to `data/backups/` once a day, and the newest 14 are kept.
- Those snapshots sit on the same SD card as the database. Copy one off the Pi now and then, from your PC:

  ```bash
  scp "<pi-user>@turtlewatch.local:turtle-watch/data/backups/*.db" .
  ```

To restore:

```bash
docker compose stop
cp data/backups/<snapshot>.db data/turtlewatch.db
rm -f data/turtlewatch.db-wal data/turtlewatch.db-shm
docker compose start
```

Never copy `turtlewatch.db` while the app is running; use the backup command.

## Rolling back an update

A failed build leaves the old container running. If a new version misbehaves:

```bash
git log --oneline                     # find the previous commit
git checkout <previous-commit> && docker compose up -d --build
```

Your data is untouched; the database format has no migrations. `git switch main` returns to the latest code.

## Hardware bring-up

None of this has been tested on a real Pill yet.

1. Join the Pill to your 2.4 GHz Wi-Fi and reserve its IP address.
2. Put it in 1-Wire mode from its web page, or with:

   ```bash
   curl -X POST -d '{"config":{"mode":"onewire"}}' http://<pill-ip>/rpc/Pill.SetConfig
   ```

3. Check that it reports a temperature component:

   ```bash
   curl -X POST -d '{"include":["status"]}' http://<pill-ip>/rpc/Shelly.GetComponents
   ```

   Look for a key like `temperature:100` with a `tC` value. The number after the colon is the component ID; Shelly's documentation does not say what it is on the Pill.
4. Set `SHELLY_HOST=<pill-ip>` in `.env`, then `docker compose up -d`.
5. Watch `docker compose logs -f`. Success looks like `Shelly OK: temperature:<id> = 24.40 C`.

If the log says `multiple temperature components: …`, set `SHELLY_TEMPERATURE_ID` to the one in the tank. If it says `no temperature component`, the Pill is not in 1-Wire mode or has not detected the probe. If it says `auth required`, turn off authentication on the Pill; Turtlewatch does not support it.

Leave the Pi's time synchronisation on. The Pi has no clock battery, so after a power cut its timestamps are wrong until it reaches a time server.

## Configuration

Set in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `SHELLY_HOST` | empty | Pill IP address. Empty runs demo-only. |
| `SHELLY_TEMPERATURE_ID` | empty | Only if the Pill reports several temperature components. |
| `POLL_INTERVAL_SECONDS` | `300` | Seconds between readings. |
| `PORT` | `8000` | Port on the Pi. |
| `TZ` | `UTC` | Time zone for logs and the demo data's daily cycle. |
| `TEMP_THRESHOLDS_F` | `68,70,74,79,82` | Health limits in °F; see below. |
| `PUID`, `PGID` | `1000` | Owner of `./data` on the Pi. |

## Temperature health

The dashboard rates the current reading against five limits, the same in Real and Demo, day and night:

| Water | Status |
|---|---|
| 74–79 °F | Optimal |
| 70–73.9 °F | Acceptable overnight: fine for brief cooling, not as a sustained temperature |
| 68–69.9 °F | Caution: too cool |
| below 68 °F | Too cold |
| 79.1–82 °F | Above optimal |
| above 82 °F | Too warm |

These are conservative defaults for routine care of an adult red-eared slider, not medical limits. Change them with `TEMP_THRESHOLDS_F` in `.env`, then `docker compose up -d`. The graph shades the optimal band. Health is separate from connection status: if the reading is stale or the sensor is failing, the rating says it describes the last reading.

**Sustained cooling.** After two consecutive readings below 70 °F (the second limit) the rating gets a yellow panel and a line such as "Below 70 °F for about 25 minutes". A reading below 68 °F (the first limit) turns it red at once. It clears after two consecutive readings at or above 70 °F, so one warm reading doesn't make it flicker. A gap in readings (sensor offline, dropped readings) ends the excursion: nothing is assumed about the water while no readings arrive, and the count starts again from the next reading. The same rules apply to Real and Demo, each from its own database.

**Units.** The °F/°C switch in the header changes only the display, and the browser remembers it. Readings are stored in °C and the limits are set in °F.

## Development

No Docker or hardware needed. Python 3.13 or newer and Node 22 or newer.

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python backend/main.py seed --days 365
.venv/bin/python backend/main.py serve      # API on :8000
cd web && npm install && npm run dev        # dashboard on :5173
```

Tests:

```bash
.venv/bin/python -m unittest discover -s backend
cd web && npm test
```

Turtlewatch is for a trusted home network only. It has no login; never forward its port to the internet.
