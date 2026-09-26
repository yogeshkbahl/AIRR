import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { ColumnProfile, DatasetPreview, QualityReport, QualityRule, QualityRuleSet } from '../api/types'
import { theme } from '../theme'

vi.mock('../api/client', () => ({
  api: {
    qualityRules: vi.fn(),
    saveQualityRules: vi.fn(),
    evaluateQualityRules: vi.fn(),
    qualityReportCsvUrl: (id: string) => `/api/v1/datasets/${id}/quality-report.csv`,
    preview: vi.fn(),
  },
  onLlmActivity: () => () => {},
}))

const { api } = await import('../api/client')
const { default: QualityRulesPanel } = await import('../components/QualityRulesPanel')
const { default: DataPreview } = await import('../components/DataPreview')

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>{ui}</ThemeProvider>
    </QueryClientProvider>,
  )
}

const column = (overrides: Partial<ColumnProfile>): ColumnProfile =>
  ({
    name: 'region',
    label: 'Region',
    position: 0,
    physical_type: 'string',
    analytical_role: 'categorical_dimension',
    role_source: 'inferred',
    role_reason: '',
    default_aggregation: 'count',
    non_null_count: 10,
    null_count: 0,
    null_percent: 0,
    distinct_count: 4,
    uniqueness_ratio: 0.4,
    is_sensitive: false,
    sensitive_kind: null,
    hierarchy: null,
    hierarchy_level: null,
    numeric: null,
    categorical: null,
    date: null,
    sample_values: [],
    ...overrides,
  }) as ColumnProfile

const COLUMNS = [
  column({}),
  column({ name: 'revenue_amount', label: 'Revenue amount', analytical_role: 'currency' }),
  column({ name: 'customer_email', label: 'Customer email', analytical_role: 'sensitive', is_sensitive: true }),
]

const rule = (overrides: Partial<QualityRule> = {}): QualityRule => ({
  id: 'rule-required-region',
  column: 'region',
  kind: 'required',
  enabled: false,
  severity: 'high',
  description: 'Region drives every regional report.',
  allowed: [],
  min_value: null,
  max_value: null,
  inclusive: true,
  pattern: null,
  earliest: null,
  latest: null,
  allow_future: true,
  max_fail_percent: 0,
  origin: 'suggested',
  remediation: 'Fix the extract that leaves this column empty.',
  ...overrides,
})

const ruleSet = (rules: QualityRule[]): QualityRuleSet => ({
  dataset_id: 'ds1',
  rules,
  updated_at: null,
  schema_fingerprint: 'abc',
  dropped_on_restore: [],
})

const report = (overrides: Partial<QualityReport> = {}): QualityReport => ({
  dataset_id: 'ds1',
  evaluated_at: new Date('2026-01-01T10:00:00Z').toISOString(),
  analyzed_rows: 4045,
  sampled: false,
  rules_evaluated: 2,
  rules_passed: 1,
  rules_failed: 1,
  rules_skipped: 0,
  results: [
    {
      rule_id: 'rule-required-region',
      column: 'region',
      kind: 'required',
      label: 'Region: required',
      expression: '"region" IS NOT NULL AND TRIM("region") <> \'\'',
      severity: 'high',
      passed: false,
      evaluated_rows: 4045,
      passed_rows: 4010,
      failed_rows: 35,
      failed_percent: 0.865,
      skipped_reason: null,
      sample_values: ['(null)'],
      remediation: 'Fix the extract that leaves this column empty.',
      evidence_kind: 'fact',
    },
    {
      rule_id: 'rule-range-revenue',
      column: 'revenue_amount',
      kind: 'numeric_range',
      label: 'Revenue amount: numeric range',
      expression: '"revenue_amount" >= 0',
      severity: 'medium',
      passed: true,
      evaluated_rows: 4045,
      passed_rows: 4045,
      failed_rows: 0,
      failed_percent: 0,
      skipped_reason: null,
      sample_values: [],
      remediation: '',
      evidence_kind: 'fact',
    },
  ],
  suspicions: ["'Revenue amount' has 135 values outside the IQR fence (Tukey fence)"],
  note: 'Rule results are confirmed breaches of a stated constraint.',
  ...overrides,
})

const preview = (overrides: Partial<DatasetPreview> = {}): DatasetPreview => ({
  dataset_id: 'ds1',
  columns: ['region', 'revenue_amount', 'customer_email'],
  rows: [
    { region: 'North', revenue_amount: 120.5, customer_email: 'u***@example.com' },
    { region: 'South', revenue_amount: 90.25, customer_email: 'p***@example.com' },
  ],
  offset: 0,
  limit: 25,
  returned_rows: 2,
  total_rows: 4045,
  filtered_rows: 4045,
  sort_by: null,
  sort_desc: false,
  masked_columns: ['customer_email'],
  note: '1 column(s) holding personal data are masked in this view.',
  ...overrides,
})

beforeEach(() => {
  vi.mocked(api.qualityRules).mockReset()
  vi.mocked(api.saveQualityRules).mockReset()
  vi.mocked(api.evaluateQualityRules).mockReset()
  vi.mocked(api.preview).mockReset()
})

describe('quality rules panel', () => {
  it('presents suggestions as proposals that are switched off', async () => {
    vi.mocked(api.qualityRules).mockResolvedValue(ruleSet([rule()]))
    wrap(<QualityRulesPanel datasetId="ds1" columns={COLUMNS} />)

    expect(await screen.findByText('proposed')).toBeInTheDocument()
    const toggle = screen.getByRole('checkbox', { name: /Enable required rule on region/ })
    expect(toggle).not.toBeChecked()
    // Nothing can be evaluated until a person agrees to a rule.
    expect(screen.getByRole('button', { name: /Evaluate 0 rules/ })).toBeDisabled()
  })

  it('enables a rule through the API rather than locally', async () => {
    vi.mocked(api.qualityRules).mockResolvedValue(ruleSet([rule()]))
    vi.mocked(api.saveQualityRules).mockResolvedValue(ruleSet([rule({ enabled: true, origin: 'user' })]))
    wrap(<QualityRulesPanel datasetId="ds1" columns={COLUMNS} />)

    await userEvent.click(await screen.findByRole('checkbox', { name: /Enable required rule on region/ }))
    await waitFor(() => expect(vi.mocked(api.saveQualityRules)).toHaveBeenCalledOnce())
    const [, sent] = vi.mocked(api.saveQualityRules).mock.calls[0]
    expect(sent[0]).toMatchObject({ id: 'rule-required-region', enabled: true, origin: 'user' })
  })

  it('shows a breach as a fact with its expression, count and next step', async () => {
    vi.mocked(api.qualityRules).mockResolvedValue(ruleSet([rule({ enabled: true })]))
    vi.mocked(api.evaluateQualityRules).mockResolvedValue(report())
    wrap(<QualityRulesPanel datasetId="ds1" columns={COLUMNS} />)

    await userEvent.click(await screen.findByRole('button', { name: /Evaluate 1 rule/ }))

    expect(await screen.findByText('Region: required')).toBeInTheDocument()
    expect(screen.getByText('fail')).toBeInTheDocument()
    expect(screen.getByText(/IS NOT NULL/)).toBeInTheDocument()
    expect(screen.getByText('35')).toBeInTheDocument()
    expect(screen.getByText(/Fix the extract/)).toBeInTheDocument()
    expect(screen.getByText('Fact')).toBeInTheDocument()
  })

  it('keeps statistical suspicions separate from rule breaches', async () => {
    vi.mocked(api.qualityRules).mockResolvedValue(ruleSet([rule({ enabled: true })]))
    vi.mocked(api.evaluateQualityRules).mockResolvedValue(report())
    wrap(<QualityRulesPanel datasetId="ds1" columns={COLUMNS} />)

    await userEvent.click(await screen.findByRole('button', { name: /Evaluate 1 rule/ }))
    expect(await screen.findByText('Statistical suspicions')).toBeInTheDocument()
    expect(screen.getByText(/need a human decision/)).toBeInTheDocument()
    expect(screen.getByText('Inference')).toBeInTheDocument()
  })

  it('hides passing rules until asked', async () => {
    vi.mocked(api.qualityRules).mockResolvedValue(ruleSet([rule({ enabled: true })]))
    vi.mocked(api.evaluateQualityRules).mockResolvedValue(report())
    wrap(<QualityRulesPanel datasetId="ds1" columns={COLUMNS} />)

    await userEvent.click(await screen.findByRole('button', { name: /Evaluate 1 rule/ }))
    expect(screen.queryByText('Revenue amount: numeric range')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: /Show passing rules/i }))
    expect(await screen.findByText('Revenue amount: numeric range')).toBeInTheDocument()
  })

  it('surfaces a rejected rule instead of failing silently', async () => {
    vi.mocked(api.qualityRules).mockResolvedValue(ruleSet([rule()]))
    vi.mocked(api.saveQualityRules).mockRejectedValue(
      Object.assign(new Error("The pattern on 'region' is not a valid regular expression."), {
        requestId: 'req-9',
      }),
    )
    wrap(<QualityRulesPanel datasetId="ds1" columns={COLUMNS} />)

    await userEvent.click(await screen.findByRole('checkbox', { name: /Enable required rule on region/ }))
    expect(await screen.findByText(/not a valid regular expression/)).toBeInTheDocument()
  })
})

describe('data preview', () => {
  it('shows masked columns and the row window', async () => {
    vi.mocked(api.preview).mockResolvedValue(preview())
    wrap(<DataPreview datasetId="ds1" columns={COLUMNS} />)

    expect(await screen.findByText('u***@example.com')).toBeInTheDocument()
    expect(screen.getByText('masked')).toBeInTheDocument()
    expect(screen.getByText(/rows 1–2 of 4,045/)).toBeInTheDocument()
  })

  it('never offers a personal-data column as a filter', async () => {
    vi.mocked(api.preview).mockResolvedValue(preview())
    wrap(<DataPreview datasetId="ds1" columns={COLUMNS} />)

    await screen.findByText('u***@example.com')
    await userEvent.click(screen.getByLabelText('Filter column'))
    const options = await screen.findAllByRole('option')
    const labels = options.map((option) => option.textContent)
    expect(labels).toContain('Region')
    expect(labels).not.toContain('Customer email')
  })

  it('requests the next page by offset', async () => {
    vi.mocked(api.preview).mockResolvedValue(preview())
    wrap(<DataPreview datasetId="ds1" columns={COLUMNS} />)

    await screen.findByText('u***@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() =>
      expect(vi.mocked(api.preview)).toHaveBeenLastCalledWith('ds1', expect.objectContaining({ offset: 25 })),
    )
  })

  it('sorts by a clicked column', async () => {
    vi.mocked(api.preview).mockResolvedValue(preview())
    wrap(<DataPreview datasetId="ds1" columns={COLUMNS} />)

    await screen.findByText('u***@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Sort by Revenue amount' }))
    await waitFor(() =>
      expect(vi.mocked(api.preview)).toHaveBeenLastCalledWith(
        'ds1',
        expect.objectContaining({ sort_by: 'revenue_amount', sort_desc: false }),
      ),
    )
  })

  it('explains an empty filter result', async () => {
    vi.mocked(api.preview).mockResolvedValue(preview({ rows: [], returned_rows: 0, filtered_rows: 0 }))
    wrap(<DataPreview datasetId="ds1" columns={COLUMNS} />)
    expect(await screen.findByText(/No rows match this filter/)).toBeInTheDocument()
  })
})
