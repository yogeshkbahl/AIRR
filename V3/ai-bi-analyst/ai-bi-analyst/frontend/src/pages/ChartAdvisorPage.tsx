import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert, Autocomplete, Box, Button, Chip, Grid, List, ListItemButton, ListItemText, Paper, Stack,
  TextField, Tooltip, Typography,
} from '@mui/material'
import { api } from '../api/client'
import type { ChartAdviceResponse, ChartOption } from '../api/types'
import { useWorkbench } from '../App'
import { EmptyState, ErrorView, Loading, SectionHeader, TierChip } from '../components/Bits'
import ChartCard from '../components/ChartCard'
import { useWorkspaceState } from '../hooks/useWorkspaceState'
import { canonicalSelection, isCurrentResponse, selectionFingerprint } from '../utils/selection'
import { palette } from '../theme'
import { titleCase } from '../utils/format'

/**
 * ENH-03 + ENH-05.
 *
 * There is exactly one piece of selection truth on this page: the ordered
 * `selected_column_ids` held in dataset-scoped workspace state. Badges, counts,
 * the request payload, the option list, titles and the preview are all derived
 * from it. Advice is fetched with a query keyed by the selection fingerprint, so
 * a superseded request is cancelled and a late response for an older selection
 * is discarded rather than rendered.
 */
export default function ChartAdvisorPage({ datasetId }: { datasetId: string }) {
  const { provider } = useWorkbench()
  const { chartAdvisor, patchChartAdvisor, droppedOnRestore, isLoading: stateLoading } =
    useWorkspaceState(datasetId)
  const scrollRestored = useRef(false)

  const columns = useQuery({
    queryKey: ['columns', datasetId],
    queryFn: () => api.columns(datasetId),
    staleTime: 5 * 60 * 1000,
  })

  const knownIds = useMemo(() => (columns.data ?? []).map((column) => column.name), [columns.data])
  const selection = useMemo(
    () => canonicalSelection(chartAdvisor.selected_column_ids, knownIds),
    [chartAdvisor.selected_column_ids, knownIds],
  )
  const fingerprint = selectionFingerprint(datasetId, selection.ids)

  const advice = useQuery({
    // The fingerprint is part of the key, so changing the selection starts a
    // new request and the previous one can no longer resolve into this state.
    queryKey: ['chart-advice', datasetId, fingerprint, provider],
    queryFn: ({ signal }) =>
      api.chartAdvice(
        datasetId,
        {
          columns: selection.ids,
          question: chartAdvisor.question || undefined,
          use_llm: true,
          selection_fingerprint: fingerprint,
        },
        provider,
        signal,
      ),
    enabled: selection.ids.length > 0 && !columns.isLoading,
    staleTime: 5 * 60 * 1000,
  })

  // Only accept a payload that names this dataset and this exact selection.
  const current: ChartAdviceResponse | undefined = isCurrentResponse(advice.data, datasetId, fingerprint)
    ? advice.data
    : undefined
  const stale = Boolean(advice.data && !current)

  const options = current?.options ?? []
  const active: ChartOption | undefined =
    options.find((option) => option.chart_type === chartAdvisor.active_chart_type) ?? options[0]

  // If the stored chart type is no longer valid for the current selection, drop
  // it so a preview for a deselected column cannot linger.
  useEffect(() => {
    if (!current) return
    const stored = chartAdvisor.active_chart_type
    if (stored && !options.some((option) => option.chart_type === stored)) {
      patchChartAdvisor({ active_chart_type: options[0]?.chart_type ?? null })
    }
  }, [current, options, chartAdvisor.active_chart_type, patchChartAdvisor])

  // Restore scroll position once, after the durable state has loaded.
  useEffect(() => {
    if (stateLoading || scrollRestored.current) return
    scrollRestored.current = true
    if (chartAdvisor.scroll_y > 0) window.scrollTo({ top: chartAdvisor.scroll_y })
  }, [stateLoading, chartAdvisor.scroll_y])

  useEffect(() => {
    const onScroll = () => {
      if (Math.abs(window.scrollY - chartAdvisor.scroll_y) > 80) {
        patchChartAdvisor({ scroll_y: window.scrollY })
      }
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [chartAdvisor.scroll_y, patchChartAdvisor])

  const setSelected = useCallback(
    (ids: string[]) => {
      const next = canonicalSelection(ids, knownIds)
      patchChartAdvisor({ selected_column_ids: next.ids })
    },
    [knownIds, patchChartAdvisor],
  )

  const autocompleteOptions = useMemo(
    () =>
      (columns.data ?? []).map((column) => ({
        name: column.name,
        label: column.label,
        role: column.analytical_role,
        distinct: column.distinct_count,
      })),
    [columns.data],
  )
  const byName = useMemo(
    () => new Map(autocompleteOptions.map((option) => [option.name, option])),
    [autocompleteOptions],
  )
  // Value order follows the canonical selection, not the options array.
  const autocompleteValue = selection.ids
    .map((id) => byName.get(id))
    .filter((option): option is NonNullable<typeof option> => Boolean(option))

  if (columns.isLoading) return <Loading label="Loading columns…" />
  if (columns.error) return <ErrorView error={columns.error} />

  return (
    <Box>
      <SectionHeader
        title="Chart advisor"
        description="Pick the columns you care about. The backend works out which charts are valid for those roles and cardinalities, and only then asks the narrator to rank them."
      />

      {droppedOnRestore.length > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {droppedOnRestore.join(', ')} {droppedOnRestore.length === 1 ? 'is' : 'are'} no longer in this dataset,
          so it was removed from your saved selection.
        </Alert>
      )}

      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack spacing={2}>
          <Autocomplete
            multiple
            options={autocompleteOptions}
            getOptionLabel={(option) => option.label}
            value={autocompleteValue}
            onChange={(_, value) => setSelected(value.map((option) => option.name))}
            isOptionEqualToValue={(a, b) => a.name === b.name}
            renderOption={(props, option) => (
              <li {...props} key={option.name}>
                <Stack>
                  <Typography variant="body2">{option.label}</Typography>
                  <Typography variant="caption">
                    {titleCase(option.role)} · {option.distinct.toLocaleString()} distinct
                  </Typography>
                </Stack>
              </li>
            )}
            renderInput={(params) => (
              <TextField
                {...params}
                label={`Columns (${selection.ids.length} selected, up to 8)`}
                placeholder={selection.ids.length ? '' : 'Start typing a column name'}
                inputProps={{ ...params.inputProps, 'data-testid': 'column-select' }}
              />
            )}
          />
          <TextField
            label="What do you want the chart to answer? (optional)"
            value={chartAdvisor.question}
            onChange={(event) => patchChartAdvisor({ question: event.target.value })}
            placeholder="Which channel is losing margin, and since when?"
            fullWidth
          />
          <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
            {selection.ids.length > 0 && (
              <Button onClick={() => setSelected([])}>Clear selection</Button>
            )}
            <Button onClick={() => advice.refetch()} disabled={!selection.ids.length || advice.isFetching}>
              {advice.isFetching ? 'Checking compatibility…' : 'Re-check'}
            </Button>
            <Typography variant="caption" data-testid="selection-fingerprint">
              Selection: {selection.ids.length ? selection.ids.join(' › ') : 'none'}
            </Typography>
          </Stack>
        </Stack>
      </Paper>

      {selection.dropped.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Ignored {selection.dropped.length} column(s) that are not in this dataset: {selection.dropped.join(', ')}.
        </Alert>
      )}
      {selection.truncated && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Only the first 8 selected columns are evaluated.
        </Alert>
      )}

      {advice.error ? <ErrorView error={advice.error} /> : null}
      {advice.isFetching && !current && <Loading label="Evaluating chart rules…" />}
      {stale && !advice.isFetching && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Those results were for a different selection and have been discarded. Re-checking the current one.
        </Alert>
      )}

      {selection.ids.length === 0 && (
        <EmptyState title="Nothing selected yet">
          Try one measure plus one date for a trend, two measures for a scatter, or a measure plus two dimensions
          for a heatmap.
        </EmptyState>
      )}

      {current && (
        <Grid container spacing={2}>
          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h3">Valid for this selection</Typography>
              <Typography variant="caption" data-testid="selection-summary">
                {current.selection_summary}
              </Typography>
              <Stack direction="row" spacing={0.5} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
                {current.selected_columns.map((id) => (
                  <Chip key={id} size="small" label={byName.get(id)?.label ?? id} variant="outlined" />
                ))}
              </Stack>
              <Chip
                size="small"
                sx={{ mt: 1 }}
                variant={current.llm_used ? 'filled' : 'outlined'}
                label={current.llm_used ? 'Ranked by the narrator' : 'Ranked by rule order'}
              />
              {current.llm_note && (
                <Alert severity="info" sx={{ mt: 1 }}>
                  {current.llm_note}
                </Alert>
              )}
              <List dense sx={{ mt: 1 }} data-testid="valid-options">
                {current.options.map((option) => (
                  <ListItemButton
                    key={option.chart_type}
                    selected={active?.chart_type === option.chart_type}
                    onClick={() => patchChartAdvisor({ active_chart_type: option.chart_type })}
                    sx={{ alignItems: 'flex-start', '&.Mui-selected': { backgroundColor: palette.primaryTint } }}
                  >
                    <ListItemText
                      primary={
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>
                            {option.rank}. {option.chart_type.replace(/_/g, ' ')}
                          </Typography>
                          <TierChip tier={option.tier} />
                        </Stack>
                      }
                      secondary={option.rationale}
                      secondaryTypographyProps={{ fontSize: '0.75rem' }}
                    />
                  </ListItemButton>
                ))}
              </List>
            </Paper>

            {current.rejected.length > 0 && (
              <Paper sx={{ p: 2, mt: 2 }}>
                <Typography variant="h4">Not valid, and why</Typography>
                <Stack spacing={1} sx={{ mt: 1 }}>
                  {current.rejected.slice(0, 12).map((option) => (
                    <Tooltip key={option.chart_type} title={option.prerequisites.join(' · ')}>
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>
                          {option.chart_type.replace(/_/g, ' ')}
                        </Typography>
                        <Typography variant="caption">{option.blockers[0]}</Typography>
                      </Box>
                    </Tooltip>
                  ))}
                </Stack>
              </Paper>
            )}
          </Grid>

          <Grid item xs={12} md={8}>
            {active?.spec ? (
              <Stack spacing={2}>
                <ChartCard
                  datasetId={datasetId}
                  spec={active.spec}
                  height={360}
                  subtitle={active.rationale}
                />
                <Paper sx={{ p: 2 }}>
                  <Typography variant="h4">Before you publish this</Typography>
                  <Typography variant="subtitle2" sx={{ mt: 1 }}>
                    Prerequisites
                  </Typography>
                  <Stack component="ul" sx={{ pl: 2.5, m: 0 }}>
                    {active.prerequisites.map((item) => (
                      <Typography component="li" variant="body2" key={item}>
                        {item}
                      </Typography>
                    ))}
                  </Stack>
                  <Typography variant="subtitle2" sx={{ mt: 1 }}>
                    Avoid when
                  </Typography>
                  <Stack component="ul" sx={{ pl: 2.5, m: 0 }}>
                    {active.avoid_when.map((item) => (
                      <Typography component="li" variant="body2" key={item}>
                        {item}
                      </Typography>
                    ))}
                  </Stack>
                </Paper>
              </Stack>
            ) : (
              <EmptyState title="Pick a chart on the left">
                Every option in that list has already been checked against your columns.
              </EmptyState>
            )}
          </Grid>
        </Grid>
      )}
    </Box>
  )
}
