import type {
  AnalysisPlan, Anomaly, AnomalyPreview, AuditRow, ChartAdviceResponse, ChartData, ChartSpec,
  ColumnProfile, DatasetOverview, HealthResponse, PlanResult, QuestionResponse,
  RecommendationSet, RelationshipDetail, RelationshipMatrix, Storyboard, UploadResponse,
  ApiErrorBody, BusinessInterpretation,
} from './types'

const BASE = '/api/v1'

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
    request<{ interpretation: BusinessInterpretation; llm_used: boolean; provider: string }>(
      `/datasets/${id}/summary?provider=${encodeURIComponent(provider)}`,
      { method: 'POST' },
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
    request<RecommendationSet>(`/datasets/${id}/recommendations`, json(body)),

  chartAdvice: (id: string, body: { columns: string[]; question?: string; use_llm: boolean }, provider: string) =>
    request<ChartAdviceResponse>(
      `/datasets/${id}/chart-advice?provider=${encodeURIComponent(provider)}`,
      json(body),
    ),

  chartData: (id: string, spec: ChartSpec) => request<ChartData>(`/datasets/${id}/chart-data`, json(spec)),

  ask: (id: string, body: { question: string; columns: string[]; use_llm: boolean }, provider: string) =>
    request<QuestionResponse>(`/datasets/${id}/questions?provider=${encodeURIComponent(provider)}`, json(body)),

  runPlan: (id: string, plan: AnalysisPlan) => request<PlanResult>(`/datasets/${id}/plan`, json(plan)),

  storyboard: (id: string) => request<Storyboard>(`/datasets/${id}/storyboard`),

  saveStoryboard: (id: string, board: Storyboard) =>
    request<Storyboard>(`/datasets/${id}/storyboard`, { method: 'PUT', body: JSON.stringify(board) }),

  audit: (id: string) => request<AuditRow[]>(`/datasets/${id}/audit`),

  remove: (id: string) => request<{ deleted: boolean }>(`/datasets/${id}`, { method: 'DELETE' }),

  exportJsonUrl: (id: string) => `${BASE}/datasets/${id}/storyboard/export.json`,
  exportHtmlUrl: (id: string) => `${BASE}/datasets/${id}/storyboard/export.html`,
}
