import test from 'node:test'
import assert from 'node:assert/strict'
import { DAY, buildTicks, formatAge, isolatedFlags, toF, tzOffsetMinutes } from './util.js'

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
