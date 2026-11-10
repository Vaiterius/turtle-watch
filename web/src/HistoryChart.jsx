import { useEffect, useRef } from 'react'
import {
  Chart, Filler, LinearScale, LineController, LineElement, PointElement, Tooltip,
} from 'chart.js'
import { buildTicks, formatPointTime, formatTick, isolatedFlags, toF } from './util.js'

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler, Tooltip)

// Chart.js draws on a canvas, so it reads the same theme tokens the utilities use.
const token = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(`--color-${name}`).trim()

const withAlpha = (hex, alpha) => {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

const crosshair = {
  id: 'crosshair',
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.() ?? []
    if (!active.length) return
    const { ctx, chartArea } = chart
    ctx.save()
    ctx.strokeStyle = token('line')
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(active[0].element.x, chartArea.top)
    ctx.lineTo(active[0].element.x, chartArea.bottom)
    ctx.stroke()
    ctx.restore()
  },
}

// `mode` is a dependency only so the colours are re-read when the accent changes.
export default function HistoryChart({ history, mode }) {
  const canvas = useRef(null)

  useEffect(() => {
    const { points, start, end, step, bucket } = history
    const accent = token('accent')
    const muted = token('muted')
    const aggregated = bucket > 0
    const maxGap = step * 1.5
    const isolated = isolatedFlags(points.map((p) => p[0]), maxGap)
    const series = (column) =>
      points.map((p) => ({ x: p[0], y: toF(p[column]), lo: toF(p[2]), hi: toF(p[3]), n: p[4] }))
    const hidden = { borderWidth: 0, pointRadius: 0, pointHoverRadius: 0, spanGaps: maxGap }
    const { unit, ticks } = buildTicks(start, end)

    const chart = new Chart(canvas.current, {
      type: 'line',
      data: {
        datasets: [
          {
            data: series(1),
            borderColor: accent,
            borderWidth: 2,
            borderJoinStyle: 'round',
            // Breaks the line wherever readings are missing; never bridges a gap.
            spanGaps: maxGap,
            pointRadius: isolated.map((alone) => (alone ? 2.5 : 0)),
            pointBackgroundColor: accent,
            pointBorderWidth: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: accent,
            pointHoverBorderColor: token('surface'),
            pointHoverBorderWidth: 2,
          },
          ...(aggregated
            ? [
                { data: series(2), ...hidden },
                { data: series(3), ...hidden, fill: '-1', backgroundColor: withAlpha(accent, 0.16) },
              ]
            : []),
        ],
      },
      options: {
        parsing: false,
        normalized: true,
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { right: 12 } },
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            type: 'linear',
            min: start,
            max: end,
            afterBuildTicks: (axis) => { axis.ticks = ticks.map((value) => ({ value })) },
            ticks: {
              color: muted,
              maxRotation: 0,
              autoSkipPadding: 16,
              callback: (value) => formatTick(value, unit),
            },
            grid: { display: false },
            border: { color: token('line') },
          },
          y: {
            grace: '12%',
            ticks: { color: muted, maxTicksLimit: 6, callback: (value) => `${Math.round(value * 10) / 10}°` },
            grid: { color: withAlpha(token('line'), 0.6) },
            border: { display: false },
          },
        },
        plugins: {
          tooltip: {
            filter: (item) => item.datasetIndex === 0,
            displayColors: false,
            backgroundColor: token('bg'),
            borderColor: token('line'),
            borderWidth: 1,
            titleColor: muted,
            titleFont: { weight: 'normal' },
            bodyColor: token('text'),
            bodyFont: { size: 14, weight: 'bold' },
            footerColor: muted,
            footerFont: { weight: 'normal' },
            padding: 10,
            callbacks: {
              title: (items) => (items.length ? formatPointTime(items[0].raw.x, bucket) : ''),
              label: (item) => `${item.raw.y.toFixed(1)} °F${aggregated ? ' average' : ''}`,
              footer: (items) => {
                if (!aggregated || !items.length) return ''
                const { lo, hi, n } = items[0].raw
                return `${lo.toFixed(1)} to ${hi.toFixed(1)} °F · ${n} reading${n === 1 ? '' : 's'}`
              },
            },
          },
        },
      },
      plugins: [crosshair],
    })
    return () => chart.destroy()
  }, [history, mode])

  return (
    <div className="relative h-64 sm:h-80">
      <canvas ref={canvas} role="img" aria-label="Water temperature over time" />
    </div>
  )
}
