import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert, Autocomplete, Box, Button, Chip, Collapse, Grid, Paper, Stack, Table, TableBody, TableCell,
  TableRow, TextField, Typography,
} from '@mui/material'
import SendIcon from '@mui/icons-material/Send'
import { api } from '../api/client'
import type { QuestionResponse } from '../api/types'
import { useWorkbench } from '../App'
import { ErrorView, EvidenceTag, Loading, ResultTable, SectionHeader } from '../components/Bits'
import ChartCard from '../components/ChartCard'
import { useStoryboard } from '../hooks/useStoryboard'
import { palette } from '../theme'

const STARTERS = [
  'What are the best executive KPIs for this dataset?',
  'Compare revenue and margin by region over time',
  'Which factors appear most associated with churn?',
  'Top 10 products by revenue',
  'How has the monthly trend changed this year?',
]

export default function AskDataPage({ datasetId }: { datasetId: string }) {
  const { provider } = useWorkbench()
  const [question, setQuestion] = useState('')
  const [columns, setColumns] = useState<string[]>([])
  const [showSql, setShowSql] = useState(false)
  const { addInsight } = useStoryboard(datasetId)

  const profile = useQuery({ queryKey: ['columns', datasetId], queryFn: () => api.columns(datasetId) })

  const ask = useMutation<QuestionResponse, unknown, string>({
    mutationFn: (text: string) => api.ask(datasetId, { question: text, columns, use_llm: true }, provider),
  })

  const submit = (text: string) => {
    const trimmed = text.trim()
    if (trimmed.length < 3) return
    setQuestion(trimmed)
    ask.mutate(trimmed)
  }

  const result = ask.data

  return (
    <Box>
      <SectionHeader
        title="Ask the data"
        description="Your question becomes an analysis plan you can read, the plan runs as a parameterised query, and only then does the narrator put the result into words. No free-form SQL is ever generated or executed."
      />

      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack spacing={2}>
          <TextField
            label="Your question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit(question)
              }
            }}
            fullWidth
            multiline
            minRows={2}
          />
          <Autocomplete
            multiple
            size="small"
            options={(profile.data ?? []).map((column) => column.name)}
            value={columns}
            onChange={(_, value) => setColumns(value.slice(0, 8))}
            renderInput={(params) => (
              <TextField {...params} label="Columns to prefer (optional)" placeholder="Narrow the plan" />
            )}
          />
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <Button
              variant="contained"
              startIcon={<SendIcon fontSize="small" />}
              disabled={ask.isPending || question.trim().length < 3}
              onClick={() => submit(question)}
            >
              {ask.isPending ? 'Running the plan…' : 'Ask'}
            </Button>
            {STARTERS.map((starter) => (
              <Chip key={starter} size="small" variant="outlined" label={starter} onClick={() => submit(starter)} />
            ))}
          </Stack>
        </Stack>
      </Paper>

      {ask.isPending && <Loading label="Building the plan and executing it…" />}
      {ask.error ? <ErrorView error={ask.error} /> : null}

      {result && (
        <Grid container spacing={2}>
          <Grid item xs={12} md={7}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="h2">{result.answer.headline}</Typography>
                <EvidenceTag kind={result.answer.evidence_kind} />
              </Stack>
              <Typography variant="body1" sx={{ mt: 1 }}>{result.answer.summary}</Typography>

              <Typography variant="h4" sx={{ mt: 2 }}>Evidence</Typography>
              <Stack component="ul" sx={{ pl: 2.5, m: 0 }} spacing={0.25}>
                {result.answer.evidence.map((item) => (
                  <Typography component="li" variant="body2" key={item}>{item}</Typography>
                ))}
              </Stack>

              {result.answer.caveats.length > 0 && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  <Stack spacing={0.25}>
                    {result.answer.caveats.map((caveat) => (
                      <Typography key={caveat} variant="body2">{caveat}</Typography>
                    ))}
                  </Stack>
                </Alert>
              )}

              {result.answer.follow_up_questions.length > 0 && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="h4">Worth asking next</Typography>
                  <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
                    {result.answer.follow_up_questions.map((follow) => (
                      <Chip key={follow} size="small" variant="outlined" label={follow} onClick={() => submit(follow)} />
                    ))}
                  </Stack>
                </Box>
              )}

              <Button
                sx={{ mt: 2 }}
                onClick={() =>
                  addInsight(
                    result.answer.headline,
                    `${result.answer.summary} ${result.answer.evidence.join(' ')}`,
                    result.answer.evidence_kind,
                  )
                }
              >
                Add this answer to the storyboard
              </Button>
            </Paper>

            {result.chart && (
              <Box sx={{ mt: 2 }}>
                <ChartCard datasetId={datasetId} spec={result.chart} height={320} />
              </Box>
            )}
          </Grid>

          <Grid item xs={12} md={5}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h3">The plan that ran</Typography>
              <Typography variant="caption">
                {result.llm_used ? `Planned by ${result.provider}` : 'Planned by rules'} · prompt{' '}
                {result.prompt_version}
              </Typography>
              <Table size="small" sx={{ mt: 1 }}>
                <TableBody>
                  {[
                    ['Intent', result.plan.intent],
                    [
                      'Metrics',
                      result.plan.metrics.map((metric) => `${metric.aggregation}(${metric.column})`).join(', ') ||
                        'row count',
                    ],
                    ['Dimensions', result.plan.dimensions.join(', ') || '—'],
                    [
                      'Time',
                      result.plan.time_dimension
                        ? `${result.plan.time_dimension} at ${result.plan.time_grain} grain`
                        : '—',
                    ],
                    [
                      'Filters',
                      result.plan.filters.map((filter) => `${filter.column} ${filter.op} ${String(filter.value)}`).join(', ') ||
                        'none',
                    ],
                    ['Row limit', String(result.plan.limit)],
                  ].map(([label, value]) => (
                    <TableRow key={label}>
                      <TableCell sx={{ border: 0, py: 0.3, width: 110, verticalAlign: 'top' }}>{label}</TableCell>
                      <TableCell sx={{ border: 0, py: 0.3 }}>{String(value)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {result.plan.assumptions.length > 0 && (
                <Alert severity="info" sx={{ mt: 1 }}>
                  <Stack spacing={0.25}>
                    {result.plan.assumptions.map((assumption) => (
                      <Typography key={assumption} variant="body2">{assumption}</Typography>
                    ))}
                  </Stack>
                </Alert>
              )}
              {result.plan.clarification_needed && (
                <Alert severity="warning" sx={{ mt: 1 }}>{result.plan.clarification_needed}</Alert>
              )}

              {result.result && (
                <>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 2 }}>
                    <Typography variant="h4">Computed rows</Typography>
                    <Typography variant="caption">
                      {result.result.row_count.toLocaleString()} rows in {result.result.executed_ms} ms
                    </Typography>
                  </Stack>
                  <Box sx={{ mt: 1 }}>
                    <ResultTable columns={result.result.columns} rows={result.result.rows} maxHeight={260} />
                  </Box>
                  <Button size="small" sx={{ mt: 1 }} onClick={() => setShowSql((value) => !value)}>
                    {showSql ? 'Hide the query' : 'Show the query that ran'}
                  </Button>
                  <Collapse in={showSql}>
                    <Box
                      component="pre"
                      className="figure"
                      sx={{
                        mt: 1, p: 1.5, fontSize: '0.7rem', whiteSpace: 'pre-wrap',
                        backgroundColor: palette.sunken, border: `1px solid ${palette.line}`,
                      }}
                    >
                      {result.result.sql_like}
                    </Box>
                    <Typography variant="caption">
                      Compiled from the plan above with an allow-list of aggregate functions. Filter values are
                      bound as parameters, never interpolated.
                    </Typography>
                  </Collapse>
                </>
              )}
            </Paper>
          </Grid>
        </Grid>
      )}
    </Box>
  )
}
