import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert, Box, Button, Card, CardContent, Chip, Grid, LinearProgress, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, Tooltip, Typography,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import { api } from '../api/client'
import type { BusinessInterpretation } from '../api/types'
import { useWorkbench } from '../App'
import { EmptyState, ErrorView, EvidenceTag, KeyFigure, Loading, SectionHeader } from '../components/Bits'
import { bytes, compactNumber, percent, titleCase } from '../utils/format'
import { palette } from '../theme'

const ROLE_HINTS: Record<string, string> = {
  numeric: 'Measures, currency and rate columns you can aggregate.',
  categorical: 'Grouping attributes for bars, rows and filters.',
  datetime: 'Dates and timestamps available for trends.',
  boolean: 'Two-valued flags.',
  identifier: 'Keys. Counted, never summed.',
  free_text: 'Long text, not usable as a dimension.',
  geography: 'Location attributes, usable on a map.',
  sensitive: 'Look like personal data. Masked everywhere.',
  unusable: 'Empty or otherwise unusable.',
}

export default function OverviewPage({ datasetId }: { datasetId: string }) {
  const { provider } = useWorkbench()
  const { data, isLoading, error } = useQuery({
    queryKey: ['overview', datasetId],
    queryFn: () => api.overview(datasetId),
  })
  const anomalies = useQuery({ queryKey: ['anomalies', datasetId], queryFn: () => api.anomalies(datasetId) })

  const summary = useMutation<{ interpretation: BusinessInterpretation; llm_used: boolean; provider: string }>({
    mutationFn: () => api.summary(datasetId, provider),
  })

  if (isLoading) return <Loading label="Loading the profile…" />
  if (error) return <ErrorView error={error} />
  if (!data) return <EmptyState title="Nothing loaded">Upload a file to begin.</EmptyState>

  const highCount = (anomalies.data ?? []).filter((a) => a.severity === 'high').length
  const mediumCount = (anomalies.data ?? []).filter((a) => a.severity === 'medium').length

  return (
    <Box>
      <SectionHeader
        title="Overview"
        description="Everything on this page is counted from the file itself. The narrative at the bottom is generated from these same numbers."
      />

      <Grid container spacing={2}>
        <Grid item xs={6} md={3}>
          <KeyFigure label="Records" value={data.row_count.toLocaleString()} hint={`${bytes(data.file_size_bytes)} on disk`} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KeyFigure label="Columns" value={String(data.column_count)} hint={`${bytes(data.memory_bytes)} in memory`} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KeyFigure
            label="Duplicate rows"
            value={data.duplicate_row_count.toLocaleString()}
            hint={`${percent(data.duplicate_row_percent, 2)} of rows are exact repeats`}
            tone={data.duplicate_row_percent > 0 ? 'warn' : 'ink'}
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <KeyFigure
            label="Missing cells"
            value={compactNumber(data.missing_cell_count)}
            hint={`${percent(data.missing_cell_percent, 2)} of all cells`}
            tone={data.missing_cell_percent > 5 ? 'warn' : 'ink'}
          />
        </Grid>
      </Grid>

      {data.sampled && (
        <Alert severity="warning" sx={{ mt: 2 }}>
          Column statistics come from a sample: {data.sample_method}. Record and null counts are computed over the
          whole file.
        </Alert>
      )}
      {data.ingestion_notes.length > 0 && (
        <Alert severity="info" sx={{ mt: 2 }}>
          <Stack spacing={0.25}>
            {data.ingestion_notes.map((note) => (
              <Typography key={note} variant="body2">{note}</Typography>
            ))}
          </Stack>
        </Alert>
      )}

      <Grid container spacing={2} sx={{ mt: 1 }}>
        <Grid item xs={12} md={7}>
          <Paper sx={{ p: 2, height: '100%' }}>
            <Typography variant="h3">Data quality score</Typography>
            <Stack direction="row" spacing={2} alignItems="baseline" sx={{ mt: 1 }}>
              <Typography className="figure" sx={{ fontSize: '2.6rem', fontWeight: 600, letterSpacing: '-0.03em' }}>
                {data.quality.score.toFixed(1)}
              </Typography>
              <Chip label={`Grade ${data.quality.grade}`} sx={{ backgroundColor: palette.primaryTint }} />
              <Typography variant="caption" sx={{ flex: 1 }}>{data.quality.formula}</Typography>
            </Stack>
            <Table size="small" sx={{ mt: 1.5 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Component</TableCell>
                  <TableCell align="right">Weight</TableCell>
                  <TableCell align="right">Score</TableCell>
                  <TableCell>How it is calculated</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.quality.components.map((component) => (
                  <TableRow key={component.name}>
                    <TableCell>{component.name}</TableCell>
                    <TableCell align="right" className="figure">{(component.weight * 100).toFixed(0)}%</TableCell>
                    <TableCell align="right" className="figure">{component.score.toFixed(1)}</TableCell>
                    <TableCell sx={{ color: palette.muted }}>{component.detail}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        </Grid>

        <Grid item xs={12} md={5}>
          <Paper sx={{ p: 2, height: '100%' }}>
            <Typography variant="h3">Columns by analytical role</Typography>
            <Stack spacing={1} sx={{ mt: 1.5 }}>
              {Object.entries(data.role_counts)
                .filter(([, count]) => count > 0)
                .map(([role, count]) => (
                  <Tooltip key={role} title={ROLE_HINTS[role] ?? ''}>
                    <Box>
                      <Stack direction="row" justifyContent="space-between">
                        <Typography variant="body2">{titleCase(role)}</Typography>
                        <Typography variant="body2" className="figure">{count}</Typography>
                      </Stack>
                      <LinearProgress
                        variant="determinate"
                        value={(100 * count) / data.column_count}
                        sx={{ height: 6, backgroundColor: palette.sunken }}
                      />
                    </Box>
                  </Tooltip>
                ))}
            </Stack>
            <Box sx={{ mt: 2, pt: 2, borderTop: `1px solid ${palette.line}` }}>
              <Typography variant="body2">
                <strong>{data.categorical_column_count}</strong> categorical columns hold{' '}
                <strong>{data.distinct_category_values.toLocaleString()}</strong> distinct values between them.
              </Typography>
              <Typography variant="caption">
                Two different measures that are easy to confuse: the number of columns, and the number of members
                inside them.
              </Typography>
            </Box>
            {(highCount > 0 || mediumCount > 0) && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="body2">
                  {highCount} high and {mediumCount} medium severity findings are waiting on the data profile page.
                </Typography>
              </Box>
            )}
          </Paper>
        </Grid>
      </Grid>

      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Stack direction="row" spacing={2} alignItems="flex-start">
            <Box sx={{ flex: 1 }}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="h3">Business summary</Typography>
                <EvidenceTag kind={summary.data?.llm_used ? 'suggestion' : 'inference'} />
              </Stack>
              <Typography variant="caption">
                Grounded in the computed profile above. Narrator: {summary.data?.provider ?? provider}.
              </Typography>
            </Box>
            <Button
              startIcon={<AutoAwesomeIcon fontSize="small" />}
              onClick={() => summary.mutate()}
              disabled={summary.isPending}
              variant="outlined"
            >
              {summary.data ? 'Regenerate' : 'Write the summary'}
            </Button>
          </Stack>

          {summary.isPending && <Loading label="Writing…" />}
          {summary.error && <ErrorView error={summary.error} />}

          {summary.data && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body1">{summary.data.interpretation.dataset_summary}</Typography>
              <Grid container spacing={2} sx={{ mt: 1.5 }}>
                <Grid item xs={12} md={6}>
                  <Typography variant="subtitle2">Likely grain</Typography>
                  <Typography variant="body2">{summary.data.interpretation.likely_grain}</Typography>
                  <Typography variant="subtitle2" sx={{ mt: 1.5 }}>Subject area</Typography>
                  <Typography variant="body2">{summary.data.interpretation.likely_subject_area}</Typography>
                </Grid>
                <Grid item xs={12} md={6}>
                  <Typography variant="subtitle2">Questions for the business</Typography>
                  <Stack component="ul" sx={{ pl: 2.5, m: 0 }} spacing={0.25}>
                    {summary.data.interpretation.open_questions.map((question) => (
                      <Typography component="li" variant="body2" key={question}>{question}</Typography>
                    ))}
                  </Stack>
                </Grid>
              </Grid>
              {summary.data.interpretation.data_quality_call_outs.length > 0 && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  <Stack spacing={0.25}>
                    {summary.data.interpretation.data_quality_call_outs.map((call) => (
                      <Typography key={call} variant="body2">{call}</Typography>
                    ))}
                  </Stack>
                </Alert>
              )}
            </Box>
          )}

          {!summary.data && !summary.isPending && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
              {data.business_context
                ? `Your context: “${data.business_context}”`
                : 'No business context was supplied, so the summary will describe the data structurally.'}
            </Typography>
          )}
        </CardContent>
      </Card>
    </Box>
  )
}
