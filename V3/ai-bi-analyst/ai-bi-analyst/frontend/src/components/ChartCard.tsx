import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Box, Button, Card, CardContent, Chip, Collapse, Divider, Stack, Tooltip, Typography } from '@mui/material'
import PushPinOutlinedIcon from '@mui/icons-material/PushPinOutlined'
import CodeIcon from '@mui/icons-material/Code'
import { api } from '../api/client'
import type { ChartSpec } from '../api/types'
import { useStoryboard } from '../hooks/useStoryboard'
import ChartRenderer from './ChartRenderer'
import { ErrorView, EvidenceTag, Loading } from './Bits'
import { palette } from '../theme'

interface Props {
  datasetId: string
  spec: ChartSpec
  height?: number
  subtitle?: string
  pinnable?: boolean
  defaultOpenSpec?: boolean
}

export default function ChartCard({
  datasetId, spec, height = 300, subtitle, pinnable = true, defaultOpenSpec = false,
}: Props) {
  const [showSpec, setShowSpec] = useState(defaultOpenSpec)
  const [pinned, setPinned] = useState(false)
  const { addChart, isSaving } = useStoryboard(datasetId)
  // A new spec is a different chart, so it has not been added yet.
  useEffect(() => setPinned(false), [spec])

  const { data, isLoading, error } = useQuery({
    queryKey: ['chart-data', datasetId, spec],
    queryFn: () => api.chartData(datasetId, spec),
  })

  return (
    <Card sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <CardContent sx={{ pb: 1 }}>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="h3" sx={{ mb: 0.25 }}>{spec.title}</Typography>
            <Typography variant="caption">
              {spec.chart_type.replace(/_/g, ' ')}
              {spec.aggregation !== 'none' && ` · ${spec.aggregation}`}
              {data?.truncated && ` · capped at ${spec.limit} rows`}
            </Typography>
            {subtitle && (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>{subtitle}</Typography>
            )}
          </Box>
          <Stack direction="row" spacing={0.5} alignItems="center">
            <EvidenceTag kind="fact" />
            {pinnable && (
              <Tooltip title={pinned ? 'Already on the storyboard' : 'Add to the storyboard'}>
                <Button
                  size="small"
                  startIcon={<PushPinOutlinedIcon fontSize="small" />}
                  disabled={pinned || isSaving || !data}
                  onClick={async () => {
                    if (!data) return
                    await addChart(data.spec)
                    setPinned(true)
                  }}
                >
                  {pinned ? 'On the storyboard' : 'Add to storyboard'}
                </Button>
              </Tooltip>
            )}
          </Stack>
        </Stack>
      </CardContent>

      <Box sx={{ px: 2, pb: 1, flex: 1, minWidth: 0 }}>
        {isLoading && <Loading label="Running the query…" />}
        {error && <ErrorView error={error} />}
        {data && <ChartRenderer spec={data.spec} columns={data.columns} rows={data.rows} height={height} />}
      </Box>

      {spec.notes.length > 0 && (
        <Box sx={{ px: 2, pb: 1 }}>
          {spec.notes.map((note) => (
            <Typography key={note} variant="caption" sx={{ display: 'block', color: palette.severity.medium }}>
              {note}
            </Typography>
          ))}
        </Box>
      )}

      <Divider />
      <Box sx={{ px: 1.5, py: 0.75 }}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Button size="small" startIcon={<CodeIcon fontSize="small" />} onClick={() => setShowSpec((open) => !open)}>
            {showSpec ? 'Hide definition' : 'Definition'}
          </Button>
          {spec.drill_path.length > 0 && (
            <Chip size="small" variant="outlined" label={`Drill: ${spec.drill_path.join(' › ')}`} />
          )}
          {data && <Typography variant="caption">{data.row_count.toLocaleString()} rows returned</Typography>}
        </Stack>
        <Collapse in={showSpec}>
          <Box
            component="pre"
            className="figure"
            sx={{
              m: 1, p: 1.5, fontSize: '0.7rem', backgroundColor: palette.sunken,
              border: `1px solid ${palette.line}`, overflowX: 'auto',
            }}
          >
            {JSON.stringify(data?.spec ?? spec, null, 2)}
          </Box>
        </Collapse>
      </Box>
    </Card>
  )
}
