import { useEffect, useState } from 'react'
import HistoryChart from './HistoryChart.jsx'
import {
  HEALTH, degreeRange, excursionText, fetchJson, formatAge, formatDateTime, formatTime, fromF, healthDetail, healthFor,
  toF, tzOffsetMinutes,
} from './util.js'

const REFRESH_MS = 30_000
const MODE_KEY = 'turtlewatch.mode'
const UNIT_KEY = 'turtlewatch.unit'
const RANGES = [['24h', '24H'], ['7d', '7D'], ['30d', '30D'], ['1y', '1Y'], ['all', 'All']]
const SEED_COMMAND = 'docker compose exec turtlewatch python main.py seed --days 30'

// Browser storage can be unavailable (private browsing); the choice then just isn't remembered.
const loadSetting = (key, allowed, fallback) => {
  try {
    const value = localStorage.getItem(key)
    return allowed.includes(value) ? value : fallback
  } catch {
    return fallback
  }
}
const saveSetting = (key, value) => {
  try {
    localStorage.setItem(key, value)
  } catch {
    // not remembered
  }
}

// Complete class names only: Tailwind cannot see names built at runtime.
const PILL = {
  Demo: 'text-warn',
  Online: 'text-ok',
  Stale: 'text-warn',
  Offline: 'text-danger',
  'No data': 'text-muted',
}

const TONE_TEXT = {
  good: 'text-good',
  fair: 'text-fair',
  caution: 'text-caution',
  danger: 'text-danger',
}

const ICONS = {
  check: <><circle cx="12" cy="12" r="9" /><path d="m8 12.5 2.5 2.5L16 9.5" /></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />,
  down: <path d="M12 5v14M6 13l6 6 6-6" />,
  up: <path d="M12 19V5M6 11l6-6 6 6" />,
  alert: <><path d="M12 3.5 2.5 20h19L12 3.5Z" /><path d="M12 10v4.5M12 17.5v.01" /></>,
}

function Icon({ name, className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" className={className} aria-hidden="true">
      {ICONS[name]}
    </svg>
  )
}

const PANEL = {
  danger: 'mt-5 flex max-w-xl items-start gap-3 rounded-lg border border-danger/50 bg-danger/10 px-3.5 py-3',
  caution: 'mt-5 flex max-w-xl items-start gap-3 rounded-lg border border-caution/35 bg-caution/5 px-3.5 py-3',
  none: 'mt-5 flex max-w-xl items-start gap-3',
}

// Temperature health, shown apart from the connection pill. `current` is false when the
// reading is stale or the sensor is failing, so the badge says which reading it describes.
// `excursion` is the server's sustained-cooling state; it adds a panel and a duration line.
function HealthBadge({ tempF, thresholds, current, excursion, unit }) {
  const key = healthFor(tempF, thresholds)
  const { label, tone, icon } = HEALTH[key]
  const panel = excursion?.level ?? (tone === 'danger' ? 'danger' : 'none')
  return (
    <div role={panel === 'danger' ? 'alert' : 'status'} className={PANEL[panel]}>
      <Icon name={icon} className={`mt-px h-5 w-5 shrink-0 ${TONE_TEXT[tone]}`} />
      <div className="text-sm">
        <div className={`font-semibold ${TONE_TEXT[tone]}`}>
          {label}
          {!current && <span className="font-normal text-muted"> · at the last reading</span>}
        </div>
        <div className="mt-0.5 text-muted">{healthDetail(key, thresholds, unit)}</div>
        {excursion && (
          <div className={`mt-1.5 font-medium ${excursion.level === 'danger' ? 'text-danger' : 'text-caution'}`}>
            Sustained cooling · {excursionText(excursion, thresholds, unit)}
          </div>
        )}
      </div>
    </div>
  )
}

function pillFor(mode, status) {
  if (mode === 'demo') return 'Demo'
  if (status.sensor.state === 'error') return 'Offline'
  if (!status.reading) return 'No data'
  return status.reading.stale ? 'Stale' : 'Online'
}

function emptyMessage(mode, sensor) {
  if (mode === 'demo') return { text: 'No demo data yet. Generate some on the Pi:', code: SEED_COMMAND }
  if (sensor.state === 'unconfigured') {
    return { text: 'No sensor configured. Set SHELLY_HOST in .env and restart, or switch to Demo.' }
  }
  if (sensor.state === 'error') return { text: `The last sensor poll failed: ${sensor.error}.` }
  return { text: 'Waiting for the first reading.' }
}

function Segmented({ options, value, onChange, label }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={
            value === key
              ? 'rounded-md bg-line px-3 py-1.5 text-sm font-medium text-text'
              : 'rounded-md px-3 py-1.5 text-sm text-muted transition-colors hover:text-text'
          }
        >
          {text}
        </button>
      ))}
    </div>
  )
}

function Hero({ mode, status, unit }) {
  if (!status) {
    return (
      <section className="py-10" aria-busy="true">
        <div className="text-7xl font-light text-line sm:text-8xl">––.–</div>
        <p className="mt-3 text-sm text-muted">Loading…</p>
      </section>
    )
  }
  const { reading, sensor } = status
  const pill = pillFor(mode, status)
  const notCurrent = mode === 'real' && reading?.stale
  const empty = reading ? null : emptyMessage(mode, sensor)
  return (
    <section className="py-10">
      <div className={`flex items-center gap-2 text-sm font-medium ${PILL[pill]}`}>
        <span className="h-2 w-2 rounded-full bg-current" aria-hidden="true" />
        {pill}
        {mode === 'demo' && <span className="font-normal text-muted">· simulated data, not your tank</span>}
      </div>
      <div className={`mt-2 flex items-start tabular-nums ${notCurrent ? 'text-muted' : 'text-text'}`}>
        <span className="text-7xl font-light tracking-tight sm:text-8xl">
          {reading ? fromF(toF(reading.temp_c), unit).toFixed(1) : '––.–'}
        </span>
        <span className="mt-2 ml-1 text-2xl text-muted sm:text-3xl">°{unit}</span>
      </div>
      {reading && mode === 'real' && (
        <p className="mt-3 text-sm text-muted">
          Last reading {formatTime(reading.ts)} · {formatAge(reading.age)}
          {notCurrent && <span className="text-warn"> · not current</span>}
          {!notCurrent && sensor.state === 'error' && (
            <span className="text-danger"> · last poll failed: {sensor.error}</span>
          )}
        </p>
      )}
      {reading && mode === 'demo' && (
        <p className="mt-3 text-sm text-muted">Simulated reading · {formatDateTime(reading.ts)}</p>
      )}
      {reading && (
        <HealthBadge
          tempF={toF(reading.temp_c)}
          thresholds={status.thresholds_f}
          current={mode === 'demo' || (!reading.stale && sensor.state !== 'error')}
          excursion={status.excursion}
          unit={unit}
        />
      )}
      {empty && (
        <p className="mt-3 text-sm text-muted">
          {empty.text}
          {empty.code && (
            <code className="mt-2 block overflow-x-auto rounded-md border border-line bg-surface px-3 py-2 text-xs text-text">
              {empty.code}
            </code>
          )}
        </p>
      )}
    </section>
  )
}

function Stat({ label, celsius, unit }) {
  return (
    <div className="px-4 py-3 first:pl-0">
      <div className="text-xs tracking-wide text-muted uppercase">{label}</div>
      <div className="mt-1 text-xl tabular-nums">
        {celsius == null ? '–' : fromF(toF(celsius), unit).toFixed(1)}
        <span className="ml-0.5 text-sm text-muted">°{unit}</span>
      </div>
    </div>
  )
}

function SystemStatus({ mode, status }) {
  const { sensor, reading } = status
  const sensorText = {
    ok: 'Reachable',
    error: `Poll failed (${sensor.error})`,
    starting: 'Starting, first poll in progress',
    unconfigured: 'Not configured',
  }[sensor.state]
  const lastSuccess = mode === 'real' ? reading?.ts ?? sensor.last_success : sensor.last_success
  const rows = [
    ['Data source', mode === 'demo' ? 'Demo (simulated)' : 'Real sensor'],
    [mode === 'demo' ? 'Real sensor' : 'Sensor', sensorText],
    ['Last successful reading', lastSuccess ? formatDateTime(lastSuccess) : '–'],
    ['Last poll attempt', sensor.last_attempt ? formatDateTime(sensor.last_attempt) : '–'],
  ]
  return (
    <footer className="mt-10 border-t border-line pt-5">
      <h2 className="text-xs tracking-wide text-muted uppercase">System status</h2>
      <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4">
            <dt className="text-muted">{label}</dt>
            <dd className={`text-right ${label.endsWith('ensor') && sensor.state === 'error' ? 'text-danger' : ''}`}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      {mode === 'demo' && sensor.state !== 'unconfigured' && (
        <p className="mt-3 text-xs text-muted">The real sensor keeps being polled in the background while you view demo data.</p>
      )}
    </footer>
  )
}

export default function App() {
  const [mode, setMode] = useState(() => loadSetting(MODE_KEY, ['real', 'demo'], 'real'))
  const [unit, setUnit] = useState(() => loadSetting(UNIT_KEY, ['F', 'C'], 'F'))
  const [range, setRange] = useState('24h')
  const [status, setStatus] = useState(null)
  const [history, setHistory] = useState(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.mode = mode
    saveSetting(MODE_KEY, mode)
  }, [mode])
  useEffect(() => saveSetting(UNIT_KEY, unit), [unit])

  // Status every 30 s, paused while the tab is hidden. This never polls the Shelly.
  useEffect(() => {
    let live = true
    const load = async () => {
      try {
        const next = await fetchJson(`/api/status?mode=${mode}`)
        if (live) {
          setStatus(next)
          setUnreachable(false)
        }
      } catch {
        if (live) setUnreachable(true)
      }
    }
    const onVisible = () => document.visibilityState === 'visible' && load()
    setStatus(null)
    setHistory(null)
    load()
    const timer = setInterval(() => document.visibilityState === 'visible' && load(), REFRESH_MS)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      live = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [mode])

  // History only when the mode or range changes or a newer reading has arrived.
  const loaded = status?.mode === mode
  const latestTs = status?.reading?.ts ?? 0
  useEffect(() => {
    if (!loaded) return
    const controller = new AbortController()
    fetchJson(`/api/history?mode=${mode}&range=${range}&tz=${tzOffsetMinutes()}`, controller.signal)
      .then((next) => {
        setHistory(next)
        setUnreachable(false)
      })
      .catch((error) => error.name !== 'AbortError' && setUnreachable(true))
    return () => controller.abort()
  }, [mode, range, loaded, latestTs])

  // Keep the previous range on screen until the new one arrives, so switching ranges never blanks the chart.
  const current = history?.mode === mode ? history : null
  const switching = current && current.range !== range
  const stats = current?.stats

  return (
    <main className="mx-auto max-w-4xl px-4 pt-5 pb-12 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-base font-semibold tracking-tight">
          <span className="text-accent">●</span> Turtlewatch
        </h1>
        <div className="flex gap-2">
          <Segmented label="Temperature unit" options={[['F', '°F'], ['C', '°C']]} value={unit} onChange={setUnit} />
          <Segmented label="Data source" options={[['real', 'Real'], ['demo', 'Demo']]} value={mode} onChange={setMode} />
        </div>
      </header>

      {mode === 'demo' && (
        <p role="status" className="mt-4 rounded-lg border border-warn px-3 py-2 text-sm text-warn">
          Demo data, simulated. Switch to Real to see your tank.
        </p>
      )}
      {unreachable && (
        <p role="alert" className="mt-4 rounded-lg border border-danger px-3 py-2 text-sm text-danger">
          Can't reach Turtlewatch. Retrying; anything shown below may be out of date.
        </p>
      )}

      <div className={unreachable ? 'opacity-60' : ''}>
        <Hero mode={mode} status={status} unit={unit} />

        <section className="rounded-xl border border-line bg-surface p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm text-muted">
              Water temperature{current?.bucket > 0 && ' · average with min to max band'}
              {status && (
                <span className="mt-1 flex items-center gap-1.5 text-xs">
                  <span className="inline-block h-2.5 w-4 rounded-sm border border-good/40 bg-good/15" aria-hidden="true" />
                  Optimal {degreeRange(status.thresholds_f[2], status.thresholds_f[3], unit)}
                </span>
              )}
            </h2>
            <Segmented label="Time range" options={RANGES} value={range} onChange={setRange} />
          </div>
          <div className={`mt-4 transition-opacity ${switching ? 'opacity-60' : ''}`} aria-busy={switching || undefined}>
            {!current ? (
              <div className="flex h-64 items-center justify-center text-sm text-muted sm:h-80">Loading…</div>
            ) : current.points.length === 0 ? (
              <div className="flex h-64 items-center justify-center text-sm text-muted sm:h-80">
                No readings in this range.
              </div>
            ) : (
              <HistoryChart history={current} mode={mode} thresholds={status.thresholds_f} tempUnit={unit} />
            )}
          </div>
          <div className={`mt-3 grid grid-cols-3 divide-x divide-line border-t border-line pt-1 transition-opacity ${switching ? 'opacity-60' : ''}`}>
            <Stat label="Min" celsius={stats?.min_c} unit={unit} />
            <Stat label="Avg" celsius={stats?.avg_c} unit={unit} />
            <Stat label="Max" celsius={stats?.max_c} unit={unit} />
          </div>
          {stats && (
            <p className="mt-1 text-xs text-muted">
              From {stats.count.toLocaleString()} stored reading{stats.count === 1 ? '' : 's'} in this range.
            </p>
          )}
        </section>

        {status && <SystemStatus mode={mode} status={status} />}
      </div>
    </main>
  )
}
