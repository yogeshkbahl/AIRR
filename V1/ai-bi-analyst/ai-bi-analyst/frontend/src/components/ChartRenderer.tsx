import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import { Alert, Box, Stack, Typography } from '@mui/material'
import type { ChartSpec } from '../api/types'
import { compactNumber, fullNumber } from '../utils/format'
import { palette } from '../theme'
import { ResultTable } from './Bits'

/** Colour-blind-safe sequence; order is stable so a series keeps its colour. */
const SERIES_COLORS = ['#1F4E79', '#17605B', '#B25E00', '#5B3E8E', '#8A3A5E', '#3F6B8A', '#6B7A32', '#9A4A20']

const BASE_LAYOUT = {
  margin: { l: 64, r: 24, t: 8, b: 56 },
  font: { family: '"IBM Plex Sans", sans-serif', size: 12, color: palette.ink },
  paper_bgcolor: 'transparent',
  plot_bgcolor: 'transparent',
  xaxis: { gridcolor: palette.line, zerolinecolor: palette.line, automargin: true },
  yaxis: { gridcolor: palette.line, zerolinecolor: palette.line, automargin: true },
  legend: { orientation: 'h' as const, y: -0.22 },
  hoverlabel: { bgcolor: '#FFFFFF', bordercolor: palette.line },
  showlegend: false,
}

const CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] as never[] }

type Row = Record<string, unknown>

export interface ChartRendererProps {
  spec: ChartSpec
  columns: string[]
  rows: Row[]
  height?: number
}

const valueKey = (columns: string[], spec: ChartSpec): string => {
  const metricish = columns.filter(
    (c) => c !== 'period' && c !== spec.encoding.x && c !== spec.encoding.series && c !== spec.encoding.facet,
  )
  return metricish[metricish.length - 1] ?? columns[columns.length - 1]
}

const categoryKey = (columns: string[], spec: ChartSpec): string => {
  if (columns.includes('period')) return 'period'
  if (spec.encoding.x && columns.includes(spec.encoding.x)) return spec.encoding.x
  return columns[0]
}

const num = (value: unknown): number => (typeof value === 'number' ? value : Number(value) || 0)
const text = (value: unknown): string => (value === null || value === undefined ? '(blank)' : String(value))

export interface Figure {
  data: Record<string, unknown>[]
  layout: Record<string, unknown>
}

/** Maps a validated spec plus computed rows onto a Plotly figure. */
export function buildFigure(spec: ChartSpec, columns: string[], rows: Row[]): Figure | null {
  const type = spec.chart_type
  const yKey = valueKey(columns, spec)
  const xKey = categoryKey(columns, spec)
  const seriesKey = spec.encoding.series && columns.includes(spec.encoding.series) ? spec.encoding.series : null

  const groupSeries = () => {
    const groups = new Map<string, Row[]>()
    rows.forEach((row) => {
      const key = seriesKey ? text(row[seriesKey]) : 'All'
      const bucket = groups.get(key) ?? []
      bucket.push(row)
      groups.set(key, bucket)
    })
    return [...groups.entries()]
  }

  switch (type) {
    case 'line':
    case 'multi_line':
    case 'area':
    case 'forecast_line':
    case 'anomaly_timeline':
    case 'small_multiples': {
      const groups = groupSeries()
      return {
        data: groups.map(([name, groupRows], index) => ({
          type: 'scatter' as const,
          mode: 'lines+markers' as const,
          name,
          x: groupRows.map((row) => text(row[xKey])),
          y: groupRows.map((row) => num(row[yKey])),
          line: { color: SERIES_COLORS[index % SERIES_COLORS.length], width: 2 },
          marker: { size: 5 },
          fill: type === 'area' ? ('tozeroy' as const) : undefined,
        })),
        layout: { ...BASE_LAYOUT, showlegend: groups.length > 1, yaxis: { ...BASE_LAYOUT.yaxis, title: yKey } },
      }
    }
    case 'bar':
    case 'column':
    case 'pareto':
    case 'driver_bar':
    case 'waterfall': {
      const sorted = [...rows].sort((a, b) => num(b[yKey]) - num(a[yKey]))
      const bars = {
        type: 'bar' as const,
        x: sorted.map((row) => text(row[xKey])),
        y: sorted.map((row) => num(row[yKey])),
        marker: { color: palette.primary },
        name: yKey,
        hovertemplate: `%{x}<br>${yKey}: %{y:,.2f}<extra></extra>`,
      }
      if (type !== 'pareto') {
        return { data: [bars], layout: { ...BASE_LAYOUT, yaxis: { ...BASE_LAYOUT.yaxis, title: yKey } } }
      }
      const total = sorted.reduce((sum, row) => sum + num(row[yKey]), 0) || 1
      let running = 0
      const cumulative = sorted.map((row) => {
        running += num(row[yKey])
        return (100 * running) / total
      })
      return {
        data: [
          bars,
          {
            type: 'scatter' as const,
            mode: 'lines+markers' as const,
            name: 'Cumulative %',
            x: sorted.map((row) => text(row[xKey])),
            y: cumulative,
            yaxis: 'y2',
            line: { color: palette.severity.medium, width: 2 },
          },
        ],
        layout: {
          ...BASE_LAYOUT,
          showlegend: true,
          yaxis: { ...BASE_LAYOUT.yaxis, title: yKey },
          yaxis2: { overlaying: 'y', side: 'right', range: [0, 100], title: 'Cumulative %', gridcolor: 'transparent' },
        },
      }
    }
    case 'stacked_bar':
    case 'stacked_bar_100':
    case 'funnel': {
      const groups = groupSeries()
      return {
        data: groups.map(([name, groupRows], index) => ({
          type: 'bar' as const,
          name,
          x: groupRows.map((row) => text(row[xKey])),
          y: groupRows.map((row) => num(row[yKey])),
          marker: { color: SERIES_COLORS[index % SERIES_COLORS.length] },
        })),
        layout: {
          ...BASE_LAYOUT,
          barmode: 'stack' as const,
          barnorm: type === 'stacked_bar_100' ? ('percent' as const) : undefined,
          showlegend: groups.length > 1,
        },
      }
    }
    case 'pie':
    case 'donut': {
      return {
        data: [
          {
            type: 'pie' as const,
            hole: type === 'donut' ? 0.55 : 0,
            labels: rows.map((row) => text(row[xKey])),
            values: rows.map((row) => num(row[yKey])),
            marker: { colors: SERIES_COLORS },
            textinfo: 'label+percent' as const,
          },
        ],
        layout: { ...BASE_LAYOUT, margin: { l: 8, r: 8, t: 8, b: 8 } },
      }
    }
    case 'histogram': {
      const key = spec.encoding.x && columns.includes(spec.encoding.x) ? spec.encoding.x : columns[0]
      return {
        data: [
          {
            type: 'histogram' as const,
            x: rows.map((row) => num(row[key])),
            marker: { color: palette.primary },
            nbinsx: 30,
          },
        ],
        layout: { ...BASE_LAYOUT, xaxis: { ...BASE_LAYOUT.xaxis, title: key }, yaxis: { ...BASE_LAYOUT.yaxis, title: 'Rows' } },
      }
    }
    case 'scatter':
    case 'bubble':
    case 'cluster_scatter': {
      const xColumn = spec.encoding.x ?? columns[0]
      const yColumn = spec.encoding.y ?? columns[1]
      const colorColumn =
        (type === 'cluster_scatter' ? '__cluster__' : spec.encoding.color) ?? null
      const groups = new Map<string, Row[]>()
      rows.forEach((row) => {
        const key = colorColumn && row[colorColumn] !== undefined ? text(row[colorColumn]) : 'All rows'
        const bucket = groups.get(key) ?? []
        bucket.push(row)
        groups.set(key, bucket)
      })
      return {
        data: [...groups.entries()].map(([name, groupRows], index) => ({
          type: 'scattergl' as const,
          mode: 'markers' as const,
          name,
          x: groupRows.map((row) => num(row[xColumn])),
          y: groupRows.map((row) => num(row[yColumn])),
          marker: {
            color: SERIES_COLORS[index % SERIES_COLORS.length],
            size: spec.encoding.size ? undefined : 6,
            opacity: 0.65,
          },
        })),
        layout: {
          ...BASE_LAYOUT,
          showlegend: groups.size > 1,
          xaxis: { ...BASE_LAYOUT.xaxis, title: xColumn },
          yaxis: { ...BASE_LAYOUT.yaxis, title: yColumn },
        },
      }
    }
    case 'box': {
      const groupColumn = spec.encoding.x ?? columns[0]
      const valueColumn = spec.encoding.y ?? columns[1]
      const groups = new Map<string, number[]>()
      rows.forEach((row) => {
        const key = text(row[groupColumn])
        const bucket = groups.get(key) ?? []
        bucket.push(num(row[valueColumn]))
        groups.set(key, bucket)
      })
      return {
        data: [...groups.entries()].map(([name, values], index) => ({
          type: 'box' as const,
          name,
          y: values,
          boxpoints: 'outliers' as const,
          marker: { color: SERIES_COLORS[index % SERIES_COLORS.length] },
        })),
        layout: { ...BASE_LAYOUT, yaxis: { ...BASE_LAYOUT.yaxis, title: valueColumn } },
      }
    }
    case 'heatmap':
    case 'cohort_heatmap': {
      const rowKey = spec.encoding.series ?? spec.encoding.y ?? columns[1]
      const colKey = columns.includes('period') ? 'period' : spec.encoding.x ?? columns[0]
      const xs = [...new Set(rows.map((row) => text(row[colKey])))]
      const ys = [...new Set(rows.map((row) => text(row[rowKey])))]
      const lookup = new Map(rows.map((row) => [`${text(row[colKey])}|${text(row[rowKey])}`, num(row[yKey])]))
      return {
        data: [
          {
            type: 'heatmap' as const,
            x: xs,
            y: ys,
            z: ys.map((y) => xs.map((x) => lookup.get(`${x}|${y}`) ?? null)),
            colorscale: [
              [0, '#F2F6F9'],
              [0.5, '#7FA5C4'],
              [1, palette.primary],
            ] as never,
            hovertemplate: `%{x} · %{y}<br>${yKey}: %{z:,.2f}<extra></extra>`,
          },
        ],
        layout: { ...BASE_LAYOUT, margin: { ...BASE_LAYOUT.margin, l: 130 } },
      }
    }
    default:
      return null
  }
}

export default function ChartRenderer({ spec, columns, rows, height = 320 }: ChartRendererProps) {
  const figure = useMemo(() => buildFigure(spec, columns, rows), [spec, columns, rows])

  if (!rows.length) {
    return <Typography variant="body2" color="text.secondary">The query returned no rows for this chart.</Typography>
  }

  if (spec.chart_type === 'kpi_card' || spec.chart_type === 'scorecard') {
    const key = valueKey(columns, spec)
    return (
      <Stack spacing={0.5} sx={{ py: 2 }}>
        <Typography className="figure" sx={{ fontSize: '2.6rem', fontWeight: 600, letterSpacing: '-0.03em' }}>
          {compactNumber(num(rows[0][key]))}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {key} · {spec.aggregation} · exact {fullNumber(num(rows[0][key]))}
        </Typography>
      </Stack>
    )
  }

  if (spec.chart_type === 'table' || spec.chart_type === 'pivot_table') {
    return <ResultTable columns={columns} rows={rows} maxHeight={height + 60} />
  }

  if (!figure) {
    return (
      <Box>
        <Alert severity="info" sx={{ mb: 1.5 }}>
          {spec.chart_type.replace(/_/g, ' ')} needs a purpose-built visual in your BI tool. The computed rows
          behind it are shown here so you can check the numbers before building it.
        </Alert>
        <ResultTable columns={columns} rows={rows} maxHeight={280} />
      </Box>
    )
  }

  return (
    <Plot
      data={figure.data as never}
      layout={{ ...figure.layout, height, autosize: true } as never}
      config={CONFIG}
      style={{ width: '100%' }}
      useResizeHandler
    />
  )
}
