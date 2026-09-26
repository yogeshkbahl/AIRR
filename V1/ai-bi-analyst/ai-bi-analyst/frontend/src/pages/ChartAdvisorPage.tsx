import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert, Autocomplete, Box, Button, Chip, Grid, List, ListItemButton, ListItemText, Paper, Stack,
  TextField, Tooltip, Typography,
} from '@mui/material'
import { api } from '../api/client'
import type { ChartAdviceResponse, ChartOption } from '../api/types'
import { useWorkbench } from '../App'
import { EmptyState, ErrorView, Loading, SectionHeader, TierChip } from '../components/Bits'
import ChartCard from '../components/ChartCard'
import { palette } from '../theme'
import { titleCase } from '../utils/format'

export default function ChartAdvisorPage({ datasetId }: { datasetId: string }) {
  const { provider } = useWorkbench()
  const [selected, setSelected] = useState<string[]>([])
  const [question, setQuestion] = useState('')
  const [active, setActive] = useState<ChartOption | null>(null)

  const columns = useQuery({ queryKey: ['columns', datasetId], queryFn: () => api.columns(datasetId) })

  const advise = useMutation<ChartAdviceResponse>({
    mutationFn: () =>
      api.chartAdvice(datasetId, { columns: selected, question: question || undefined, use_llm: true }, provider),
    onSuccess: (result) => setActive(result.options[0] ?? null),
  })

  const options = useMemo(
    () =>
      (columns.data ?? []).map((column) => ({
        name: column.name,
        label: column.label,
        role: column.analytical_role,
        distinct: column.distinct_count,
      })),
    [columns.data],
  )

  return (
    <Box>
      <SectionHeader
        title="Chart advisor"
        description="Pick the columns you care about. The backend works out which charts are actually valid for those roles and cardinalities, and only then asks the narrator to rank them."
      />

      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack spacing={2}>
          <Autocomplete
            multiple
            options={options}
            getOptionLabel={(option) => option.label}
            value={options.filter((option) => selected.includes(option.name))}
            onChange={(_, value) => setSelected(value.slice(0, 8).map((option) => option.name))}
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
                label="Columns (up to 8)"
                placeholder={selected.length ? '' : 'Start typing a column name'}
              />
            )}
          />
          <TextField
            label="What do you want the chart to answer? (optional)"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Which channel is losing margin, and since when?"
            fullWidth
          />
          <Stack direction="row" spacing={2} alignItems="center">
            <Button
              variant="contained"
              disabled={!selected.length || advise.isPending}
              onClick={() => advise.mutate()}
            >
              {advise.isPending ? 'Checking compatibility…' : 'Show valid charts'}
            </Button>
            {selected.length > 0 && <Button onClick={() => setSelected([])}>Clear selection</Button>}
          </Stack>
        </Stack>
      </Paper>

      {advise.error && <ErrorView error={advise.error} />}
      {advise.isPending && <Loading label="Evaluating chart rules…" />}

      {!advise.data && !advise.isPending && (
        <EmptyState title="Nothing selected yet">
          Try one measure plus one date for a trend, two measures for a scatter, or a measure plus two dimensions
          for a heatmap.
        </EmptyState>
      )}

      {advise.data && (
        <Grid container spacing={2}>
          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h3">Valid for this selection</Typography>
              <Typography variant="caption">{advise.data.selection_summary}</Typography>
              {advise.data.llm_used ? (
                <Chip size="small" sx={{ mt: 1 }} label="Ranked by the narrator" />
              ) : (
                <Chip size="small" variant="outlined" sx={{ mt: 1 }} label="Ranked by rule order" />
              )}
              {advise.data.llm_note && (
                <Alert severity="info" sx={{ mt: 1 }}>{advise.data.llm_note}</Alert>
              )}
              <List dense sx={{ mt: 1 }}>
                {advise.data.options.map((option) => (
                  <ListItemButton
                    key={option.chart_type}
                    selected={active?.chart_type === option.chart_type}
                    onClick={() => setActive(option)}
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

            {advise.data.rejected.length > 0 && (
              <Paper sx={{ p: 2, mt: 2 }}>
                <Typography variant="h4">Not valid, and why</Typography>
                <Stack spacing={1} sx={{ mt: 1 }}>
                  {advise.data.rejected.slice(0, 12).map((option) => (
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
                  defaultOpenSpec={false}
                />
                <Paper sx={{ p: 2 }}>
                  <Typography variant="h4">Before you publish this</Typography>
                  <Typography variant="subtitle2" sx={{ mt: 1 }}>Prerequisites</Typography>
                  <Stack component="ul" sx={{ pl: 2.5, m: 0 }}>
                    {active.prerequisites.map((item) => (
                      <Typography component="li" variant="body2" key={item}>{item}</Typography>
                    ))}
                  </Stack>
                  <Typography variant="subtitle2" sx={{ mt: 1 }}>Avoid when</Typography>
                  <Stack component="ul" sx={{ pl: 2.5, m: 0 }}>
                    {active.avoid_when.map((item) => (
                      <Typography component="li" variant="body2" key={item}>{item}</Typography>
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
