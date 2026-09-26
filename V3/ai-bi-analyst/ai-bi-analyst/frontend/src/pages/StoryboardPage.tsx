import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, Card, CardContent, Chip, Grid, IconButton, Paper, Stack, TextField, Tooltip,
  Typography,
} from '@mui/material'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked'
import DownloadIcon from '@mui/icons-material/Download'
import PrintIcon from '@mui/icons-material/Print'
import { api } from '../api/client'
import { useStoryboard } from '../hooks/useStoryboard'
import { EmptyState, ErrorView, EvidenceTag, Loading, SectionHeader } from '../components/Bits'
import ChartCard from '../components/ChartCard'
import { palette } from '../theme'

const CHECKLIST: Record<string, string> = {
  three_to_six_kpis: 'Three to six KPI cards',
  primary_time_trend: 'One primary time trend',
  variance_or_driver: 'One variance or driver view',
  categorical_comparison: 'One categorical comparison',
  anomaly_or_risk_view: 'One anomaly or risk view',
  detail_table: 'One detail table for drill-through',
}

export default function StoryboardPage({ datasetId }: { datasetId: string }) {
  const { board, isLoading, error, update, removeItem, moveItem, isSaving, saveError } = useStoryboard(datasetId)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (board) {
      setTitle(board.title)
      setDescription(board.description)
    }
  }, [board?.dataset_id, board?.updated_at]) // eslint-disable-line react-hooks/exhaustive-deps

  if (isLoading) return <Loading label="Loading the storyboard…" />
  if (error) return <ErrorView error={error} />
  if (!board) return <EmptyState title="No storyboard yet">Pin a chart from any page to start one.</EmptyState>

  const done = Object.values(board.completeness).filter(Boolean).length
  const total = Object.keys(board.completeness).length || 1

  return (
    <Box>
      <SectionHeader
        title="Storyboard"
        description="A blueprint for the dashboard you are going to build in Oracle Analytics, Power BI, Tableau or your own front end. Every pinned chart re-runs against the file, so the numbers in the export are current."
        action={
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined"
              startIcon={<DownloadIcon fontSize="small" />}
              href={api.exportJsonUrl(datasetId)}
            >
              Export definition
            </Button>
            <Button
              variant="contained"
              startIcon={<PrintIcon fontSize="small" />}
              href={api.exportHtmlUrl(datasetId)}
              target="_blank"
              rel="noreferrer"
            >
              Printable view
            </Button>
          </Stack>
        }
      />

      <Grid container spacing={2}>
        <Grid item xs={12} md={8}>
          <Paper sx={{ p: 2 }}>
            <Stack spacing={2}>
              <TextField
                label="Dashboard title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                onBlur={() => title !== board.title && update({ title })}
                fullWidth
              />
              <TextField
                label="What decision does this page support?"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                onBlur={() => description !== board.description && update({ description })}
                fullWidth
                multiline
                minRows={2}
              />
            </Stack>
          </Paper>
        </Grid>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2, height: '100%' }}>
            <Typography variant="h3">Executive page checklist</Typography>
            <Typography variant="caption">
              {done} of {total} elements present
            </Typography>
            <Stack spacing={0.75} sx={{ mt: 1.5 }}>
              {Object.entries(CHECKLIST).map(([key, label]) => {
                const satisfied = board.completeness[key]
                return (
                  <Stack key={key} direction="row" spacing={1} alignItems="center">
                    {satisfied ? (
                      <CheckCircleOutlineIcon fontSize="small" sx={{ color: palette.evidence.inference }} />
                    ) : (
                      <RadioButtonUncheckedIcon fontSize="small" sx={{ color: palette.muted }} />
                    )}
                    <Typography variant="body2" sx={{ color: satisfied ? palette.ink : palette.muted }}>
                      {label}
                    </Typography>
                  </Stack>
                )
              })}
            </Stack>
          </Paper>
        </Grid>
      </Grid>

      {saveError && (
        <Box sx={{ mt: 2 }}>
          <ErrorView error={saveError} />
        </Box>
      )}

      <Box sx={{ mt: 3 }}>
        {board.items.length === 0 ? (
          <EmptyState title="The storyboard is empty">
            Pin a chart from the chart advisor or a finding from the data profile. Aim for three to six KPI cards,
            a trend, a driver view, a comparison, a risk view and one detail table.
          </EmptyState>
        ) : (
          <Grid container spacing={2}>
            {board.items.map((item, index) => (
              <Grid item xs={12} md={item.kind === 'insight' ? 6 : 6} key={item.id}>
                {item.spec ? (
                  <Box sx={{ position: 'relative' }}>
                    <ChartCard datasetId={datasetId} spec={item.spec} height={260} pinnable={false} />
                    <Stack direction="row" spacing={0.25} sx={{ position: 'absolute', top: 8, right: 8 }}>
                      <ItemControls
                        index={index}
                        count={board.items.length}
                        onUp={() => moveItem(item.id, -1)}
                        onDown={() => moveItem(item.id, 1)}
                        onRemove={() => removeItem(item.id)}
                        busy={isSaving}
                      />
                    </Stack>
                  </Box>
                ) : (
                  <Card sx={{ height: '100%' }}>
                    <CardContent>
                      <Stack direction="row" spacing={1} alignItems="flex-start">
                        <Box sx={{ flex: 1 }}>
                          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                            <Typography variant="h3">{item.title}</Typography>
                            <EvidenceTag kind={item.evidence_kind} />
                            <Chip size="small" variant="outlined" label={item.kind} />
                          </Stack>
                          <Typography variant="body2" sx={{ mt: 1 }}>{item.text}</Typography>
                        </Box>
                        <ItemControls
                          index={index}
                          count={board.items.length}
                          onUp={() => moveItem(item.id, -1)}
                          onDown={() => moveItem(item.id, 1)}
                          onRemove={() => removeItem(item.id)}
                          busy={isSaving}
                        />
                      </Stack>
                    </CardContent>
                  </Card>
                )}
              </Grid>
            ))}
          </Grid>
        )}
      </Box>

      <Alert severity="info" sx={{ mt: 3 }}>
        The exported definition carries the chart specs, the semantic layer and the provenance of every item.
        Deploying it into an Oracle catalog or RPD is deliberately out of scope here; treat the export as the
        blueprint your BI developer implements.
      </Alert>
    </Box>
  )
}

function ItemControls({
  index, count, onUp, onDown, onRemove, busy,
}: {
  index: number
  count: number
  onUp: () => void
  onDown: () => void
  onRemove: () => void
  busy: boolean
}) {
  return (
    <Stack direction="row" spacing={0.25}>
      <Tooltip title="Move earlier">
        <IconButton size="small" disabled={index === 0 || busy} onClick={onUp} aria-label="Move earlier">
          <ArrowUpwardIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Move later">
        <IconButton size="small" disabled={index === count - 1 || busy} onClick={onDown} aria-label="Move later">
          <ArrowDownwardIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Remove from the storyboard">
        <IconButton size="small" disabled={busy} onClick={onRemove} aria-label="Remove item">
          <DeleteOutlineIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Stack>
  )
}
