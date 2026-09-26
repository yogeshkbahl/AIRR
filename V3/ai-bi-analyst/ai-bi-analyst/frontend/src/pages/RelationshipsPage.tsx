import { useMemo, useState } from 'react'
import Plot from 'react-plotly.js'
import { useQuery } from '@tanstack/react-query'
import {
  Alert, Box, Button, Chip, FormControl, Grid, InputLabel, MenuItem, Paper, Select, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, Typography,
} from '@mui/material'
import { api } from '../api/client'
import type { RelationshipResult } from '../api/types'
import { EmptyState, ErrorView, EvidenceTag, Loading, SectionHeader } from '../components/Bits'
import ChartCard from '../components/ChartCard'
import { useStoryboard } from '../hooks/useStoryboard'
import { palette } from '../theme'
import { fullNumber, titleCase } from '../utils/format'

const METHOD_HELP: Record<string, string> = {
  pearson: 'Linear correlation between two numeric columns, −1 to 1.',
  spearman: 'Rank correlation. Robust to outliers and non-linear but monotonic shapes.',
  cramers_v: 'Association between two categorical columns, 0 to 1, from a chi-square table.',
  correlation_ratio_eta: 'Share of a measure’s variance explained by group membership, 0 to 1.',
  point_biserial: 'Correlation between a two-valued flag and a measure.',
  spearman_vs_time: 'Monotonic trend of a measure against time.',
  not_applicable: 'No statistic is appropriate for this pair of roles.',
}

export default function RelationshipsPage({ datasetId }: { datasetId: string }) {
  const [pair, setPair] = useState<{ x: string; y: string } | null>(null)
  const [segmentBy, setSegmentBy] = useState('')
  const { addInsight } = useStoryboard(datasetId)

  const columns = useQuery({ queryKey: ['columns', datasetId], queryFn: () => api.columns(datasetId) })
  const matrix = useQuery({
    queryKey: ['relationships', datasetId],
    queryFn: () => api.relationships(datasetId),
  })
  const detail = useQuery({
    queryKey: ['pair', datasetId, pair, segmentBy],
    queryFn: () =>
      api.relationshipPair(datasetId, {
        column_x: pair!.x,
        column_y: pair!.y,
        segment_by: segmentBy || null,
      }),
    enabled: Boolean(pair),
  })

  const segmentCandidates = useMemo(
    () =>
      (columns.data ?? []).filter(
        (column) =>
          (column.analytical_role === 'categorical_dimension' || column.analytical_role === 'geography') &&
          column.distinct_count >= 2 &&
          column.distinct_count <= 12,
      ),
    [columns.data],
  )

  if (matrix.isLoading) return <Loading label="Computing pairwise associations…" />
  if (matrix.error) return <ErrorView error={matrix.error} />
  if (!matrix.data) return <EmptyState title="No matrix available">Upload a file first.</EmptyState>

  const { labels, values, methods, columns: names, pairs } = matrix.data
  const ranked = pairs.filter((item) => item.reliable && item.statistic !== null).slice(0, 12)
  const suppressed = pairs.filter((item) => !item.reliable)

  return (
    <Box>
      <SectionHeader
        title="Relationships"
        description="The statistic is chosen from the pair of column roles, not applied blindly. Click any cell to drill into that pair."
      />

      {names.length < 2 ? (
        <EmptyState title="Not enough correlatable columns">
          At least two measures, dimensions or dates are needed.
        </EmptyState>
      ) : (
        <Grid container spacing={2}>
          <Grid item xs={12} lg={7}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h3">Association matrix</Typography>
              <Typography variant="caption">{matrix.data.note}</Typography>
              <Plot
                data={[
                  {
                    type: 'heatmap',
                    x: labels,
                    y: labels,
                    z: values as number[][],
                    zmin: -1,
                    zmax: 1,
                    colorscale: [
                      [0, '#8A3A5E'],
                      [0.5, '#F4F6F8'],
                      [1, palette.primary],
                    ] as never,
                    hoverongaps: false,
                    customdata: methods as never,
                    hovertemplate: '%{y} × %{x}<br>%{customdata} = %{z:.3f}<extra></extra>',
                  } as never,
                ]}
                layout={
                  {
                    height: Math.max(360, 26 * labels.length),
                    margin: { l: 150, r: 20, t: 10, b: 140 },
                    font: { family: '"IBM Plex Sans", sans-serif', size: 11 },
                    paper_bgcolor: 'transparent',
                    xaxis: { tickangle: -45, automargin: true },
                    yaxis: { automargin: true },
                  } as never
                }
                config={{ displaylogo: false, responsive: true }}
                style={{ width: '100%' }}
                useResizeHandler
                onClick={(event) => {
                  const point = event.points?.[0]
                  if (!point) return
                  const x = names[labels.indexOf(String(point.x))]
                  const y = names[labels.indexOf(String(point.y))]
                  if (x && y && x !== y) setPair({ x, y })
                }}
              />
              {suppressed.length > 0 && (
                <Typography variant="caption">
                  {suppressed.length} pairs are blank because they were unreliable: unsupported roles, constant
                  columns, tiny samples or excessive missingness.
                </Typography>
              )}
            </Paper>
          </Grid>

          <Grid item xs={12} lg={5}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h3">Strongest associations</Typography>
              <Table size="small" sx={{ mt: 1 }}>
                <TableHead>
                  <TableRow>
                    <TableCell>Pair</TableCell>
                    <TableCell>Method</TableCell>
                    <TableCell align="right">Statistic</TableCell>
                    <TableCell align="right">n</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {ranked.map((item) => (
                    <TableRow
                      key={`${item.column_x}-${item.column_y}`}
                      hover
                      sx={{ cursor: 'pointer' }}
                      onClick={() => setPair({ x: item.column_x, y: item.column_y })}
                    >
                      <TableCell>
                        {item.column_x} × {item.column_y}
                      </TableCell>
                      <TableCell>
                        <Chip size="small" variant="outlined" label={item.method.replace(/_/g, ' ')} />
                      </TableCell>
                      <TableCell align="right" className="figure">
                        {fullNumber(item.statistic)}
                      </TableCell>
                      <TableCell align="right" className="figure">{item.sample_size.toLocaleString()}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Alert severity="info" sx={{ mt: 2 }}>
                A strong association is a place to look, not a cause. Two columns can move together because a
                third one drives both.
              </Alert>
            </Paper>
          </Grid>
        </Grid>
      )}

      {pair && (
        <Box sx={{ mt: 3 }}>
          <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 1.5 }} flexWrap="wrap" useFlexGap>
            <Typography variant="h2">
              {pair.x} × {pair.y}
            </Typography>
            <FormControl size="small" sx={{ minWidth: 200 }}>
              <InputLabel id="segment-label">Check within segments of</InputLabel>
              <Select
                labelId="segment-label"
                label="Check within segments of"
                value={segmentBy}
                onChange={(event) => setSegmentBy(event.target.value)}
              >
                <MenuItem value="">No segmentation</MenuItem>
                {segmentCandidates.map((column) => (
                  <MenuItem key={column.name} value={column.name}>{column.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <Button onClick={() => setPair(null)}>Close</Button>
          </Stack>

          {detail.isLoading && <Loading label="Analysing the pair…" />}
          {detail.error && <ErrorView error={detail.error} />}

          {detail.data && (
            <Grid container spacing={2}>
              <Grid item xs={12} md={6}>
                <ChartCard datasetId={datasetId} spec={detail.data.chart} height={320} />
              </Grid>
              <Grid item xs={12} md={6}>
                <Paper sx={{ p: 2, height: '100%' }}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="h3">Statistics</Typography>
                    <EvidenceTag kind="inference" />
                  </Stack>
                  <StatisticTable result={detail.data.result} />
                  <Typography variant="body2" sx={{ mt: 1.5 }}>{detail.data.result.interpretation}</Typography>
                  {detail.data.result.warnings.map((warning) => (
                    <Alert key={warning} severity="warning" sx={{ mt: 1 }}>{warning}</Alert>
                  ))}

                  {detail.data.segment && (
                    <Box sx={{ mt: 2, pt: 2, borderTop: `1px solid ${palette.line}` }}>
                      <Typography variant="h4">
                        Within segments of {detail.data.segment.segment_column}
                      </Typography>
                      <Table size="small" sx={{ mt: 1 }}>
                        <TableBody>
                          {detail.data.segment.segments.map((segment, index) => (
                            <TableRow key={segment}>
                              <TableCell sx={{ border: 0, py: 0.25 }}>{segment}</TableCell>
                              <TableCell align="right" className="figure" sx={{ border: 0, py: 0.25 }}>
                                {fullNumber(detail.data!.segment!.statistics[index])}
                              </TableCell>
                              <TableCell align="right" className="figure" sx={{ border: 0, py: 0.25 }}>
                                n={detail.data!.segment!.sample_sizes[index].toLocaleString()}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                      <Alert
                        severity={detail.data.segment.simpsons_paradox_suspected ? 'error' : 'success'}
                        sx={{ mt: 1 }}
                      >
                        {detail.data.segment.note}
                      </Alert>
                    </Box>
                  )}

                  <Button
                    sx={{ mt: 2 }}
                    onClick={() =>
                      addInsight(
                        `${pair.x} × ${pair.y}`,
                        detail.data!.result.interpretation,
                        'inference',
                      )
                    }
                  >
                    Add this reading to the storyboard
                  </Button>
                </Paper>
              </Grid>
            </Grid>
          )}
        </Box>
      )}
    </Box>
  )
}

function StatisticTable({ result }: { result: RelationshipResult }) {
  const rows: [string, string][] = [
    ['Pair kind', titleCase(result.pair_kind)],
    ['Method', `${result.method.replace(/_/g, ' ')} — ${METHOD_HELP[result.method] ?? 'type-appropriate statistic'}`],
    ['Statistic', fullNumber(result.statistic)],
    ['Effect size', result.effect_size_label ?? '—'],
    ['p-value', result.p_value === null ? '—' : result.p_value < 0.0001 ? '< 0.0001' : result.p_value.toPrecision(3)],
    ['Complete pairs', result.sample_size.toLocaleString()],
    ['Rows dropped for nulls', result.missing_dropped.toLocaleString()],
  ]
  if (result.secondary_method) {
    rows.splice(3, 0, [
      titleCase(result.secondary_method),
      fullNumber(result.secondary_statistic),
    ])
  }
  return (
    <Table size="small" sx={{ mt: 1 }}>
      <TableBody>
        {rows.map(([label, value]) => (
          <TableRow key={label}>
            <TableCell sx={{ border: 0, py: 0.3, width: 170, verticalAlign: 'top' }}>{label}</TableCell>
            <TableCell sx={{ border: 0, py: 0.3 }}>{value}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
