import { useCallback, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert, Autocomplete, Box, Button, Chip, Collapse, Grid, Paper, Stack, Table, TableBody, TableCell,
  TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import SendIcon from '@mui/icons-material/Send'
import ReplayIcon from '@mui/icons-material/Replay'
import { api } from '../api/client'
import type { QuestionResponse, QuickAsk } from '../api/types'
import { useWorkbench } from '../App'
import { ErrorView, EvidenceTag, Loading, ResultTable, SectionHeader } from '../components/Bits'
import ChartCard from '../components/ChartCard'
import { useStoryboard } from '../hooks/useStoryboard'
import { useWorkspaceState } from '../hooks/useWorkspaceState'
import { buildAskPayload, isSubmittable, newRequestToken } from '../utils/askPayload'
import { palette } from '../theme'

/**
 * ENH-04 — Quick Ask items come from the backend, generated from this dataset's
 * schema and semantic roles. Each carries a stable id and a structured intent,
 * and submitting one sends that id so the server runs the stored intent instead
 * of interpreting the label text. Every state the request can end in is
 * rendered, a failure keeps a retry action, double submissions are blocked, and
 * a late answer for an older question is discarded.
 */

const CATEGORY_LABEL: Record<QuickAsk['category'], string> = {
  kpi: 'KPIs',
  comparison: 'Compare',
  trend: 'Trend',
  ranking: 'Ranking',
  distribution: 'Spread',
  relationship: 'Drivers',
}

interface Submission {
  question: string
  quickAskId: string | null
  token: string
}

export default function AskDataPage({ datasetId }: { datasetId: string }) {
  const { provider } = useWorkbench()
  const { state: workspace, patch } = useWorkspaceState(datasetId)
  const [question, setQuestion] = useState(workspace?.ask.last_question ?? '')
  const [columns, setColumns] = useState<string[]>([])
  const [showSql, setShowSql] = useState(false)
  const [lastSubmission, setLastSubmission] = useState<Submission | null>(null)
  const latestToken = useRef<string>('')
  const { addInsight, isSaving } = useStoryboard(datasetId)
  const [answerAdded, setAnswerAdded] = useState<string | null>(null)

  const profile = useQuery({
    queryKey: ['columns', datasetId],
    queryFn: () => api.columns(datasetId),
    staleTime: 5 * 60 * 1000,
  })

  const quickAsks = useQuery({
    queryKey: ['quick-asks', datasetId],
    queryFn: () => api.quickAsks(datasetId),
    staleTime: 5 * 60 * 1000,
  })

  const ask = useMutation<QuestionResponse, unknown, Submission>({
    mutationFn: (submission) =>
      api.ask(
        datasetId,
        buildAskPayload({
          question: submission.question,
          columns,
          quickAskId: submission.quickAskId,
          clientRequestId: submission.token,
        }),
        provider,
      ),
  })

  const submit = useCallback(
    (text: string, quickAskId: string | null = null) => {
      const trimmed = text.trim()
      if (!isSubmittable(trimmed) || ask.isPending) return // blocks a double click
      const token = newRequestToken()
      latestToken.current = token
      const submission = { question: trimmed, quickAskId, token }
      setQuestion(trimmed)
      setLastSubmission(submission)
      patch({ ask: { last_question: trimmed, last_quick_ask_id: quickAskId } })
      ask.mutate(submission)
    },
    [ask, patch],
  )

  // A response is only rendered when it answers the most recent submission.
  const result = useMemo(() => {
    const data = ask.data
    if (!data) return undefined
    if (data.dataset_id !== datasetId) return undefined
    if (data.client_request_id && data.client_request_id !== latestToken.current) return undefined
    return data
  }, [ask.data, datasetId])

  const items = quickAsks.data?.items ?? []

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
          <Button
            variant="contained"
            startIcon={<SendIcon fontSize="small" />}
            disabled={ask.isPending || !isSubmittable(question)}
            onClick={() => submit(question)}
            sx={{ alignSelf: 'flex-start' }}
          >
            {ask.isPending ? 'Running the plan…' : 'Ask'}
          </Button>

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
              Quick ask
            </Typography>
            {quickAsks.isLoading && <Loading label="Building suggestions from your columns…" />}
            {quickAsks.error ? (
              <ErrorView
                error={quickAsks.error}
                action={
                  <Button size="small" onClick={() => quickAsks.refetch()}>
                    Retry
                  </Button>
                }
              />
            ) : null}
            {!quickAsks.isLoading && items.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                {quickAsks.data?.note ?? 'No suggestion fits this dataset.'}
              </Typography>
            )}
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap data-testid="quick-asks">
              {items.map((item) => (
                <Tooltip key={item.id} title={`${item.question} · uses ${item.required_columns.join(', ')}`}>
                  <Chip
                    label={`${CATEGORY_LABEL[item.category]}: ${item.label}`}
                    variant="outlined"
                    size="small"
                    // Chips with onClick are focusable and respond to Enter and
                    // Space, so every suggestion is keyboard operable.
                    onClick={() => submit(item.question, item.id)}
                    disabled={ask.isPending}
                    data-quick-ask-id={item.id}
                    data-category={item.category}
                  />
                </Tooltip>
              ))}
            </Stack>
          </Box>
        </Stack>
      </Paper>

      {ask.isPending && <Loading label="Building the plan and executing it…" />}

      {ask.error && lastSubmission ? (
        <Box sx={{ mb: 2 }}>
          <ErrorView
            error={ask.error}
            action={
              <Button
                size="small"
                startIcon={<ReplayIcon fontSize="small" />}
                onClick={() => submit(lastSubmission.question, lastSubmission.quickAskId)}
              >
                Retry
              </Button>
            }
          />
        </Box>
      ) : null}

      {result && (
        <Grid container spacing={2}>
          <Grid item xs={12} md={7}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="h2">{result.answer.headline}</Typography>
                <EvidenceTag kind={result.answer.evidence_kind} />
                {result.state !== 'answered' && (
                  <Chip
                    size="small"
                    label={result.state === 'clarification_required' ? 'Needs clarification' : 'No rows matched'}
                    sx={{ backgroundColor: palette.severity.medium, color: '#fff' }}
                  />
                )}
              </Stack>
              <Typography variant="body1" sx={{ mt: 1 }}>
                {result.answer.summary}
              </Typography>

              {result.state === 'empty_result' && (
                <Alert severity="info" sx={{ mt: 2 }}>
                  The plan ran but returned no rows. Widen the filters or pick a coarser grain.
                </Alert>
              )}

              <Typography variant="h4" sx={{ mt: 2 }}>
                Evidence
              </Typography>
              <Stack component="ul" sx={{ pl: 2.5, m: 0 }} spacing={0.25}>
                {result.answer.evidence.map((item) => (
                  <Typography component="li" variant="body2" key={item}>
                    {item}
                  </Typography>
                ))}
              </Stack>

              {result.answer.caveats.length > 0 && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  <Stack spacing={0.25}>
                    {result.answer.caveats.map((caveat) => (
                      <Typography key={caveat} variant="body2">
                        {caveat}
                      </Typography>
                    ))}
                  </Stack>
                </Alert>
              )}

              {result.answer.follow_up_questions.length > 0 && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="h4">Worth asking next</Typography>
                  <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
                    {result.answer.follow_up_questions.map((follow) => (
                      <Chip
                        key={follow}
                        size="small"
                        variant="outlined"
                        label={follow}
                        onClick={() => submit(follow)}
                        disabled={ask.isPending}
                      />
                    ))}
                  </Stack>
                </Box>
              )}

              <Button
                sx={{ mt: 2 }}
                variant="outlined"
                disabled={isSaving || answerAdded === (result.client_request_id ?? result.answer.headline)}
                onClick={async () => {
                  await addInsight(
                    result.answer.headline,
                    `${result.answer.summary} ${result.answer.evidence.join(' ')}`,
                    result.answer.evidence_kind,
                  )
                  setAnswerAdded(result.client_request_id ?? result.answer.headline)
                }}
              >
                {answerAdded === (result.client_request_id ?? result.answer.headline) ? 'Answer is on the storyboard' : 'Add answer to storyboard'}
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
                {result.quick_ask_id
                  ? 'Planned from a structured Quick Ask intent'
                  : result.llm_used
                    ? `Planned by ${result.provider}`
                    : 'Planned by rules'}{' '}
                · prompt {result.prompt_version}
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
                      result.plan.filters
                        .map((filter) => `${filter.column} ${filter.op} ${String(filter.value)}`)
                        .join(', ') || 'none',
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
                      <Typography key={assumption} variant="body2">
                        {assumption}
                      </Typography>
                    ))}
                  </Stack>
                </Alert>
              )}
              {result.plan.clarification_needed && (
                <Alert severity="warning" sx={{ mt: 1 }}>
                  {result.plan.clarification_needed}
                </Alert>
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
                        mt: 1,
                        p: 1.5,
                        fontSize: '0.7rem',
                        whiteSpace: 'pre-wrap',
                        backgroundColor: palette.sunken,
                        border: `1px solid ${palette.line}`,
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
