import { describe, expect, it } from 'vitest'
import { buildFigure } from '../components/ChartRenderer'
import type { ChartSpec } from '../api/types'

const spec = (overrides: Partial<ChartSpec>): ChartSpec => ({
  chart_type: 'bar',
  title: 'Test',
  encoding: {},
  aggregation: 'sum',
  filters: [],
  sort: 'natural',
  limit: 50,
  drill_path: [],
  notes: [],
  ...overrides,
})

describe('buildFigure', () => {
  it('sorts bars by value descending', () => {
    const figure = buildFigure(
      spec({ chart_type: 'bar', encoding: { x: 'region', y: 'Revenue' } }),
      ['region', 'Revenue'],
      [
        { region: 'South', Revenue: 10 },
        { region: 'North', Revenue: 40 },
        { region: 'East', Revenue: 25 },
      ],
    )
    expect(figure?.data[0].x).toEqual(['North', 'East', 'South'])
    expect(figure?.data[0].y).toEqual([40, 25, 10])
  })

  it('adds a cumulative trace for a pareto chart', () => {
    const figure = buildFigure(
      spec({ chart_type: 'pareto', encoding: { x: 'category', y: 'Revenue' } }),
      ['category', 'Revenue'],
      [
        { category: 'A', Revenue: 50 },
        { category: 'B', Revenue: 30 },
        { category: 'C', Revenue: 20 },
      ],
    )
    expect(figure?.data).toHaveLength(2)
    const cumulative = figure?.data[1] as { y: number[] }
    expect(cumulative.y[cumulative.y.length - 1]).toBeCloseTo(100)
  })

  it('creates one line per series and reads the period column', () => {
    const figure = buildFigure(
      spec({ chart_type: 'multi_line', encoding: { x: 'order_date', y: 'Revenue', series: 'channel' } }),
      ['period', 'channel', 'Revenue'],
      [
        { period: '2024-01-01T00:00:00', channel: 'Online', Revenue: 5 },
        { period: '2024-02-01T00:00:00', channel: 'Online', Revenue: 7 },
        { period: '2024-01-01T00:00:00', channel: 'Retail', Revenue: 3 },
      ],
    )
    expect(figure?.data).toHaveLength(2)
    expect(figure?.layout.showlegend).toBe(true)
    expect(figure?.data[0].x).toEqual(['2024-01-01T00:00:00', '2024-02-01T00:00:00'])
  })

  it('normalises a 100% stacked bar', () => {
    const figure = buildFigure(
      spec({ chart_type: 'stacked_bar_100', encoding: { x: 'region', y: 'Revenue', series: 'channel' } }),
      ['region', 'channel', 'Revenue'],
      [{ region: 'North', channel: 'Online', Revenue: 5 }],
    )
    expect(figure?.layout).toMatchObject({ barmode: 'stack', barnorm: 'percent' })
  })

  it('returns null for chart types that need a purpose-built visual', () => {
    expect(buildFigure(spec({ chart_type: 'decomposition_tree' }), ['a'], [{ a: 1 }])).toBeNull()
    expect(buildFigure(spec({ chart_type: 'map' }), ['a'], [{ a: 1 }])).toBeNull()
  })

  it('groups scatter points by cluster label', () => {
    const figure = buildFigure(
      spec({ chart_type: 'cluster_scatter', encoding: { x: 'units', y: 'revenue' } }),
      ['units', 'revenue', '__cluster__'],
      [
        { units: 1, revenue: 2, __cluster__: 'Segment 1' },
        { units: 3, revenue: 6, __cluster__: 'Segment 2' },
      ],
    )
    expect(figure?.data).toHaveLength(2)
  })
})
