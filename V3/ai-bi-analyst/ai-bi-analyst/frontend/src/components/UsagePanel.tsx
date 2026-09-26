import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Box, Chip, CircularProgress, Divider, IconButton, Popover, Stack, Table, TableBody, TableCell,
  TableRow, Tooltip, Typography,
} from '@mui/material'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import { api, onLlmActivity } from '../api/client'
import type { SessionUsage } from '../api/types'
import { palette } from '../theme'

/**
 * ENH-01 — anchored at the bottom of the left navigation on every route.
 *
 * Provider and model come from backend response metadata, not from the UI
 * selector, so if a provider is requested without a server key the panel says
 * which narrator actually answered. Token counts are only ever the ones the
 * provider reported; nothing here estimates. The panel refetches once per
 * completed request via the API client's activity notifier, and totals are a
 * server-side sum over unique request ids, so a replayed frontend call cannot
 * double count.
 */

const STATE_COLOR: Record<SessionUsage['state'], string> = {
  ok: palette.evidence.inference,
  not_used: palette.muted,
  not_configured: palette.severity.medium,
  error: palette.severity.high,
}

const STATE_LABEL: Record<SessionUsage['state'], string> = {
  ok: 'Live',
  not_used: 'Not used yet',
  not_configured: 'LLM not configured',
  error: 'Last request failed',
}

function useSessionUsage(datasetId: string, provider: string) {
  const queryClient = useQueryClient()
  const key = ['llm-usage', datasetId, provider]

  const query = useQuery({
    queryKey: key,
    queryFn: () => api.llmUsage(datasetId, provider),
    enabled: Boolean(datasetId),
    staleTime: 0,
  })

  useEffect(() => {
    // Refresh after every request that could have consumed tokens, with no
    // page reload and no polling.
    return onLlmActivity((id) => {
      if (id === datasetId) void queryClient.invalidateQueries({ queryKey: ['llm-usage', datasetId] })
    })
  }, [datasetId, queryClient])

  return query
}

function TokenRow({ label, value }: { label: string; value: number | null | undefined }) {
  if (value === null || value === undefined) return null
  return (
    <TableRow>
      <TableCell sx={{ border: 0, py: 0.15, px: 0, color: palette.muted, fontSize: '0.72rem' }}>
        {label}
      </TableCell>
      <TableCell
        align="right"
        className="figure"
        sx={{ border: 0, py: 0.15, px: 0, fontSize: '0.72rem' }}
      >
        {value.toLocaleString()}
      </TableCell>
    </TableRow>
  )
}

export function UsagePanelCompact({ datasetId, provider }: { datasetId: string; provider: string }) {
  const { data } = useSessionUsage(datasetId, provider)
  const label = data
    ? `${data.resolved_provider ?? provider} · ${data.totals.total_tokens.toLocaleString()} tok`
    : 'usage…'
  return (
    <Tooltip title={data?.note ?? 'Session token usage'}>
      <Chip
        size="small"
        label={label}
        aria-label={`Model usage: ${label}`}
        sx={{ backgroundColor: palette.primaryTint, maxWidth: 200 }}
      />
    </Tooltip>
  )
}

export default function UsagePanel({ datasetId, provider }: { datasetId: string; provider: string }) {
  const { data, isLoading, isFetching, error } = useSessionUsage(datasetId, provider)
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)

  const last = data?.last
  const hasDetail = Boolean(
    last?.usage.cached_read_tokens || last?.usage.cache_write_tokens || last?.usage.reasoning_tokens,
  )

  return (
    <Box
      component="section"
      aria-label="Model and token usage"
      sx={{
        borderTop: `1px solid ${palette.line}`,
        backgroundColor: palette.sunken,
        px: 2,
        py: 1.25,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
        <Box
          aria-hidden
          sx={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            backgroundColor: data ? STATE_COLOR[data.state] : palette.muted,
          }}
        />
        <Typography variant="caption" sx={{ fontWeight: 600, color: palette.ink }}>
          {data ? STATE_LABEL[data.state] : 'Model usage'}
        </Typography>
        {isFetching && !isLoading && <CircularProgress size={10} />}
        <Box sx={{ flex: 1 }} />
        <IconButton
          size="small"
          onClick={(event) => setAnchor(event.currentTarget)}
          aria-label="Usage details"
          sx={{ p: 0.25 }}
        >
          <InfoOutlinedIcon sx={{ fontSize: 15 }} />
        </IconButton>
      </Stack>

      {error ? (
        <Typography variant="caption">Usage is unavailable right now.</Typography>
      ) : (
        <>
          <Typography variant="caption" sx={{ display: 'block', color: palette.ink }}>
            {data?.resolved_provider ?? '—'}
          </Typography>
          <Typography
            variant="caption"
            className="figure"
            sx={{ display: 'block', wordBreak: 'break-all', color: palette.muted }}
          >
            {data?.resolved_model ?? '—'}
          </Typography>

          <Divider sx={{ my: 0.75 }} />

          <Stack direction="row" justifyContent="space-between">
            <Typography variant="caption">Last request</Typography>
            <Typography variant="caption" className="figure" sx={{ color: palette.ink }}>
              {last && last.usage.total_tokens > 0
                ? `${last.usage.input_tokens.toLocaleString()} in / ${last.usage.output_tokens.toLocaleString()} out`
                : '0 / 0'}
            </Typography>
          </Stack>
          <Stack direction="row" justifyContent="space-between">
            <Typography variant="caption">Session total</Typography>
            <Typography variant="caption" className="figure" sx={{ fontWeight: 600, color: palette.ink }}>
              {(data?.totals.total_tokens ?? 0).toLocaleString()}
            </Typography>
          </Stack>
          <Typography variant="caption" sx={{ display: 'block', color: palette.muted }}>
            {data?.totals.requests ?? 0} model request{(data?.totals.requests ?? 0) === 1 ? '' : 's'} this dataset
          </Typography>
        </>
      )}

      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Box sx={{ p: 2, maxWidth: 320 }}>
          <Typography variant="h4" sx={{ mb: 0.5 }}>
            Session usage
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', mb: 1 }}>
            {data?.note}
          </Typography>

          {data?.requested_provider && data.requested_provider !== data.resolved_provider && (
            <Typography variant="caption" sx={{ display: 'block', mb: 1, color: palette.severity.medium }}>
              You selected {data.requested_provider}; the backend answered with {data.resolved_provider}.
            </Typography>
          )}

          <Table size="small">
            <TableBody>
              <TokenRow label="Input tokens (total)" value={data?.totals.input_tokens} />
              <TokenRow label="Output tokens (total)" value={data?.totals.output_tokens} />
              <TokenRow label="Cached input read" value={last?.usage.cached_read_tokens} />
              <TokenRow label="Cache write" value={last?.usage.cache_write_tokens} />
              <TokenRow label="Reasoning tokens" value={last?.usage.reasoning_tokens} />
            </TableBody>
          </Table>
          {!hasDetail && (
            <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>
              This provider reported no cache or reasoning breakdown for the last request.
            </Typography>
          )}

          {last && (
            <>
              <Divider sx={{ my: 1 }} />
              <Typography variant="caption" sx={{ display: 'block' }}>
                Last call: {last.action} · {last.prompt_version || 'no prompt version'} ·{' '}
                {new Date(last.at).toLocaleTimeString()}
              </Typography>
              {last.error && (
                <Typography variant="caption" sx={{ display: 'block', color: palette.severity.high }}>
                  {last.error}
                </Typography>
              )}
            </>
          )}
          <Typography variant="caption" sx={{ display: 'block', mt: 1, color: palette.muted }}>
            Session usage for this dataset, counted from provider-reported figures. Not billing data.
          </Typography>
        </Box>
      </Popover>
    </Box>
  )
}
