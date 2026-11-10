import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DAY, buildTicks, degreeRange, degrees, excursionText, formatAge, healthDetail, healthFor, healthScale, isolatedFlags, toF,
  tzOffsetMinutes,
} from './util.js'

test('units: limits and readings convert only for display', () => {
  assert.equal(degrees(70, 'F'), '70 °F')
  assert.equal(degrees(70, 'C'), '21.1 °C')
  assert.equal(degreeRange(74, 79, 'C'), '23.3–26.1 °C')
  assert.equal(healthDetail('optimal', [68, 70, 74, 79, 82], 'C'), 'Within the 23.3–26.1 °C optimal range.')
})

test('sustained cooling wording', () => {
  const t = [68, 70, 74, 79, 82]
  assert.equal(excursionText({ start: 0, last_below: 1500, recovering: false }, t), 'Below 70 °F for about 25 minutes.')
  assert.equal(excursionText({ start: 0, last_below: 0, recovering: false }, t), 'Below 70 °F at the latest reading.')
  assert.equal(excursionText({ start: 0, last_below: 3 * 3600, recovering: true }, t, 'C'),
    'Recovering after about 3 hours below 21.1 °C.')
})

test('health bands follow the table, edges included', () => {
  const t = [68, 70, 74, 79, 82]
  const cases = [[50, 'too-cold'], [67.9, 'too-cold'], [68, 'too-cool'], [69.9, 'too-cool'],
    [70, 'overnight'], [73.9, 'overnight'], [73.96, 'optimal'], [74, 'optimal'], [79, 'optimal'],
    [79.1, 'warm'], [82, 'warm'], [82.1, 'too-warm'], [95, 'too-warm']]
  for (const [f, key] of cases) assert.equal(healthFor(f, t), key, `${f} °F`)
  assert.equal(healthFor(72, [60, 65, 70, 75, 80]), 'optimal')
})

test('Celsius to Fahrenheit', () => {
  assert.equal(toF(0), 32)
  assert.equal(toF(100), 212)
  assert.ok(Math.abs(toF(24.4) - 75.92) < 1e-9)
})

test('tz is minutes east of UTC, the negation of getTimezoneOffset', () => {
  // Pacific standard time: getTimezoneOffset() is +480, the API wants -480.
  assert.equal(tzOffsetMinutes({ getTimezoneOffset: () => 480 }), -480)
  assert.equal(tzOffsetMinutes({ getTimezoneOffset: () => -330 }), 330)
})

test('age wording', () => {
  assert.equal(formatAge(20), 'just now')
  assert.equal(formatAge(185), '3 min ago')
  assert.equal(formatAge(7300), '2 h ago')
  assert.equal(formatAge(3 * DAY + 5), '3 days ago')
})

test('ticks stay inside the window on local boundaries', () => {
  const end = Math.floor(new Date(2026, 10, 10, 15, 20).getTime() / 1000)
  for (const [span, unit] of [[DAY, 'hour'], [7 * DAY, 'day'], [30 * DAY, 'day'],
    [365 * DAY, 'month'], [6 * 365 * DAY, 'year']]) {
    const { ticks, unit: got } = buildTicks(end - span, end)
    assert.equal(got, unit)
    assert.ok(ticks.length >= 2 && ticks.length <= 16, `${unit}: ${ticks.length} ticks`)
    for (const ts of ticks) {
      assert.ok(ts >= end - span && ts <= end)
      const d = new Date(ts * 1000)
      assert.equal(d.getMinutes(), 0)
      if (unit !== 'hour') assert.equal(d.getHours(), 0)
      if (unit === 'month' || unit === 'year') assert.equal(d.getDate(), 1)
    }
  }
  assert.deepEqual(buildTicks(end, end).ticks.length <= 1, true)
})

test('isolated points are flagged', () => {
  assert.deepEqual(isolatedFlags([0, 300, 600, 2000, 5000, 5300], 450),
    [false, false, false, true, false, false])
  assert.deepEqual(isolatedFlags([42], 450), [true])
})

test('health scale spans the limits with a clamped marker', () => {
  const t = [68, 70, 74, 79, 82]
  const { bands, ticks, marker } = healthScale(t, 77)
  assert.deepEqual(bands.map((b) => b.key), ['too-cold', 'too-cool', 'overnight', 'optimal', 'warm', 'too-warm'])
  assert.ok(Math.abs(bands.reduce((sum, b) => sum + b.width, 0) - 100) < 1e-9)
  assert.equal(ticks[0].at, 2 / 18 * 100)
  assert.equal(marker, 11 / 18 * 100)
  assert.equal(healthScale(t, 40).marker, 0)
  assert.equal(healthScale(t, 99).marker, 100)
})
