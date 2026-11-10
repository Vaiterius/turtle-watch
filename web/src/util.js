// Pure helpers: no React, no DOM, so `node --test` can run them.

export const DAY = 86400

export const toF = (celsius) => (celsius * 9) / 5 + 32

// The API wants minutes east of UTC. getTimezoneOffset() is minutes WEST, so negate it.
export const tzOffsetMinutes = (date = new Date()) => -date.getTimezoneOffset()

export async function fetchJson(url, signal) {
  const response = await fetch(url, { signal })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

export function formatAge(seconds) {
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 2 * DAY) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / DAY)} days ago`
}

const fmt = (options) => new Intl.DateTimeFormat(undefined, options)
const TIME = fmt({ hour: 'numeric', minute: '2-digit' })
const HOUR = fmt({ hour: 'numeric' })
const DATE = fmt({ month: 'short', day: 'numeric' })
const DATE_TIME = fmt({ month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const FULL_DATE = fmt({ weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
const MONTH = fmt({ month: 'short' })
const YEAR = fmt({ year: 'numeric' })

const at = (ts) => new Date(ts * 1000)
export const formatTime = (ts) => TIME.format(at(ts))
export const formatDateTime = (ts) => DATE_TIME.format(at(ts))

// Axis ticks on local-time boundaries: every few hours, days or months by window size.
export function buildTicks(start, end) {
  const span = end - start
  const ticks = []
  const d = at(start)
  let unit, advance
  if (span <= 2 * DAY) {
    const hours = span <= 6 * 3600 ? 1 : 3
    unit = 'hour'
    d.setHours(Math.floor(d.getHours() / hours) * hours, 0, 0, 0)
    advance = () => d.setHours(d.getHours() + hours)
  } else if (span <= 100 * DAY) {
    const days = span <= 10 * DAY ? 1 : span <= 45 * DAY ? 5 : 14
    unit = 'day'
    d.setHours(0, 0, 0, 0)
    advance = () => d.setDate(d.getDate() + days)
  } else {
    const months = span <= 500 * DAY ? 1 : span <= 4 * 365 * DAY ? 3 : 12
    unit = months === 12 ? 'year' : 'month'
    d.setHours(0, 0, 0, 0)
    d.setMonth(Math.floor(d.getMonth() / months) * months, 1)
    advance = () => d.setMonth(d.getMonth() + months)
  }
  for (let guard = 0; guard < 500; guard++) {
    const ts = d.getTime() / 1000
    if (ts > end) break
    if (ts >= start) ticks.push(ts)
    advance()
  }
  return { unit, ticks }
}

export function formatTick(ts, unit) {
  const d = at(ts)
  if (unit === 'hour') return d.getHours() === 0 ? DATE.format(d) : HOUR.format(d)
  if (unit === 'day') return DATE.format(d)
  if (unit === 'month') return d.getMonth() === 0 ? YEAR.format(d) : MONTH.format(d)
  return YEAR.format(d)
}

// Tooltip heading for a point; `bucket` is the API's bucket size in seconds (0 = raw).
export function formatPointTime(ts, bucket) {
  if (bucket >= 7 * DAY) return `Week of ${FULL_DATE.format(at(ts))}`
  if (bucket >= DAY) return FULL_DATE.format(at(ts))
  if (bucket > 0) return `${DATE_TIME.format(at(ts))} to ${TIME.format(at(ts + bucket))}`
  return DATE_TIME.format(at(ts))
}

// True where a point has no neighbour within the gap limit, so the chart can
// draw it as a dot instead of letting it vanish between two line breaks.
export function isolatedFlags(xs, maxGap) {
  return xs.map((x, i) => {
    const before = i > 0 && x - xs[i - 1] <= maxGap
    const after = i < xs.length - 1 && xs[i + 1] - x <= maxGap
    return !before && !after
  })
}
