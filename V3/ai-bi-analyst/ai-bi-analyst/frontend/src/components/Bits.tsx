import type { ReactNode } from 'react'
import {
  Alert, Box, Chip, CircularProgress, Paper, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Tooltip, Typography,
} from '@mui/material'
import type { AnalyticalRole, ComplexityTier, EvidenceKind, Severity } from '../api/types'
import { cellText, titleCase } from '../utils/format'
import { evidenceColor, palette, severityColor, tierColor } from '../theme'

const EVIDENCE_HELP: Record<EvidenceKind, string> = {
  fact: 'Calculated directly from the uploaded file.',
  inference: 'A statistical reading of the calculated figures.',
  suggestion: 'A reporting idea proposed by the narrator. Not a finding.',
  assumption: 'Depends on business knowledge this tool does not have. Confirm before acting.',
}

export function EvidenceTag({ kind, size = 'small' }: { kind: EvidenceKind; size?: 'small' | 'medium' }) {
  return (
    <Tooltip title={EVIDENCE_HELP[kind]}>
      <Chip
        size={size}
        label={titleCase(kind)}
        variant="outlined"
        sx={{ color: evidenceColor(kind), borderColor: evidenceColor(kind) }}
      />
    </Tooltip>
  )
}

export function SeverityChip({ severity }: { severity: Severity }) {
  // Shape carries the meaning as well as colour: filled for action-now, outlined for context.
  const filled = severity === 'high' || severity === 'medium'
  return (
    <Chip
      size="small"
      label={titleCase(severity)}
      variant={filled ? 'filled' : 'outlined'}
      sx={
        filled
          ? { backgroundColor: severityColor(severity), color: '#fff' }
          : { color: severityColor(severity), borderColor: severityColor(severity) }
      }
    />
  )
}

export function TierChip({ tier }: { tier: ComplexityTier }) {
  return (
    <Chip
      size="small"
      label={titleCase(tier)}
      sx={{ backgroundColor: tierColor(tier), color: '#fff' }}
    />
  )
}

const ROLE_HELP: Partial<Record<AnalyticalRole, string>> = {
  measure: 'A number you can aggregate.',
  currency: 'A monetary amount.',
  percentage: 'A rate. Averaging is usually correct; summing usually is not.',
  categorical_dimension: 'A grouping attribute for rows, bars and filters.',
  datetime_dimension: 'A date or timestamp for trends and drill paths.',
  identifier: 'A key. Counted, never summed.',
  geography: 'A location attribute, usable on a map.',
  free_text: 'Long text. Not usable as a dimension.',
  sensitive: 'Looks like personal data. Examples are masked and never sent to a model.',
  unusable: 'Empty or otherwise unusable for analysis.',
}

export function RoleChip({ role, reason }: { role: AnalyticalRole; reason?: string }) {
  return (
    <Tooltip title={[ROLE_HELP[role], reason].filter(Boolean).join(' ')}>
      <Chip size="small" variant="outlined" label={titleCase(role)} />
    </Tooltip>
  )
}

export function SectionHeader({
  title, description, action,
}: { title: string; description?: string; action?: ReactNode }) {
  return (
    <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ mb: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="h1">{title}</Typography>
        {description && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, maxWidth: '72ch' }}>
            {description}
          </Typography>
        )}
      </Box>
      {action}
    </Stack>
  )
}

export function Loading({ label = 'Computing…' }: { label?: string }) {
  return (
    <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 4 }}>
      <CircularProgress size={18} />
      <Typography variant="body2" color="text.secondary">{label}</Typography>
    </Stack>
  )
}

export function ErrorView({ error, action }: { error: unknown; action?: ReactNode }) {
  const message =
    error && typeof error === 'object' && 'message' in error
      ? String((error as { message: string }).message)
      : 'The request failed.'
  const requestId =
    error && typeof error === 'object' && 'requestId' in error
      ? String((error as { requestId: string }).requestId)
      : undefined
  return (
    <Alert severity="error" action={action}>
      <Typography variant="body2">{message}</Typography>
      {requestId && <Typography variant="caption">Request id {requestId}</Typography>}
    </Alert>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Paper sx={{ p: 3, backgroundColor: palette.sunken }}>
      <Typography variant="h4" sx={{ mb: 0.5 }}>{title}</Typography>
      <Typography variant="body2" color="text.secondary">{children}</Typography>
    </Paper>
  )
}

export function KeyFigure({
  label, value, hint, tone = 'ink',
}: { label: string; value: string; hint?: string; tone?: 'ink' | 'warn' | 'primary' }) {
  const color = tone === 'warn' ? palette.severity.medium : tone === 'primary' ? palette.primary : palette.ink
  return (
    <Tooltip title={hint ?? ''}>
      <Paper sx={{ p: 2, height: '100%' }}>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>{label}</Typography>
        <Typography className="figure" sx={{ fontSize: '1.5rem', fontWeight: 600, color, letterSpacing: '-0.02em' }}>
          {value}
        </Typography>
        {hint && (
          <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>{hint}</Typography>
        )}
      </Paper>
    </Tooltip>
  )
}

export function ResultTable({
  columns, rows, maxHeight = 420, dense = true,
}: { columns: string[]; rows: Record<string, unknown>[]; maxHeight?: number; dense?: boolean }) {
  if (!columns.length || !rows.length) {
    return <Typography variant="body2" color="text.secondary">No rows to show.</Typography>
  }
  return (
    <TableContainer sx={{ maxHeight, border: `1px solid ${palette.line}` }}>
      <Table stickyHeader size={dense ? 'small' : 'medium'}>
        <TableHead>
          <TableRow>
            {columns.map((column) => (
              <TableCell key={column}>{column}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={index} hover>
              {columns.map((column) => (
                <TableCell
                  key={column}
                  className={typeof row[column] === 'number' ? 'figure' : undefined}
                  align={typeof row[column] === 'number' ? 'right' : 'left'}
                >
                  {cellText(row[column])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}
