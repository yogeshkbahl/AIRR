import type {
  AnalysisPlan, Anomaly, AnomalyPreview, AuditRow, CacheReport, ChartAdviceResponse, ChartData,
  ChartSpec, ColumnProfile, DatasetOverview, DatasetPreview, HealthResponse, PlanResult,
  PreviewRequest, QualityReport, QualityRule, QualityRuleSet, QuestionResponse, QuickAskList,
  RecommendationSet, RelationshipDetail, RelationshipMatrix, SessionUsage, Storyboard,
  UploadResponse, WorkspaceState, ApiErrorBody, BusinessInterpretation,
} from './types'

const BASE = '/api/v1'

/**
 * ENH-01: anything that can reach a provider announces itself here, so the
 * usage panel refetches once per completed request instead of polling.
 */
type UsageListener = (datasetId: string) => void
const usageListeners = new Set<UsageListener>()

export function onLlmActivity(listener: UsageListener): () => void {
  usageListeners.add(listener)
  return () => usageListeners.delete(listener)
}

function announceLlmActivity(datasetId: string): void {
  usageListeners.forEach((listener) => listener(datasetId))
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init?.headers
        : { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })

  if (!response.ok) {
    let body: Partial<ApiErrorBody> = {}
    try {
      body = await response.json()
    } catch {
      body = {}
    }
    throw new ApiError(
      response.status,
      body.error ?? 'request_failed',
      body.detail ?? `The request failed with status ${response.status}.`,
      body.request_id ?? response.headers.get('x-request-id') ?? 'unknown',
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

/** Wraps a call that may consume tokens and notifies the usage panel after it. */
async function llmCall<T>(datasetId: string, run: () => Promise<T>): Promise<T> {
  try {
    return await run()
  } finally {
    announceLlmActivity(datasetId)
  }
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  probeSheets: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ sheets: string[] }>('/datasets/probe-sheets', { method: 'POST', body: form })
  },

  upload: (input: { file: File; businessContext: string; sheet?: string | null }) => {
    const form = new FormData()
    form.append('file', input.file)
    form.append('business_context', input.businessContext)
    if (input.sheet) form.append('sheet', input.sheet)
    return request<UploadResponse>('/datasets', { method: 'POST', body: form })
  },

  overview: (id: string) => request<DatasetOverview>(`/datasets/${id}/overview`),

  summary: (id: string, provider: string) =>
    llmCall(id, () =>
      request<{ interpretation: BusinessInterpretation; llm_used: boolean; provider: string }>(
        `/datasets/${id}/summary?provider=${encodeURIComponent(provider)}`,
        { method: 'POST' },
      ),
    ),

  columns: (id: string) => request<ColumnProfile[]>(`/datasets/${id}/columns`),

  overrideColumns: (
    id: string,
    overrides: { name: string; analytical_role?: string; default_aggregation?: string; label?: string }[],
  ) => request<ColumnProfile[]>(`/datasets/${id}/columns`, { method: 'PUT', body: JSON.stringify(overrides) }),

  anomalies: (id: string) => request<Anomaly[]>(`/datasets/${id}/anomalies`),

  anomalyRows: (id: string, anomalyId: string) =>
    request<AnomalyPreview>(`/datasets/${id}/anomalies/${anomalyId}/rows`),

  relationships: (id: string, columns?: string[]) =>
    request<RelationshipMatrix>(`/datasets/${id}/relationships`, json({ columns: columns ?? null })),

  relationshipPair: (
    id: string,
    body: { column_x: string; column_y: string; method?: string | null; segment_by?: string | null },
  ) => request<RelationshipDetail>(`/datasets/${id}/relationships/pair`, json(body)),

  recommendations: (id: string, body: { provider: string; count?: number }) =>
    llmCall(id, () => request<RecommendationSet>(`/datasets/${id}/recommendations`, json(body))),

  chartAdvice: (
    id: string,
    body: { columns: string[]; question?: string; use_llm: boolean; selection_fingerprint?: string },
    provider: string,
    signal?: AbortSignal,
  ) =>
    llmCall(id, () =>
      request<ChartAdviceResponse>(
        `/datasets/${id}/chart-advice?provider=${encodeURIComponent(provider)}`,
        { ...json(body), signal },
      ),
    ),

  chartData: (id: string, spec: ChartSpec) => request<ChartData>(`/datasets/${id}/chart-data`, json(spec)),

  ask: (
    id: string,
    body: {
      question: string
      columns: string[]
      use_llm: boolean
      quick_ask_id?: string | null
      client_request_id?: string
    },
    provider: string,
    signal?: AbortSignal,
  ) =>
    llmCall(id, () =>
      request<QuestionResponse>(`/datasets/${id}/questions?provider=${encodeURIComponent(provider)}`, {
        ...json(body),
        signal,
      }),
    ),

  quickAsks: (id: string) => request<QuickAskList>(`/datasets/${id}/quick-asks`),

  llmUsage: (id: string, provider: string) =>
    request<SessionUsage>(`/datasets/${id}/llm-usage?provider=${encodeURIComponent(provider)}`),

  workspaceState: (id: string) => request<WorkspaceState>(`/datasets/${id}/workspace-state`),

  saveWorkspaceState: (id: string, state: WorkspaceState) =>
    request<WorkspaceState>(`/datasets/${id}/workspace-state`, {
      method: 'PUT',
      body: JSON.stringify(state),
    }),

  cacheReport: (id: string) => request<CacheReport>(`/datasets/${id}/cache`),

  preview: (id: string, body: PreviewRequest) => request<DatasetPreview>(`/datasets/${id}/preview`, json(body)),

  qualityRules: (id: string) => request<QualityRuleSet>(`/datasets/${id}/quality-rules`),

  saveQualityRules: (id: string, rules: QualityRule[]) =>
    request<QualityRuleSet>(`/datasets/${id}/quality-rules`, { method: 'PUT', body: JSON.stringify(rules) }),

  evaluateQualityRules: (id: string) =>
    request<QualityReport>(`/datasets/${id}/quality-rules/evaluate`, { method: 'POST' }),

  qualityReportCsvUrl: (id: string) => `${BASE}/datasets/${id}/quality-report.csv`,

  runPlan: (id: string, plan: AnalysisPlan) => request<PlanResult>(`/datasets/${id}/plan`, json(plan)),

  storyboard: (id: string) => request<Storyboard>(`/datasets/${id}/storyboard`),

  saveStoryboard: (id: string, board: Storyboard) =>
    request<Storyboard>(`/datasets/${id}/storyboard`, { method: 'PUT', body: JSON.stringify(board) }),

  audit: (id: string) => request<AuditRow[]>(`/datasets/${id}/audit`),

  remove: (id: string) => request<{ deleted: boolean }>(`/datasets/${id}`, { method: 'DELETE' }),

  exportJsonUrl: (id: string) => `${BASE}/datasets/${id}/storyboard/export.json`,
  exportHtmlUrl: (id: string) => `${BASE}/datasets/${id}/storyboard/export.html`,
}
