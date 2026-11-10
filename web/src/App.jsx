import { useEffect, useState } from 'react'
import HistoryChart from './HistoryChart.jsx'
import TurtleFact from './TurtleFact.jsx'
import {
  HEALTH, degreeRange, degrees, excursionText, fetchJson, formatAge, formatDateTime, formatTime, fromF, healthDetail,
  healthFor, healthScale, short, toF, tzOffsetMinutes,
} from './util.js'

const REFRESH_MS = 30_000
const MODE_KEY = 'turtlewatch.mode'
const UNIT_KEY = 'turtlewatch.unit'
const THEME_KEY = 'turtlewatch.theme' // also read by the inline script in index.html
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
const forgetSetting = (key) => {
  try {
    localStorage.removeItem(key)
  } catch {
    // nothing stored
  }
}

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')
// Set on the root at once, not in an effect: child effects run first, and the chart reads the tokens there.
const applyTheme = (theme) => {
  document.documentElement.dataset.theme = theme
}

// Complete class names only: Tailwind cannot see names built at runtime.
const PILL = {
  Demo: 'stale',
  Online: 'online',
  Stale: 'stale',
  Offline: 'offline',
  'No data': 'idle',
}

const CHIP = {
  online: 'text-online',
  stale: 'text-stale',
  offline: 'text-offline',
  idle: 'text-idle',
}

const TONE_TEXT = {
  good: 'text-good',
  fair: 'text-fair',
  caution: 'text-caution',
  danger: 'text-danger',
}

const TONE_BAND = {
  good: 'border-r-2 border-bg bg-good-fill last:border-r-0',
  fair: 'border-r-2 border-bg bg-fair-fill last:border-r-0',
  caution: 'border-r-2 border-bg bg-caution-fill last:border-r-0',
  danger: 'border-r-2 border-bg bg-danger-fill last:border-r-0',
}

const ICONS = {
  check: <><circle cx="12" cy="12" r="9" /><path d="m8 12.5 2.5 2.5L16 9.5" /></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></>,
  down: <path d="M12 5v14M6 13l6 6 6-6" />,
  up: <path d="M12 19V5M6 11l6-6 6 6" />,
  alert: <><path d="M12 3.5 2.5 20h19L12 3.5Z" /><path d="M12 10v4.5M12 17.5v.01" /></>,
}

function Icon({ name, className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round"
      strokeLinejoin="round" className={className} aria-hidden="true">
      {ICONS[name]}
    </svg>
  )
}

// Connection state: a neon indicator on a dark chip, readable on both themes.
const CHIP_SIZE = {
  large: ['gap-2 px-2.5 py-1', 'h-2.5 w-2.5'],
  small: ['gap-[7px] px-2 py-0.5 text-sm', 'h-2 w-2'],
}

function StatusChip({ tone, size = 'large', children }) {
  const [box, square] = CHIP_SIZE[size]
  return (
    <span className={`inline-flex items-center bg-chip font-semibold ${box} ${CHIP[tone]}`}>
      <span className={`shrink-0 bg-current ${square}`} aria-hidden="true" />
      {children}
    </span>
  )
}

const PANEL = {
  danger: 'flex items-start gap-3 border-l-4 border-danger bg-danger-fill/30 px-4 py-3',
  caution: 'flex items-start gap-3 border-l-4 border-caution bg-caution-fill/30 px-4 py-3',
  none: 'flex items-start gap-3 pb-1',
}

// Temperature health, shown apart from the connection status. `current` is false when the
// reading is stale or the sensor is failing, so the badge says which reading it describes.
// `excursion` is the server's sustained-cooling state; it adds a panel and a duration line.
function HealthBadge({ tempF, thresholds, current, excursion, unit }) {
  const key = healthFor(tempF, thresholds)
  const { label, tone, icon } = HEALTH[key]
  const panel = excursion?.level ?? (tone === 'danger' ? 'danger' : 'none')
  return (
    <div role={panel === 'danger' ? 'alert' : 'status'} className={PANEL[panel]}>
      <Icon name={icon} className={`mt-0.5 h-7 w-7 shrink-0 ${TONE_TEXT[tone]}`} />
      <div className="min-w-0">
        <div className={`font-display text-[32px] leading-none font-semibold ${TONE_TEXT[tone]}`}>{label}</div>
        {!current && <div className="mt-1.5 font-semibold text-warn">At the last reading</div>}
        <div className="mt-1.5 text-muted">{healthDetail(key, thresholds, unit)}</div>
        {excursion && (
          <div className={`mt-1.5 font-semibold ${excursion.level === 'danger' ? 'text-danger' : 'text-caution'}`}>
            Sustained cooling: {excursionText(excursion, thresholds, unit)}
          </div>
        )}
      </div>
    </div>
  )
}

// Where the reading sits between too cold and too warm, drawn from the server's limits.
function HealthScale({ tempF, thresholds, unit }) {
  const { bands, ticks, marker } = healthScale(thresholds, tempF)
  const left = { left: `${marker}%` }
  return (
    <div role="img" aria-label={`Health scale, reading ${degrees(tempF, unit)}`} className="relative pt-6">
      {ticks.map(({ f, at }) => (
        <div key={f} className="absolute top-0 -translate-x-1/2 text-[13px] text-muted tabular-nums" style={{ left: `${at}%` }}>
          {short(f, unit)}
        </div>
      ))}
      <div className="flex h-4">
        {bands.map(({ key, width }) => <div key={key} className={TONE_BAND[HEALTH[key].tone]} style={{ width: `${width}%` }} />)}
      </div>
      <div className="mt-1.5 flex font-display text-sm leading-tight font-medium">
        {bands.map(({ key, width }) => (
          <div key={key} className={`pr-1 ${TONE_TEXT[HEALTH[key].tone]}`} style={{ width: `${width}%` }}>
            {HEALTH[key].scale}
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="absolute top-[18px] h-7 w-[3px] -translate-x-1/2 bg-text" style={left} />
      <div aria-hidden="true"
        className="absolute top-2.5 -translate-x-1/2 border-x-[7px] border-t-[9px] border-x-transparent border-t-text" style={left} />
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
    return { text: 'No sensor configured. Set SHELLY_HOST in .env and restart, or tick Show demo data below.' }
  }
  if (sensor.state === 'error') return { text: `The last sensor poll failed: ${sensor.error}.` }
  return { text: 'Waiting for the first reading.' }
}

const FOCUS = 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text'

// Square ink toggle. An option with an icon shows only the icon and names itself to screen readers.
function Segmented({ options, value, onChange, label }) {
  return (
    <div role="group" aria-label={label} className="flex border-[1.5px] border-text">
      {options.map(([key, text, icon]) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          aria-label={icon ? text : undefined}
          title={icon ? text : undefined}
          onClick={() => onChange(key)}
          className={`flex min-h-11 min-w-11 items-center justify-center px-3 ${FOCUS} ${
            value === key ? 'bg-text font-semibold text-bg' : 'font-medium transition-colors hover:bg-line'
          }`}
        >
          {icon ? <Icon name={icon} className="h-5 w-5" /> : text}
        </button>
      ))}
    </div>
  )
}

function Tabs({ options, value, onChange, label }) {
  return (
    <div role="group" aria-label={label} className="flex gap-1">
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={`min-h-11 border-b-[3px] px-2.5 ${FOCUS} ${
            value === key ? 'border-text font-semibold' : 'border-transparent font-medium text-muted transition-colors hover:text-text'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}


const READING = 'text-[clamp(88px,17vh,168px)] leading-[0.9] tracking-[-0.01em]'

// The left column: connection, the reading, its health and the scale.
function Hero({ mode, status, unit }) {
  if (!status) {
    return (
      <div aria-busy="true">
        <div className={`font-display font-medium text-line ${READING}`}>––.–</div>
        <p className="mt-3 text-muted">Loading…</p>
      </div>
    )
  }
  const { reading, sensor } = status
  const pill = pillFor(mode, status)
  const notCurrent = mode === 'real' && reading?.stale
  const empty = reading ? null : emptyMessage(mode, sensor)
  const tempF = reading && toF(reading.temp_c)
  return (
    <>
      <div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusChip tone={PILL[pill]}>{pill}</StatusChip>
          {mode === 'demo' && <span className="text-muted">simulated data, not your tank</span>}
        </div>
        <div className={`mt-2.5 flex items-start font-display font-medium tabular-nums ${notCurrent || !reading ? 'text-muted' : 'text-text'}`}>
          <span className={READING}>{reading ? fromF(tempF, unit).toFixed(1) : '––.–'}</span>
          <span className="mt-[0.25em] ml-1.5 text-[clamp(32px,4.6vh,46px)] leading-none text-muted">°{unit}</span>
        </div>
        {reading && mode === 'real' && (
          <p className="mt-2.5 text-muted">
            Last reading {formatTime(reading.ts)}, {formatAge(reading.age)}.
            {notCurrent && <span className="font-semibold text-warn"> Not current.</span>}
            {!notCurrent && sensor.state === 'error' && (
              <span className="font-semibold text-danger"> Last poll failed: {sensor.error}.</span>
            )}
          </p>
        )}
        {reading && mode === 'demo' && (
          <p className="mt-2.5 text-muted">Simulated reading, {formatDateTime(reading.ts)}</p>
        )}
        {empty && (
          <p className="mt-2.5 text-muted">
            {empty.text}
            {empty.code && (
              <code className="mt-2 block overflow-x-auto border border-line bg-surface px-3 py-2 text-sm text-text">
                {empty.code}
              </code>
            )}
          </p>
        )}
      </div>
      {reading && (
        <HealthBadge
          tempF={tempF}
          thresholds={status.thresholds_f}
          current={mode === 'demo' || (!reading.stale && sensor.state !== 'error')}
          excursion={status.excursion}
          unit={unit}
        />
      )}
      {reading && <HealthScale tempF={tempF} thresholds={status.thresholds_f} unit={unit} />}
    </>
  )
}

function Stat({ label, celsius, unit }) {
  return (
    <div className="border-l-2 border-text pl-3">
      <div className="text-sm text-muted">{label}</div>
      <div className="font-display text-3xl leading-tight font-medium tabular-nums sm:text-[34px]">
        {celsius == null ? '–' : fromF(toF(celsius), unit).toFixed(1)}
        <span className="text-lg text-muted"> °{unit}</span>
      </div>
    </div>
  )
}

const SENSOR = {
  ok: ['online', () => 'Reachable'],
  error: ['offline', (sensor) => `Poll failed (${sensor.error})`],
  starting: ['stale', () => 'Starting, first poll in progress'],
  unconfigured: ['idle', () => 'Not configured'],
}

const ITEM = 'min-w-0 border-l border-line pl-4'
const VALUE = 'mt-1 flex min-h-[26px] items-center font-medium'

// One strip along the bottom. The demo checkbox renders without status too, so Demo can be left while
// loading or unreachable.
function SystemStatus({ mode, status, onModeChange }) {
  const sensor = status?.sensor
  const lastSuccess = mode === 'real' ? status?.reading?.ts ?? sensor?.last_success : sensor?.last_success
  const [tone, sensorText] = sensor ? SENSOR[sensor.state] : []
  return (
    <footer className="grid items-center gap-x-10 gap-y-3.5 border-t-2 border-text pt-3.5 lg:grid-cols-[auto_minmax(0,1fr)_auto]">
      <h2 className="font-display text-lg leading-tight font-semibold">System status</h2>
      {status ? (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
          <div className={ITEM}>
            <dt className="text-[13px] text-muted">Data source</dt>
            <dd className={`${VALUE} ${mode === 'demo' ? 'text-warn' : ''}`}>{mode === 'demo' ? 'Demo (simulated)' : 'Real sensor'}</dd>
          </div>
          <div className={ITEM}>
            <dt className="text-[13px] text-muted">{mode === 'demo' ? 'Real sensor' : 'Sensor'}</dt>
            <dd className={VALUE}><StatusChip tone={tone} size="small">{sensorText(sensor)}</StatusChip></dd>
          </div>
          <div className={ITEM}>
            <dt className="text-[13px] text-muted">Last successful reading</dt>
            <dd className={VALUE}>{lastSuccess ? formatDateTime(lastSuccess) : '–'}</dd>
          </div>
          <div className={ITEM}>
            <dt className="text-[13px] text-muted">Last poll attempt</dt>
            <dd className={VALUE}>{sensor.last_attempt ? formatDateTime(sensor.last_attempt) : '–'}</dd>
          </div>
        </dl>
      ) : (
        <div />
      )}
      <label className="flex min-h-11 w-fit cursor-pointer items-center gap-2.5">
        <input
          type="checkbox"
          checked={mode === 'demo'}
          onChange={(event) => onModeChange(event.target.checked ? 'demo' : 'real')}
          className="h-[18px] w-[18px] cursor-pointer accent-text"
        />
        Show demo data
      </label>
    </footer>
  )
}

export default function App() {
  const [mode, setMode] = useState(() => loadSetting(MODE_KEY, ['real', 'demo'], 'real'))
  const [unit, setUnit] = useState(() => loadSetting(UNIT_KEY, ['F', 'C'], 'F'))
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? 'light')
  const [range, setRange] = useState('24h')
  const [status, setStatus] = useState(null)
  const [history, setHistory] = useState(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.mode = mode
    saveSetting(MODE_KEY, mode)
  }, [mode])
  useEffect(() => saveSetting(UNIT_KEY, unit), [unit])

  // A choice is kept only while it differs from the system; choosing the system's own theme hands control back.
  const chooseTheme = (next) => {
    applyTheme(next)
    if (next === (darkQuery.matches ? 'dark' : 'light')) forgetSetting(THEME_KEY)
    else saveSetting(THEME_KEY, next)
    setTheme(next)
  }
  // When the system switches, at sunset say, it wins over a saved choice, as the operating system itself does.
  useEffect(() => {
    const follow = (event) => {
      const next = event.matches ? 'dark' : 'light'
      applyTheme(next)
      forgetSetting(THEME_KEY)
      setTheme(next)
    }
    darkQuery.addEventListener('change', follow)
    return () => darkQuery.removeEventListener('change', follow)
  }, [])

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

  // Landscape from lg up, so the whole dashboard fits one screen: reading and health on the left, the chart
  // filling the right, then turtle facts beside the statistics, their top rule level with the chart's
  // bottom edge. Below lg it is one column: reading, chart, statistics, facts.
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[1520px] flex-col gap-4.5 px-4 pt-4.5 pb-4 sm:px-8">
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-b-2 border-text pb-3.5">
        <h1 className="flex items-center gap-2.5 font-display text-[28px] leading-none font-semibold">
          {/* The turtle sits low in the emoji's box; lifted so it centres on the word. */}
          <span aria-hidden="true" className="inline-block -translate-y-[0.14em] text-[1.1em] leading-none">🐢</span>
          turtlewatch
        </h1>
        <div className="flex gap-3">
          <Segmented label="Temperature unit" options={[['F', '°F'], ['C', '°C']]} value={unit} onChange={setUnit} />
          <Segmented
            label="Colour theme"
            options={[['light', 'Light mode', 'sun'], ['dark', 'Dark mode', 'moon']]}
            value={theme}
            onChange={chooseTheme}
          />
        </div>
      </header>

      {mode === 'demo' && (
        <p role="status" className="border-l-4 border-warn bg-warn/10 px-4 py-2.5 font-medium">
          Demo data, simulated. The real sensor keeps being polled in the background. Untick Show demo data, at the
          bottom, to see your tank.
        </p>
      )}
      {unreachable && (
        <p role="alert" className="border-l-4 border-danger bg-danger/10 px-4 py-2.5 font-medium">
          Can't reach Turtlewatch. Retrying; anything shown below may be out of date.
        </p>
      )}

      <div
        className={`grid content-start gap-y-6 lg:flex-1 lg:content-stretch lg:grid-cols-[minmax(320px,440px)_minmax(0,1fr)] lg:grid-rows-[minmax(min-content,1fr)_auto] lg:gap-x-14 lg:gap-y-0 ${
          unreachable ? 'opacity-60' : ''
        }`}
      >
        <section aria-label="Current reading" className="flex min-w-0 flex-col gap-5 lg:col-start-1 lg:row-start-1 lg:pb-7">
          <Hero mode={mode} status={status} unit={unit} />
        </section>

        <section aria-label="History" className="flex min-w-0 flex-col gap-3 lg:col-start-2 lg:row-start-1">
          <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
            <div>
              <h2 className="font-display text-2xl leading-tight font-semibold">Water temperature</h2>
              <div className="mt-1 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-sm text-muted">
                {status && (
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-2.5 w-[18px] bg-good/15" aria-hidden="true" />
                    Optimal {degreeRange(status.thresholds_f[2], status.thresholds_f[3], unit)}
                  </span>
                )}
                {current?.bucket > 0 && <span>Average, with the min to max band shaded</span>}
              </div>
            </div>
            <Tabs label="Time range" options={RANGES} value={range} onChange={setRange} />
          </div>
          <div
            className={`relative h-72 border border-line bg-surface transition-opacity sm:h-80 lg:h-auto lg:min-h-60 lg:flex-1 ${
              switching ? 'opacity-60' : ''
            }`}
            aria-busy={switching || undefined}
          >
            {!current ? (
              <div className="absolute inset-0 flex items-center justify-center text-muted">Loading…</div>
            ) : current.points.length === 0 ? (
              <div className="absolute inset-0 flex items-center justify-center text-muted">No readings in this range.</div>
            ) : (
              <HistoryChart history={current} mode={mode} theme={theme} thresholds={status.thresholds_f} tempUnit={unit} />
            )}
          </div>
        </section>

        <div
          role="group"
          aria-label="Statistics for this range"
          className={`flex min-w-0 flex-wrap items-end gap-x-8 gap-y-2.5 transition-opacity lg:col-start-2 lg:row-start-2 lg:pt-2.5 ${
            switching ? 'opacity-60' : ''
          }`}
        >
          <div className="grid flex-[1_1_360px] grid-cols-3 gap-4">
            <Stat label="Min" celsius={stats?.min_c} unit={unit} />
            <Stat label="Avg" celsius={stats?.avg_c} unit={unit} />
            <Stat label="Max" celsius={stats?.max_c} unit={unit} />
          </div>
          {stats && (
            <p className="mb-1 text-sm text-muted">
              From {stats.count.toLocaleString()} stored reading{stats.count === 1 ? '' : 's'} in this range.
            </p>
          )}
        </div>

        <div className="min-w-0 lg:col-start-1 lg:row-start-2 lg:-mt-px">
          <TurtleFact />
        </div>
      </div>

      <SystemStatus mode={mode} status={status} onModeChange={setMode} />
    </main>
  )
}
