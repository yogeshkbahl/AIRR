import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Box, Button, Chip, Collapse, Dialog, DialogActions, DialogContent, DialogTitle, Divider,
  FormControl, FormControlLabel, Grid, InputLabel, MenuItem, Paper, Select, Stack, Switch, Table,
  TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { api } from '../api/client'
import type { ColumnProfile, QualityReport, QualityRule, RuleKind, Severity } from '../api/types'
import { ErrorView, EvidenceTag, Loading, SeverityChip } from './Bits'
import { percent, titleCase } from '../utils/format'
import { palette } from '../theme'

/**
 * Governed data-quality rules.
 *
 * The point of this panel is the distinction it draws: a rule result is a
 * confirmed breach of a constraint someone agreed to, so it is labelled `fact`
 * and shows the exact expression that produced the count. The statistical
 * findings on this page remain suspicions, and are listed separately at the
 * bottom so the two are never read as the same kind of claim.
 *
 * Suggestions are derived from the profile but arrive disabled: a rule only
 * becomes a rule when a person enables it.
 */

const KINDS: { value: RuleKind; label: string; help: string }[] = [
  { value: 'required', label: 'Required', help: 'No nulls and no blank strings.' },
  { value: 'unique', label: 'Unique', help: 'No repeated values among non-null rows.' },
  { value: 'accepted_values', label: 'Accepted values', help: 'Must be one of an agreed list.' },
  { value: 'numeric_range', label: 'Numeric range', help: 'Must fall inside a minimum and maximum.' },
  { value: 'regex_format', label: 'Format', help: 'Must match a regular expression.' },
  { value: 'date_range', label: 'Date range', help: 'Must fall inside a window, optionally not in the future.' },
]

const SEVERITIES: Severity[] = ['high', 'medium', 'low', 'info']

function constraintText(rule: QualityRule): string {
  switch (rule.kind) {
    case 'required':
      return 'must always be populated'
    case 'unique':
      return 'must be unique across rows'
    case 'accepted_values':
      return `must be one of ${rule.allowed.slice(0, 4).join(', ')}${rule.allowed.length > 4 ? ` +${rule.allowed.length - 4} more` : ''}`
    case 'numeric_range':
      return [
        rule.min_value !== null ? `≥ ${rule.min_value}` : null,
        rule.max_value !== null ? `≤ ${rule.max_value}` : null,
      ]
        .filter(Boolean)
        .join(' and ')
    case 'regex_format':
      return `must match /${rule.pattern ?? ''}/`
    case 'date_range':
      return [
        rule.earliest ? `from ${rule.earliest}` : null,
        rule.latest ? `to ${rule.latest}` : null,
        rule.allow_future ? null : 'never in the future',
      ]
        .filter(Boolean)
        .join(', ')
    default:
      return ''
  }
}

function emptyRule(column: string): QualityRule {
  return {
    id: '',
    column,
    kind: 'required',
    enabled: true,
    severity: 'medium',
    description: '',
    allowed: [],
    min_value: null,
    max_value: null,
    inclusive: true,
    pattern: null,
    earliest: null,
    latest: null,
    allow_future: true,
    max_fail_percent: 0,
    origin: 'user',
    remediation: '',
  }
}

function RuleDialog({
  open, columns, onClose, onSave,
}: {
  open: boolean
  columns: ColumnProfile[]
  onClose: () => void
  onSave: (rule: QualityRule) => void
}) {
  const [draft, setDraft] = useState<QualityRule>(() => emptyRule(columns[0]?.name ?? ''))
  const patch = (changes: Partial<QualityRule>) => setDraft((current) => ({ ...current, ...changes }))

  const id = `rule-${draft.kind.replace(/_/g, '-')}-${draft.column}`.toLowerCase()

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Add a data-quality rule</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <FormControl size="small" fullWidth>
            <InputLabel id="rule-column">Column</InputLabel>
            <Select
              labelId="rule-column"
              label="Column"
              value={draft.column}
              onChange={(event) => patch({ column: event.target.value })}
            >
              {columns.map((column) => (
                <MenuItem key={column.name} value={column.name}>
                  {column.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControl size="small" fullWidth>
            <InputLabel id="rule-kind">Rule</InputLabel>
            <Select
              labelId="rule-kind"
              label="Rule"
              value={draft.kind}
              onChange={(event) => patch({ kind: event.target.value as RuleKind })}
            >
              {KINDS.map((kind) => (
                <MenuItem key={kind.value} value={kind.value}>
                  {kind.label} — {kind.help}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {draft.kind === 'accepted_values' && (
            <TextField
              size="small"
              label="Allowed values, comma separated"
              value={draft.allowed.join(', ')}
              onChange={(event) =>
                patch({
                  allowed: event.target.value
                    .split(',')
                    .map((value) => value.trim())
                    .filter(Boolean),
                })
              }
            />
          )}

          {draft.kind === 'numeric_range' && (
            <Stack direction="row" spacing={2}>
              <TextField
                size="small"
                label="Minimum"
                type="number"
                value={draft.min_value ?? ''}
                onChange={(event) =>
                  patch({ min_value: event.target.value === '' ? null : Number(event.target.value) })
                }
              />
              <TextField
                size="small"
                label="Maximum"
                type="number"
                value={draft.max_value ?? ''}
                onChange={(event) =>
                  patch({ max_value: event.target.value === '' ? null : Number(event.target.value) })
                }
              />
            </Stack>
          )}

          {draft.kind === 'regex_format' && (
            <TextField
              size="small"
              label="Pattern"
              value={draft.pattern ?? ''}
              placeholder="^[A-Z]{3}-\\d{4}$"
              onChange={(event) => patch({ pattern: event.target.value })}
            />
          )}

          {draft.kind === 'date_range' && (
            <>
              <Stack direction="row" spacing={2}>
                <TextField
                  size="small"
                  label="Earliest"
                  type="date"
                  InputLabelProps={{ shrink: true }}
                  value={draft.earliest ?? ''}
                  onChange={(event) => patch({ earliest: event.target.value || null })}
                />
                <TextField
                  size="small"
                  label="Latest"
                  type="date"
                  InputLabelProps={{ shrink: true }}
                  value={draft.latest ?? ''}
                  onChange={(event) => patch({ latest: event.target.value || null })}
                />
              </Stack>
              <FormControlLabel
                control={
                  <Switch
                    checked={!draft.allow_future}
                    onChange={(event) => patch({ allow_future: !event.target.checked })}
                  />
                }
                label="Future dates are a breach"
              />
            </>
          )}

          <Stack direction="row" spacing={2}>
            <FormControl size="small" sx={{ minWidth: 130 }}>
              <InputLabel id="rule-severity">Severity</InputLabel>
              <Select
                labelId="rule-severity"
                label="Severity"
                value={draft.severity}
                onChange={(event) => patch({ severity: event.target.value as Severity })}
              >
                {SEVERITIES.map((severity) => (
                  <MenuItem key={severity} value={severity}>
                    {titleCase(severity)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Tooltip title="How much failure the business tolerates before this counts as a breach">
              <TextField
                size="small"
                label="Tolerance %"
                type="number"
                value={draft.max_fail_percent}
                onChange={(event) => patch({ max_fail_percent: Number(event.target.value) || 0 })}
              />
            </Tooltip>
          </Stack>

          <TextField
            size="small"
            label="Why this rule exists"
            value={draft.description}
            onChange={(event) => patch({ description: event.target.value })}
          />
          <TextField
            size="small"
            label="What to do when it fails"
            value={draft.remediation}
            onChange={(event) => patch({ remediation: event.target.value })}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!draft.column}
          onClick={() => {
            onSave({ ...draft, id })
            onClose()
          }}
        >
          Add rule
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default function QualityRulesPanel({
  datasetId,
  columns,
}: {
  datasetId: string
  columns: ColumnProfile[]
}) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [showPassing, setShowPassing] = useState(false)

  const rules = useQuery({
    queryKey: ['quality-rules', datasetId],
    queryFn: () => api.qualityRules(datasetId),
    staleTime: 60_000,
  })

  const save = useMutation({
    mutationFn: (next: QualityRule[]) => api.saveQualityRules(datasetId, next),
    onSuccess: (set) => queryClient.setQueryData(['quality-rules', datasetId], set),
  })

  const evaluate = useMutation<QualityReport>({
    mutationFn: () => api.evaluateQualityRules(datasetId),
  })

  const items = rules.data?.rules ?? []
  const enabledCount = items.filter((rule) => rule.enabled).length
  const report = evaluate.data
  const visibleResults = useMemo(
    () => (report ? report.results.filter((result) => showPassing || !result.passed) : []),
    [report, showPassing],
  )

  const toggle = (rule: QualityRule) => save.mutate([{ ...rule, enabled: !rule.enabled, origin: 'user' }])

  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction="row" spacing={2} alignItems="flex-start" sx={{ mb: 1.5 }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h3">Data-quality rules</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ maxWidth: '70ch' }}>
            A rule failure is a confirmed breach of a constraint you have agreed, which is a different claim
            from the statistical findings above. Suggested rules arrive switched off; enable the ones your
            business actually stands behind.
          </Typography>
        </Box>
        <Stack spacing={1} alignItems="flex-end">
          <Button size="small" onClick={() => setDialogOpen(true)}>
            Add rule
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={<PlayArrowIcon fontSize="small" />}
            disabled={enabledCount === 0 || evaluate.isPending}
            onClick={() => evaluate.mutate()}
          >
            {evaluate.isPending ? 'Evaluating…' : `Evaluate ${enabledCount} rule${enabledCount === 1 ? '' : 's'}`}
          </Button>
        </Stack>
      </Stack>

      {rules.isLoading && <Loading label="Loading rules…" />}
      {rules.error ? <ErrorView error={rules.error} /> : null}
      {save.error ? <ErrorView error={save.error} /> : null}

      {rules.data?.dropped_on_restore.length ? (
        <Alert severity="info" sx={{ mb: 1.5 }}>
          Rules for {rules.data.dropped_on_restore.join(', ')} were dropped because those columns are no longer
          in this dataset.
        </Alert>
      ) : null}

      {items.length > 0 && (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell width={70}>Active</TableCell>
              <TableCell>Column</TableCell>
              <TableCell>Constraint</TableCell>
              <TableCell>Severity</TableCell>
              <TableCell align="right">Tolerance</TableCell>
              <TableCell>Origin</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {items.map((rule) => (
              <TableRow key={rule.id} hover>
                <TableCell>
                  <Switch
                    size="small"
                    checked={rule.enabled}
                    onChange={() => toggle(rule)}
                    inputProps={{ 'aria-label': `Enable ${rule.kind} rule on ${rule.column}` }}
                  />
                </TableCell>
                <TableCell>
                  <Typography variant="body2">{rule.column}</Typography>
                  {rule.description && <Typography variant="caption">{rule.description}</Typography>}
                </TableCell>
                <TableCell>
                  <Typography variant="body2">
                    <strong>{titleCase(rule.kind)}</strong> — {constraintText(rule)}
                  </Typography>
                </TableCell>
                <TableCell>
                  <SeverityChip severity={rule.severity} />
                </TableCell>
                <TableCell align="right" className="figure">
                  {rule.max_fail_percent > 0 ? `${rule.max_fail_percent}%` : '0'}
                </TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={rule.origin === 'suggested' ? 'proposed' : 'agreed'}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {evaluate.error ? (
        <Box sx={{ mt: 2 }}>
          <ErrorView error={evaluate.error} />
        </Box>
      ) : null}

      <Collapse in={Boolean(report)}>
        {report && (
          <Box sx={{ mt: 2, pt: 2, borderTop: `1px solid ${palette.line}` }}>
            <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
              <Typography variant="h4">Results</Typography>
              <EvidenceTag kind="fact" />
              <Chip
                size="small"
                label={`${report.rules_passed} passed`}
                sx={{ backgroundColor: palette.primaryTint }}
              />
              <Chip
                size="small"
                label={`${report.rules_failed} failed`}
                sx={
                  report.rules_failed
                    ? { backgroundColor: palette.severity.high, color: '#fff' }
                    : { backgroundColor: palette.primaryTint }
                }
              />
              {report.rules_skipped > 0 && <Chip size="small" variant="outlined" label={`${report.rules_skipped} skipped`} />}
              <Box sx={{ flex: 1 }} />
              <FormControlLabel
                control={<Switch size="small" checked={showPassing} onChange={() => setShowPassing((v) => !v)} />}
                label={<Typography variant="caption">Show passing rules</Typography>}
              />
              <Button
                size="small"
                startIcon={<DownloadIcon fontSize="small" />}
                href={api.qualityReportCsvUrl(datasetId)}
              >
                Export report
              </Button>
            </Stack>

            <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
              Evaluated {new Date(report.evaluated_at).toLocaleString()} over{' '}
              {report.analyzed_rows.toLocaleString()} rows{report.sampled ? ' (sampled)' : ''}.
            </Typography>

            <Table size="small" sx={{ mt: 1 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Outcome</TableCell>
                  <TableCell>Rule</TableCell>
                  <TableCell>Expression evaluated</TableCell>
                  <TableCell align="right">Failed</TableCell>
                  <TableCell>Examples and next step</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {visibleResults.map((result) => (
                  <TableRow key={result.rule_id} hover>
                    <TableCell>
                      {result.skipped_reason ? (
                        <Chip size="small" variant="outlined" label="skipped" />
                      ) : result.passed ? (
                        <Chip size="small" variant="outlined" label="pass" sx={{ color: palette.evidence.inference, borderColor: palette.evidence.inference }} />
                      ) : (
                        <Chip size="small" label="fail" sx={{ backgroundColor: palette.severity.high, color: '#fff' }} />
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{result.label}</Typography>
                      <SeverityChip severity={result.severity} />
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" className="figure">
                        {result.expression}
                      </Typography>
                    </TableCell>
                    <TableCell align="right" className="figure">
                      {result.failed_rows.toLocaleString()}
                      <Typography variant="caption" sx={{ display: 'block' }}>
                        {percent(result.failed_percent, 2)} of rows
                      </Typography>
                    </TableCell>
                    <TableCell>
                      {result.skipped_reason ? (
                        <Typography variant="caption">{result.skipped_reason}</Typography>
                      ) : (
                        <>
                          {result.sample_values.length > 0 && (
                            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mb: 0.5 }}>
                              {result.sample_values.map((value, index) => (
                                <Chip key={`${value}-${index}`} size="small" variant="outlined" label={value} />
                              ))}
                            </Stack>
                          )}
                          {result.remediation && <Typography variant="caption">{result.remediation}</Typography>}
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {visibleResults.length === 0 && (
              <Alert severity="success" sx={{ mt: 1.5 }}>
                Every enabled rule passed. Turn on “show passing rules” to see the detail.
              </Alert>
            )}

            {report.suspicions.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Divider sx={{ mb: 1.5 }} />
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="h4">Statistical suspicions</Typography>
                  <EvidenceTag kind="inference" />
                </Stack>
                <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
                  Not rule breaches. These need a human decision before they mean anything.
                </Typography>
                <Stack component="ul" sx={{ pl: 2.5, m: 0 }}>
                  {report.suspicions.map((suspicion) => (
                    <Typography component="li" variant="body2" key={suspicion}>
                      {suspicion}
                    </Typography>
                  ))}
                </Stack>
              </Box>
            )}
          </Box>
        )}
      </Collapse>

      {items.length === 0 && !rules.isLoading && (
        <Grid container>
          <Grid item xs={12}>
            <Alert severity="info">
              No rule could be proposed from this profile. Add one for any column whose constraint you know.
            </Alert>
          </Grid>
        </Grid>
      )}

      <RuleDialog
        open={dialogOpen}
        columns={columns}
        onClose={() => setDialogOpen(false)}
        onSave={(rule) => save.mutate([rule])}
      />
    </Paper>
  )
}
