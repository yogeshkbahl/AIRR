import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { DatasetOverview, SessionUsage } from '../api/types'
import { theme } from '../theme'

vi.mock('../api/client', () => ({
  api: {
    llmUsage: vi.fn(),
    overview: vi.fn(),
  },
  onLlmActivity: () => () => {},
}))

const { api } = await import('../api/client')
const { default: UsagePanel } = await import('../components/UsagePanel')
const { default: DatasetSummaryTiles } = await import('../components/DatasetSummaryTiles')

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>{ui}</ThemeProvider>
    </QueryClientProvider>,
  )
}

const usage = (overrides: Partial<SessionUsage> = {}): SessionUsage => ({
  dataset_id: 'ds1',
  state: 'ok',
  requested_provider: 'anthropic',
  resolved_provider: 'anthropic',
  resolved_model: 'claude-sonnet-4-5-20250929',
  last: {
    request_id: 'req-1',
    at: new Date('2026-01-01T10:00:00Z').toISOString(),
    action: 'recommendations',
    provider: 'anthropic',
    model: 'claude-sonnet-4-5-20250929',
    prompt_version: 'report_recommendations@1.3',
    ok: true,
    error: null,
    usage: {
      input_tokens: 1200,
      output_tokens: 340,
      total_tokens: 1540,
      cached_read_tokens: 512,
      cache_write_tokens: null,
      reasoning_tokens: null,
    },
  },
  totals: {
    requests: 2,
    input_tokens: 2400,
    output_tokens: 680,
    total_tokens: 3080,
    cached_read_tokens: 512,
    cache_write_tokens: 0,
    reasoning_tokens: 0,
  },
  records: [],
  note: 'Session usage for this dataset only. Not billing data.',
  is_session_usage: true,
  ...overrides,
})

const overview = (overrides: Partial<DatasetOverview> = {}): DatasetOverview => ({
  dataset_id: 'ds1',
  filename: 'orders.csv',
  created_at: new Date('2026-01-01T10:00:00Z').toISOString(),
  row_count: 4045,
  column_count: 20,
  analyzed_row_count: 4045,
  sampled: false,
  sample_method: null,
  file_size_bytes: 765337,
  memory_bytes: 900000,
  duplicate_row_count: 45,
  duplicate_row_percent: 1.11,
  missing_cell_count: 1276,
  missing_cell_percent: 1.58,
  role_counts: { numeric: 7 },
  distinct_category_values: 19,
  categorical_column_count: 5,
  quality: { score: 91.9, grade: 'B', formula: 'f', components: [] },
  business_context: '',
  ingestion_notes: [],
  ...overrides,
})

beforeEach(() => {
  vi.mocked(api.llmUsage).mockReset()
  vi.mocked(api.overview).mockReset()
})

describe('ENH-01 usage panel', () => {
  it('shows the model the backend actually used and the session total', async () => {
    vi.mocked(api.llmUsage).mockResolvedValue(usage())
    wrap(<UsagePanel datasetId="ds1" provider="anthropic" />)

    expect(await screen.findByText('claude-sonnet-4-5-20250929')).toBeInTheDocument()
    expect(screen.getByText('anthropic')).toBeInTheDocument()
    expect(screen.getByText('3,080')).toBeInTheDocument()
    expect(screen.getByText('1,200 in / 340 out')).toBeInTheDocument()
    expect(screen.getByText(/2 model requests this dataset/)).toBeInTheDocument()
  })

  it('says the provider is not configured and does not invent a total', async () => {
    vi.mocked(api.llmUsage).mockResolvedValue(
      usage({
        state: 'not_configured',
        requested_provider: 'openai',
        resolved_provider: 'heuristic',
        resolved_model: 'rule-based-narrator',
        last: null,
        totals: {
          requests: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0,
          cached_read_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0,
        },
        note: 'openai has no server-side key, so the built-in narrator answered.',
      }),
    )
    wrap(<UsagePanel datasetId="ds1" provider="openai" />)

    expect(await screen.findByText('LLM not configured')).toBeInTheDocument()
    expect(screen.getByText('0 / 0')).toBeInTheDocument()
    expect(screen.getByText('rule-based-narrator')).toBeInTheDocument()
  })

  it('reports "Not used yet" for the built-in narrator', async () => {
    vi.mocked(api.llmUsage).mockResolvedValue(
      usage({
        state: 'not_used',
        resolved_provider: 'heuristic',
        resolved_model: 'rule-based-narrator',
        last: null,
        totals: {
          requests: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0,
          cached_read_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0,
        },
      }),
    )
    wrap(<UsagePanel datasetId="ds1" provider="heuristic" />)
    expect(await screen.findByText('Not used yet')).toBeInTheDocument()
  })

  it('keeps the confirmed total visible when the last request failed', async () => {
    vi.mocked(api.llmUsage).mockResolvedValue(
      usage({
        state: 'error',
        last: {
          request_id: 'req-2',
          at: new Date().toISOString(),
          action: 'question',
          provider: 'anthropic',
          model: 'claude-sonnet-4-5',
          prompt_version: 'executive_answer@1.3',
          ok: false,
          error: 'Anthropic rate limited (HTTP 429).',
          usage: {
            input_tokens: 0, output_tokens: 0, total_tokens: 0,
            cached_read_tokens: null, cache_write_tokens: null, reasoning_tokens: null,
          },
        },
      }),
    )
    wrap(<UsagePanel datasetId="ds1" provider="anthropic" />)

    expect(await screen.findByText('Last request failed')).toBeInTheDocument()
    expect(screen.getByText('3,080')).toBeInTheDocument() // total preserved
  })

  it('shows cached-token detail in the popover and labels the figures as session usage', async () => {
    vi.mocked(api.llmUsage).mockResolvedValue(usage())
    wrap(<UsagePanel datasetId="ds1" provider="anthropic" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Usage details' }))
    expect(await screen.findByText('Cached input read')).toBeInTheDocument()
    expect(screen.getByText('512')).toBeInTheDocument()
    // The label appears in the panel note and again in the popover footer.
    expect(screen.getAllByText(/Not billing data/).length).toBeGreaterThan(0)
  })

  it('never renders a key or an authorization header', async () => {
    vi.mocked(api.llmUsage).mockResolvedValue(usage())
    const { container } = wrap(<UsagePanel datasetId="ds1" provider="anthropic" />)
    await screen.findByText('claude-sonnet-4-5-20250929')
    const text = container.textContent?.toLowerCase() ?? ''
    for (const forbidden of ['api_key', 'x-api-key', 'authorization', 'sk-']) {
      expect(text).not.toContain(forbidden)
    }
  })
})

describe('ENH-02 summary tiles', () => {
  it('renders the four authoritative numbers with their denominators', async () => {
    vi.mocked(api.overview).mockResolvedValue(overview())
    wrap(<DatasetSummaryTiles datasetId="ds1" />)

    expect(await screen.findByText('4,045')).toBeInTheDocument()
    expect(screen.getByText('20')).toBeInTheDocument()
    expect(screen.getByText('45')).toBeInTheDocument()
    expect(screen.getByText('1,276')).toBeInTheDocument()
    expect(screen.getByText(/matched on every column/)).toBeInTheDocument()
    expect(screen.getByText(/blank strings are reported separately/)).toBeInTheDocument()
  })

  it('reads the same query as Overview, so one fetch serves both pages', async () => {
    vi.mocked(api.overview).mockResolvedValue(overview())
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <DatasetSummaryTiles datasetId="ds1" />
          <DatasetSummaryTiles datasetId="ds1" />
        </ThemeProvider>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getAllByText('4,045')).toHaveLength(2))
    expect(vi.mocked(api.overview)).toHaveBeenCalledTimes(1)
  })

  it('shows no numbers at all when the summary cannot be loaded', async () => {
    vi.mocked(api.overview).mockRejectedValue(new Error('offline'))
    wrap(<DatasetSummaryTiles datasetId="ds1" />)
    expect(await screen.findByText(/blank rather than showing numbers from another/)).toBeInTheDocument()
    expect(screen.queryByText('4,045')).not.toBeInTheDocument()
  })

  it('does not show one dataset\u2019s numbers under another dataset', async () => {
    vi.mocked(api.overview).mockResolvedValue(overview({ dataset_id: 'other-dataset' }))
    wrap(<DatasetSummaryTiles datasetId="ds1" />)
    await waitFor(() => expect(screen.queryByText('4,045')).not.toBeInTheDocument())
  })
})
