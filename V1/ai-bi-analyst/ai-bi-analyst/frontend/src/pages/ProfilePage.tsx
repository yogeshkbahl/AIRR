import { Fragment, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, Collapse, Divider,
  FormControl, Grid, IconButton, InputLabel, MenuItem, Paper, Select, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import { api } from '../api/client'
import type { AnalyticalRole, Anomaly, ColumnProfile } from '../api/types'
import {
  EmptyState, ErrorView, EvidenceTag, Loading, ResultTable, RoleChip, SectionHeader, SeverityChip,
} from '../components/Bits'
import { useStoryboard } from '../hooks/useStoryboard'
import { compactNumber, fullNumber, percent, shortDate, titleCase } from '../utils/format'
import { palette } from '../theme'

const ROLES: AnalyticalRole[] = [
  'measure', 'currency', 'percentage', 'categorical_dimension', 'datetime_dimension',
  'identifier', 'geography', 'free_text', 'sensitive', 'unusable',
]

const AGGREGATIONS = ['sum', 'avg', 'min', 'max', 'count', 'count_distinct', 'median', 'none']

function ColumnDetail({ column }: { column: ColumnProfile }) {
  return (
    <Grid container spacing={2}>
      <Grid item xs={12} md={4}>
        <Typography variant="subtitle2">Why this role</Typography>
        <Typography variant="body2">{column.role_reason || 'Inferred from the values.'}</Typography>
        <Typography variant="subtitle2" sx={{ mt: 1.5 }}>Completeness</Typography>
        <Typography variant="body2" className="figure">
          {column.non_null_count.toLocaleString()} filled · {column.null_count.toLocaleString()} null (
          {percent(column.null_percent, 2)})
        </Typography>
        <Typography variant="subtitle2" sx={{ mt: 1.5 }}>Cardinality</Typography>
        <Typography variant="body2" className="figure">
          {column.distinct_count.toLocaleString()} distinct · uniqueness {percent(column.uniqueness_ratio * 100, 1)}
        </Typography>
        {column.hierarchy && (
          <>
            <Typography variant="subtitle2" sx={{ mt: 1.5 }}>Candidate hierarchy</Typography>
            <Typography variant="body2">{column.hierarchy}</Typography>
          </>
        )}
        <Typography variant="subtitle2" sx={{ mt: 1.5 }}>
          Sample values {column.is_sensitive && '(masked)'}
        </Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {column.sample_values.map((value, index) => (
            <Chip key={`${value}-${index}`} size="small" variant="outlined" label={value} />
          ))}
        </Stack>
      </Grid>

      {column.numeric && (
        <Grid item xs={12} md={4}>
          <Typography variant="subtitle2">Numeric statistics</Typography>
          <Table size="small">
            <TableBody>
              {[
                ['Minimum', fullNumber(column.numeric.min)],
                ['25th percentile', fullNumber(column.numeric.p25)],
                ['Median', fullNumber(column.numeric.median)],
                ['Mean', fullNumber(column.numeric.mean)],
                ['75th percentile', fullNumber(column.numeric.p75)],
                ['Maximum', fullNumber(column.numeric.max)],
                ['Std deviation', fullNumber(column.numeric.std)],
                ['IQR', fullNumber(column.numeric.iqr)],
                ['Zeros', column.numeric.zero_count.toLocaleString()],
                ['Negatives', column.numeric.negative_count.toLocaleString()],
                ['Outliers (IQR)', column.numeric.outlier_count_iqr.toLocaleString()],
                ['Outliers (robust z)', column.numeric.outlier_count_robust_z.toLocaleString()],
              ].map(([label, value]) => (
                <TableRow key={label}>
                  <TableCell sx={{ border: 0, py: 0.25 }}>{label}</TableCell>
                  <TableCell align="right" className="figure" sx={{ border: 0, py: 0.25 }}>{value}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Grid>
      )}

      {column.categorical && (
        <Grid item xs={12} md={4}>
          <Typography variant="subtitle2">Top values</Typography>
          <Table size="small">
            <TableBody>
              {column.categorical.top_values.map((top) => (
                <TableRow key={top.value}>
                  <TableCell sx={{ border: 0, py: 0.25 }}>{top.value || '(blank)'}</TableCell>
                  <TableCell align="right" className="figure" sx={{ border: 0, py: 0.25 }}>
                    {top.count.toLocaleString()} · {top.percent.toFixed(1)}%
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>
            {column.categorical.rare_category_count} rare categories ·{' '}
            {column.categorical.blank_string_count} blank strings ·{' '}
            {column.categorical.whitespace_issue_count} with stray spaces ·{' '}
            {column.categorical.casing_variant_groups} casing variant groups
          </Typography>
        </Grid>
      )}

      {column.date && (
        <Grid item xs={12} md={4}>
          <Typography variant="subtitle2">Date coverage</Typography>
          <Table size="small">
            <TableBody>
              {[
                ['Earliest', shortDate(column.date.earliest)],
                ['Latest', shortDate(column.date.latest)],
                ['Span (days)', String(column.date.span_days ?? '—')],
                ['Distinct days', String(column.date.distinct_days ?? '—')],
                ['Granularity', column.date.inferred_granularity ?? '—'],
                ['Largest gap (days)', String(column.date.largest_gap_days ?? '—')],
                ['Invalid values', String(column.date.invalid_count)],
                ['Future dates', String(column.date.future_count)],
              ].map(([label, value]) => (
                <TableRow key={label}>
                  <TableCell sx={{ border: 0, py: 0.25 }}>{label}</TableCell>
                  <TableCell align="right" className="figure" sx={{ border: 0, py: 0.25 }}>{value}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Grid>
      )}
    </Grid>
  )
}

function AnomalyCard({ datasetId, anomaly }: { datasetId: string; anomaly: Anomaly }) {
  const [open, setOpen] = useState(false)
  const { addInsight } = useStoryboard(datasetId)
  const preview = useQuery({
    queryKey: ['anomaly-rows', datasetId, anomaly.id],
    queryFn: () => api.anomalyRows(datasetId, anomaly.id),
    enabled: open && Boolean(anomaly.row_filter),
  })

  return (
    <Paper sx={{ p: 2, mb: 1.5 }}>
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <SeverityChip severity={anomaly.severity} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="h4">{anomaly.title}</Typography>
            <EvidenceTag kind={anomaly.evidence_kind} />
            {anomaly.columns.map((column) => (
              <Chip key={column} size="small" label={column} variant="outlined" />
            ))}
          </Stack>
          <Typography variant="body2" sx={{ mt: 0.75 }}>{anomaly.explanation}</Typography>
          <Typography variant="body2" sx={{ mt: 0.75, color: palette.primary }}>
            Next step: {anomaly.recommended_action}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', mt: 0.75 }}>
            {anomaly.affected_rows.toLocaleString()} rows affected ({percent(anomaly.affected_percent, 2)}) ·
            method: {anomaly.method}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            {anomaly.row_filter && (
              <Button size="small" onClick={() => setOpen((value) => !value)}>
                {open ? 'Hide affected rows' : 'Show affected rows'}
              </Button>
            )}
            <Button
              size="small"
              onClick={() =>
                addInsight(
                  anomaly.title,
                  `${anomaly.explanation} Next step: ${anomaly.recommended_action} (${anomaly.method})`,
                  anomaly.evidence_kind,
                )
              }
            >
              Add to storyboard
            </Button>
          </Stack>
          <Collapse in={open}>
            <Box sx={{ mt: 1.5 }}>
              {preview.isLoading && <Loading label="Fetching rows…" />}
              {preview.error && <ErrorView error={preview.error} />}
              {preview.data && (
                <>
                  {preview.data.hidden_columns.length > 0 && (
                    <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
                      Hidden from this preview: {preview.data.hidden_columns.join(', ')}
                    </Typography>
                  )}
                  <ResultTable columns={preview.data.columns} rows={preview.data.rows} maxHeight={260} />
                </>
              )}
            </Box>
          </Collapse>
        </Box>
      </Stack>
    </Paper>
  )
}

export default function ProfilePage({ datasetId }: { datasetId: string }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<string>('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  const columns = useQuery({ queryKey: ['columns', datasetId], queryFn: () => api.columns(datasetId) })
  const anomalies = useQuery({ queryKey: ['anomalies', datasetId], queryFn: () => api.anomalies(datasetId) })

  const override = useMutation({
    mutationFn: (body: { name: string; analytical_role?: string; default_aggregation?: string }) =>
      api.overrideColumns(datasetId, [body]),
    onSuccess: (updated) => {
      queryClient.setQueryData(['columns', datasetId], updated)
      void queryClient.invalidateQueries({ queryKey: ['overview', datasetId] })
      void queryClient.invalidateQueries({ queryKey: ['anomalies', datasetId] })
      void queryClient.invalidateQueries({ queryKey: ['relationships', datasetId] })
    },
  })

  const visible = useMemo(() => {
    const all = columns.data ?? []
    const term = search.trim().toLowerCase()
    return all.filter(
      (column) =>
        (roleFilter === 'all' || column.analytical_role === roleFilter) &&
        (!term || column.name.toLowerCase().includes(term) || column.label.toLowerCase().includes(term)),
    )
  }, [columns.data, roleFilter, search])

  if (columns.isLoading) return <Loading label="Loading column profiles…" />
  if (columns.error) return <ErrorView error={columns.error} />

  const findings = anomalies.data ?? []
  const bySeverity = ['high', 'medium', 'low', 'info'] as const

  return (
    <Box>
      <SectionHeader
        title="Data profile"
        description="One row per column, with the statistics that matter for reporting. Change a role or default aggregation and the whole profile is recomputed."
      />

      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
          <TextField
            size="small"
            label="Find a column"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            sx={{ minWidth: 240 }}
          />
          <FormControl size="small" sx={{ minWidth: 220 }}>
            <InputLabel id="role-filter">Role</InputLabel>
            <Select
              labelId="role-filter"
              label="Role"
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
            >
              <MenuItem value="all">All roles</MenuItem>
              {ROLES.map((role) => (
                <MenuItem key={role} value={role}>{titleCase(role)}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <Typography variant="body2" color="text.secondary">
            {visible.length} of {columns.data?.length ?? 0} columns
          </Typography>
        </Stack>
      </Paper>

      <Paper sx={{ overflowX: 'auto', mb: 3 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell width={40} />
              <TableCell>Column</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Role</TableCell>
              <TableCell>Default aggregation</TableCell>
              <TableCell align="right">Nulls</TableCell>
              <TableCell align="right">Distinct</TableCell>
              <TableCell align="right">Range or top value</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {visible.map((column) => (
              <Fragment key={column.name}>
                <TableRow hover>
                  <TableCell>
                    <IconButton
                      size="small"
                      aria-label={`Details for ${column.name}`}
                      onClick={() => setExpanded(expanded === column.name ? null : column.name)}
                    >
                      {expanded === column.name ? <ExpandMoreIcon fontSize="small" /> : <ChevronRightIcon fontSize="small" />}
                    </IconButton>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>{column.label}</Typography>
                    <Typography variant="caption" className="figure">{column.name}</Typography>
                  </TableCell>
                  <TableCell>{titleCase(column.physical_type)}</TableCell>
                  <TableCell>
                    <FormControl size="small" variant="standard" sx={{ minWidth: 150 }}>
                      <Select
                        value={column.analytical_role}
                        onChange={(event) => override.mutate({ name: column.name, analytical_role: event.target.value })}
                        disableUnderline
                        renderValue={(value) => <RoleChip role={value as AnalyticalRole} reason={column.role_reason} />}
                      >
                        {ROLES.map((role) => (
                          <MenuItem key={role} value={role}>{titleCase(role)}</MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </TableCell>
                  <TableCell>
                    <FormControl size="small" variant="standard" sx={{ minWidth: 120 }}>
                      <Select
                        value={column.default_aggregation}
                        disableUnderline
                        onChange={(event) =>
                          override.mutate({ name: column.name, default_aggregation: event.target.value })
                        }
                      >
                        {AGGREGATIONS.map((aggregation) => (
                          <MenuItem key={aggregation} value={aggregation}>{aggregation}</MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </TableCell>
                  <TableCell align="right" className="figure">
                    <Tooltip title={`${column.null_count.toLocaleString()} null values`}>
                      <span>{percent(column.null_percent, 1)}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right" className="figure">{column.distinct_count.toLocaleString()}</TableCell>
                  <TableCell align="right" className="figure">
                    {column.numeric
                      ? `${compactNumber(column.numeric.min)} → ${compactNumber(column.numeric.max)}`
                      : column.date
                        ? `${shortDate(column.date.earliest)} → ${shortDate(column.date.latest)}`
                        : (column.categorical?.top_values[0]?.value ?? '—')}
                  </TableCell>
                </TableRow>
                {expanded === column.name && (
                  <TableRow>
                    <TableCell colSpan={8} sx={{ backgroundColor: palette.sunken }}>
                      <ColumnDetail column={column} />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
        {visible.length === 0 && (
          <Box sx={{ p: 3 }}>
            <EmptyState title="No column matches that filter">
              Clear the search box or pick a different role.
            </EmptyState>
          </Box>
        )}
      </Paper>

      {override.error && <ErrorView error={override.error} />}

      <Divider sx={{ my: 3 }} />

      <SectionHeader
        title="Findings"
        description="Each finding names its method, its scope and the decision it needs from you. Findings that depend on business rules are labelled as assumptions, not facts."
      />

      {anomalies.isLoading && <Loading label="Scanning for anomalies…" />}
      {findings.length === 0 && !anomalies.isLoading && (
        <EmptyState title="No anomalies were detected">
          The checks covered missingness, duplicates, constants, mixed types, outliers, casing, date coverage and
          key consistency.
        </EmptyState>
      )}

      {bySeverity.map((severity) => {
        const group = findings.filter((finding) => finding.severity === severity)
        if (!group.length) return null
        return (
          <Accordion key={severity} defaultExpanded={severity === 'high' || severity === 'medium'} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack direction="row" spacing={1.5} alignItems="center">
                <SeverityChip severity={severity} />
                <Typography variant="h4">
                  {group.length} {severity} severity finding{group.length > 1 ? 's' : ''}
                </Typography>
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              {group.map((finding) => (
                <AnomalyCard key={finding.id} datasetId={datasetId} anomaly={finding} />
              ))}
            </AccordionDetails>
          </Accordion>
        )
      })}

      {findings.length > 0 && (
        <Alert severity="info" sx={{ mt: 2 }}>
          Row previews exclude every column that looks like personal data, so you can share a screenshot of a
          finding without exposing customers.
        </Alert>
      )}
    </Box>
  )
}
