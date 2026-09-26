import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert, Box, Button, Card, CardContent, Chip, Collapse, Divider, Grid, Paper, Stack, Tab, Tabs,
  Tooltip, Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import { api } from '../api/client'
import type { ComplexityTier, RecommendationSet, ReportRecommendation } from '../api/types'
import { useWorkbench } from '../App'
import { EmptyState, ErrorView, EvidenceTag, Loading, SectionHeader, TierChip } from '../components/Bits'
import { useStoryboard } from '../hooks/useStoryboard'
import { useWorkspaceState } from '../hooks/useWorkspaceState'
import { palette } from '../theme'
import { titleCase } from '../utils/format'

const TIERS: { id: ComplexityTier; label: string; blurb: string }[] = [
  { id: 'easy', label: 'Easy', blurb: 'One measure, one dimension. Build these first; they anchor the review.' },
  { id: 'medium', label: 'Medium', blurb: 'Two dimensions or a controlled series count. Needs a little interaction design.' },
  { id: 'complex', label: 'Complex', blurb: 'Needs a defined baseline, a confirmed hierarchy or enough history.' },
  { id: 'very_complex', label: 'Very complex', blurb: 'Needs owners, thresholds and a model you are willing to defend.' },
]

function RecommendationCard({
  datasetId, recommendation,
}: { datasetId: string; recommendation: ReportRecommendation }) {
  const [open, setOpen] = useState(false)
  const [added, setAdded] = useState(false)
  const { addInsight, isSaving } = useStoryboard(datasetId)

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="h3">{recommendation.title}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {recommendation.business_question}
            </Typography>
          </Box>
          <Stack spacing={0.5} alignItems="flex-end">
            <TierChip tier={recommendation.tier} />
            <EvidenceTag kind="suggestion" />
          </Stack>
        </Stack>

        <Stack direction="row" spacing={0.75} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
          <Chip size="small" label={recommendation.chart_type.replace(/_/g, ' ')} sx={{ backgroundColor: palette.primaryTint }} />
          <Chip size="small" variant="outlined" label={`For ${recommendation.audience}`} />
          <Tooltip title="How confident the narrator is that this fits the data, not that it fits your business">
            <Chip size="small" variant="outlined" label={`Confidence ${recommendation.confidence}`} />
          </Tooltip>
        </Stack>

        <Typography variant="body2" sx={{ mt: 1.5 }}>
          <strong>Decision it supports.</strong> {recommendation.decision_supported}
        </Typography>
        <Typography variant="body2" sx={{ mt: 0.75 }}>
          <strong>Why this shape.</strong> {recommendation.why_this_representation}
        </Typography>

        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
          <Button size="small" onClick={() => setOpen((value) => !value)}>
            {open ? 'Hide the build sheet' : 'Build sheet'}
          </Button>
          <Button
            size="small"
            disabled={added || isSaving}
            onClick={async () => {
              await addInsight(
                recommendation.title,
                [
                  recommendation.business_question,
                  `Chart: ${recommendation.chart_type}. Metrics: ${
                    recommendation.metrics.map((metric) => `${metric.aggregation}(${metric.column})`).join(', ') || 'none'
                  }. Dimensions: ${recommendation.dimensions.join(', ') || 'none'}.`,
                  recommendation.cautions.length ? `Cautions: ${recommendation.cautions.join(' ')}` : '',
                ]
                  .filter(Boolean)
                  .join(' '),
                'suggestion',
              )
              setAdded(true)
            }}
          >
            {added ? 'On the storyboard' : 'Add to storyboard'}
          </Button>
        </Stack>

        <Collapse in={open}>
          <Box sx={{ mt: 1.5, pt: 1.5, borderTop: `1px solid ${palette.line}` }}>
            <Grid container spacing={1.5}>
              <Grid item xs={12} sm={6}>
                <Typography variant="subtitle2">Metrics</Typography>
                {recommendation.metrics.length ? (
                  recommendation.metrics.map((metric) => (
                    <Typography key={metric.column} variant="body2" className="figure">
                      {metric.aggregation}({metric.column})
                    </Typography>
                  ))
                ) : (
                  <Typography variant="body2">Row count</Typography>
                )}
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Dimensions</Typography>
                <Typography variant="body2">{recommendation.dimensions.join(', ') || '—'}</Typography>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Drill path</Typography>
                <Typography variant="body2">{recommendation.drill_path.join(' › ') || '—'}</Typography>
              </Grid>
              <Grid item xs={12} sm={6}>
                <Typography variant="subtitle2">Prompts and filters</Typography>
                <Typography variant="body2">
                  {[...recommendation.prompts, ...recommendation.filters].join(', ') || '—'}
                </Typography>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Columns required</Typography>
                <Typography variant="body2" className="figure">
                  {recommendation.required_columns.join(', ')}
                </Typography>
              </Grid>
            </Grid>

            {recommendation.cautions.length > 0 && (
              <Alert severity="warning" sx={{ mt: 1.5 }}>
                <Stack spacing={0.25}>
                  {recommendation.cautions.map((caution) => (
                    <Typography key={caution} variant="body2">{caution}</Typography>
                  ))}
                </Stack>
              </Alert>
            )}
            {recommendation.assumptions.length > 0 && (
              <Alert severity="info" sx={{ mt: 1 }}>
                <Stack spacing={0.25}>
                  {recommendation.assumptions.map((assumption) => (
                    <Typography key={assumption} variant="body2">{assumption}</Typography>
                  ))}
                </Stack>
              </Alert>
            )}
            {recommendation.repairs.length > 0 && (
              <Alert severity="success" sx={{ mt: 1 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>Corrected before you saw it</Typography>
                <Stack spacing={0.25}>
                  {recommendation.repairs.map((repair) => (
                    <Typography key={repair} variant="body2">{repair}</Typography>
                  ))}
                </Stack>
              </Alert>
            )}
          </Box>
        </Collapse>
      </CardContent>
    </Card>
  )
}

export default function RecommendationsPage({ datasetId }: { datasetId: string }) {
  const { provider } = useWorkbench()
  const { state: workspace, patch } = useWorkspaceState(datasetId)
  const tier = (workspace?.recommendations_tier ?? 'easy') as ComplexityTier
  const setTier = (value: ComplexityTier) => patch({ recommendations_tier: value })

  /**
   * ENH-05: a cached query, not a mount effect. Returning to this page reuses
   * the cached recommendations instead of re-running the analysis, and the
   * narrator is part of the key so switching it fetches once.
   */
  const generate = useQuery<RecommendationSet>({
    queryKey: ['recommendations', datasetId, provider],
    queryFn: () => api.recommendations(datasetId, { provider, count: 14 }),
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  })

  const result = generate.data
  const inTier = (result?.recommendations ?? []).filter((item) => item.tier === tier)

  return (
    <Box>
      <SectionHeader
        title="Report ideas"
        description="Each idea names its audience, its metrics and its drill path, and has been checked against the real columns and cardinalities before it reached this page."
        action={
          <Button
            variant="outlined"
            startIcon={<RefreshIcon fontSize="small" />}
            onClick={() => generate.refetch()}
            disabled={generate.isFetching}
          >
            Regenerate
          </Button>
        }
      />

      {generate.isFetching && !result && <Loading label="Assembling report ideas…" />}
      {generate.error ? <ErrorView error={generate.error} /> : null}

      {result && (
        <>
          <Paper sx={{ p: 2, mb: 2 }}>
            <Grid container spacing={2}>
              <Grid item xs={12} md={8}>
                <Typography variant="h4">Interpretation</Typography>
                <Typography variant="body2" sx={{ mt: 0.5 }}>{result.interpretation.dataset_summary}</Typography>
                <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>
                  Grain: {result.interpretation.likely_grain} · Subject: {result.interpretation.likely_subject_area}
                </Typography>
              </Grid>
              <Grid item xs={12} md={4}>
                <Typography variant="h4">Provenance</Typography>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  Narrator: {result.provider} ({result.model})
                </Typography>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  Prompt: {result.prompt_version} · Profile: {result.profile_version}
                </Typography>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  Generated {new Date(result.generated_at).toLocaleString()}
                </Typography>
                {result.rejected_count > 0 && (
                  <Typography variant="caption" sx={{ display: 'block', color: palette.severity.medium }}>
                    {result.rejected_count} suggestion(s) were rejected in validation.
                  </Typography>
                )}
              </Grid>
            </Grid>
            {result.rejection_notes.length > 0 && (
              <Alert severity="info" sx={{ mt: 1.5 }}>
                <Stack spacing={0.25}>
                  {result.rejection_notes.map((note) => (
                    <Typography key={note} variant="body2">{note}</Typography>
                  ))}
                </Stack>
              </Alert>
            )}
          </Paper>

          <Tabs
            value={tier}
            onChange={(_, value: string) => setTier(value as ComplexityTier)}
            variant="scrollable"
            allowScrollButtonsMobile
            sx={{ borderBottom: `1px solid ${palette.line}`, mb: 2 }}
          >
            {TIERS.map((entry) => (
              <Tab
                key={entry.id}
                value={entry.id}
                label={`${entry.label} (${(result.by_tier[entry.id] ?? []).length})`}
              />
            ))}
          </Tabs>

          <Typography variant="body2" color="text.secondary" sx={{ mb: 2, maxWidth: '75ch' }}>
            {TIERS.find((entry) => entry.id === tier)?.blurb}
          </Typography>

          {inTier.length === 0 ? (
            <EmptyState title={`Nothing qualifies for the ${titleCase(tier)} tier`}>
              This data does not meet the prerequisites for these visuals. The chart advisor will tell you exactly
              which condition is missing.
            </EmptyState>
          ) : (
            <Grid container spacing={2}>
              {inTier.map((recommendation) => (
                <Grid item xs={12} md={6} key={`${recommendation.title}-${recommendation.chart_type}`}>
                  <RecommendationCard datasetId={datasetId} recommendation={recommendation} />
                </Grid>
              ))}
            </Grid>
          )}

          <Divider sx={{ my: 3 }} />
          <Alert severity="warning">
            These are reporting ideas, not approved reports. Metric definitions, filters and thresholds still need
            sign-off from whoever owns the number.
          </Alert>
        </>
      )}
    </Box>
  )
}
