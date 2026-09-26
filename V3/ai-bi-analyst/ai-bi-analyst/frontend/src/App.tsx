import { createContext, useContext, useMemo, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  Alert, AppBar, Box, Chip, Divider, FormControl, IconButton, InputLabel, List, ListItemButton,
  ListItemText, MenuItem, Select, Toolbar, Tooltip, Typography,
} from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api/client'
import type { ProviderInfo } from './api/types'
import { palette } from './theme'
import UsagePanel, { UsagePanelCompact } from './components/UsagePanel'
import UploadPage from './pages/UploadPage'
import OverviewPage from './pages/OverviewPage'
import ProfilePage from './pages/ProfilePage'
import RelationshipsPage from './pages/RelationshipsPage'
import RecommendationsPage from './pages/RecommendationsPage'
import ChartAdvisorPage from './pages/ChartAdvisorPage'
import AskDataPage from './pages/AskDataPage'
import StoryboardPage from './pages/StoryboardPage'

const STEPS = [
  { slug: 'overview', label: 'Overview', hint: 'Size, shape and quality of the file' },
  { slug: 'profile', label: 'Data profile', hint: 'Column by column, with the findings' },
  { slug: 'relationships', label: 'Relationships', hint: 'Which columns move together' },
  { slug: 'recommendations', label: 'Report ideas', hint: 'Grouped by how hard they are to build' },
  { slug: 'chart-advisor', label: 'Chart advisor', hint: 'Pick columns, see what is valid' },
  { slug: 'ask', label: 'Ask the data', hint: 'Questions answered by executed queries' },
  { slug: 'storyboard', label: 'Storyboard', hint: 'Assemble and export the dashboard' },
]

interface WorkbenchState {
  provider: string
  setProvider: (value: string) => void
  providers: ProviderInfo[]
}

const WorkbenchContext = createContext<WorkbenchState>({
  provider: 'heuristic',
  setProvider: () => {},
  providers: [],
})

export const useWorkbench = () => useContext(WorkbenchContext)

function ProviderPicker() {
  const { provider, setProvider, providers } = useWorkbench()
  return (
    <FormControl size="small" sx={{ minWidth: 210 }}>
      <InputLabel id="provider-label">Narrator</InputLabel>
      <Select
        labelId="provider-label"
        label="Narrator"
        value={provider}
        onChange={(event) => setProvider(event.target.value)}
      >
        {providers.map((item) => (
          <MenuItem key={item.id} value={item.id} disabled={!item.configured}>
            {item.id === 'heuristic' ? 'Built in (no key needed)' : item.label}
            {!item.configured && ' — no server key'}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  )
}

function DatasetBar({ datasetId }: { datasetId: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { provider } = useWorkbench()
  const { data } = useQuery({ queryKey: ['overview', datasetId], queryFn: () => api.overview(datasetId) })
  const remove = useMutation({
    mutationFn: () => api.remove(datasetId),
    onSuccess: () => {
      queryClient.clear()
      // Client-side navigation: deleting a session must not reload the app.
      navigate('/', { replace: true })
    },
  })

  return (
    <Box
      sx={{
        px: 3, py: 1.25, backgroundColor: palette.ink, color: '#E9EEF3',
        display: 'flex', alignItems: 'center', gap: 3, flexWrap: 'wrap',
      }}
    >
      <Typography sx={{ fontWeight: 600, fontSize: '0.9rem' }}>{data?.filename ?? 'Loading file…'}</Typography>
      {data && (
        <>
          <Typography className="figure" variant="body2" sx={{ color: '#A9B8C4' }}>
            {data.row_count.toLocaleString()} rows · {data.column_count} columns
          </Typography>
          <Tooltip title={data.quality.formula}>
            <Chip
              size="small"
              label={`Quality ${data.quality.score.toFixed(0)} (${data.quality.grade})`}
              sx={{ backgroundColor: '#25323E', color: '#E9EEF3' }}
            />
          </Tooltip>
          {data.sampled && (
            <Tooltip title={data.sample_method ?? ''}>
              <Chip size="small" label="Sampled" sx={{ backgroundColor: palette.severity.medium, color: '#fff' }} />
            </Tooltip>
          )}
          <Box sx={{ flex: 1 }} />
          <Box sx={{ display: { xs: 'block', md: 'none' } }}>
            <UsagePanelCompact datasetId={datasetId} provider={provider} />
          </Box>
          <Tooltip title="Delete this session and its uploaded data">
            <IconButton size="small" onClick={() => remove.mutate()} sx={{ color: '#A9B8C4' }} aria-label="Delete session">
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </>
      )}
    </Box>
  )
}

function WorkflowRail({ datasetId }: { datasetId: string }) {
  const { pathname } = useLocation()
  const { provider } = useWorkbench()
  return (
    <Box
      component="nav"
      aria-label="Analysis steps"
      sx={{
        width: 232, flexShrink: 0, borderRight: `1px solid ${palette.line}`,
        backgroundColor: palette.surface, minHeight: '100%',
        display: 'flex', flexDirection: 'column',
        position: 'sticky', top: 0, alignSelf: 'flex-start', maxHeight: '100vh',
      }}
    >
      <List dense disablePadding>
        {STEPS.map((step, index) => {
          const to = `/d/${datasetId}/${step.slug}`
          const selected = pathname.startsWith(to)
          return (
            <ListItemButton
              key={step.slug}
              component={Link}
              to={to}
              selected={selected}
              sx={{
                alignItems: 'flex-start', py: 1.1, borderLeft: '3px solid',
                borderLeftColor: selected ? palette.primary : 'transparent',
                '&.Mui-selected': { backgroundColor: palette.primaryTint },
              }}
            >
              <Typography
                className="figure"
                sx={{ width: 22, fontSize: '0.75rem', color: palette.muted, mt: '2px' }}
              >
                {index + 1}
              </Typography>
              <ListItemText
                primary={step.label}
                secondary={step.hint}
                primaryTypographyProps={{ fontSize: '0.85rem', fontWeight: selected ? 600 : 500 }}
                secondaryTypographyProps={{ fontSize: '0.72rem' }}
              />
            </ListItemButton>
          )
        })}
      </List>
      <Divider />
      <Box sx={{ p: 2, flex: 1, overflowY: 'auto' }}>
        <Typography variant="caption">
          Every figure on these pages is computed in Python. Report ideas and wording are suggestions that
          need business validation.
        </Typography>
      </Box>
      {/* ENH-01: anchored at the bottom of the navigation, on every route. */}
      <UsagePanel datasetId={datasetId} provider={provider} />
    </Box>
  )
}

function Workspace() {
  const { datasetId = '' } = useParams()
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <AppBar position="static">
        <Toolbar sx={{ gap: 2 }}>
          <Typography component={Link} to="/" sx={{ fontWeight: 600, color: palette.ink, textDecoration: 'none' }}>
            AI BI Analyst
          </Typography>
          <Typography variant="caption" sx={{ display: { xs: 'none', md: 'block' } }}>
            Python computes the facts. The model only interprets them.
          </Typography>
          <Box sx={{ flex: 1 }} />
          <ProviderPicker />
        </Toolbar>
      </AppBar>
      <DatasetBar datasetId={datasetId} />
      <Box sx={{ display: 'flex', flex: 1, alignItems: 'stretch' }}>
        <Box sx={{ display: { xs: 'none', md: 'block' } }}>
          <WorkflowRail datasetId={datasetId} />
        </Box>
        <Box component="main" sx={{ flex: 1, p: { xs: 2, md: 3 }, minWidth: 0 }}>
          <Routes>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<OverviewPage datasetId={datasetId} />} />
            <Route path="profile" element={<ProfilePage datasetId={datasetId} />} />
            <Route path="relationships" element={<RelationshipsPage datasetId={datasetId} />} />
            <Route path="recommendations" element={<RecommendationsPage datasetId={datasetId} />} />
            <Route path="chart-advisor" element={<ChartAdvisorPage datasetId={datasetId} />} />
            <Route path="ask" element={<AskDataPage datasetId={datasetId} />} />
            <Route path="storyboard" element={<StoryboardPage datasetId={datasetId} />} />
            <Route path="*" element={<Navigate to="overview" replace />} />
          </Routes>
        </Box>
      </Box>
    </Box>
  )
}

export default function App() {
  const [provider, setProvider] = useState('heuristic')
  const { data, isError } = useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: Infinity })

  const value = useMemo<WorkbenchState>(
    () => ({ provider, setProvider, providers: data?.providers ?? [] }),
    [provider, data?.providers],
  )

  return (
    <WorkbenchContext.Provider value={value}>
      {isError && (
        <Alert severity="error" sx={{ borderRadius: 0 }}>
          The backend is not reachable. Start it with <code>uvicorn app.main:app --reload</code> in the backend
          folder, then reload this page.
        </Alert>
      )}
      <Routes>
        <Route path="/" element={<UploadPage limits={data?.limits} />} />
        <Route path="/d/:datasetId/*" element={<Workspace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </WorkbenchContext.Provider>
  )
}

export { STEPS }
