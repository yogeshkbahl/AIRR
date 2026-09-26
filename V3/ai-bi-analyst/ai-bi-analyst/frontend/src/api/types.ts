/** Mirrors backend/app/schemas.py. Keep the two in step when either changes. */

export type PhysicalType =
  | 'integer' | 'float' | 'decimal' | 'boolean' | 'string' | 'date' | 'datetime' | 'unsupported'

export type AnalyticalRole =
  | 'measure' | 'categorical_dimension' | 'datetime_dimension' | 'identifier'
  | 'free_text' | 'geography' | 'currency' | 'percentage' | 'sensitive' | 'unusable'

export type Aggregation = 'sum' | 'avg' | 'min' | 'max' | 'count' | 'count_distinct' | 'median' | 'none'
export type Severity = 'info' | 'low' | 'medium' | 'high'
export type EvidenceKind = 'fact' | 'inference' | 'suggestion' | 'assumption'
export type ComplexityTier = 'easy' | 'medium' | 'complex' | 'very_complex'

export interface NumericStats {
  min: number | null; max: number | null; mean: number | null; median: number | null
  std: number | null; p25: number | null; p75: number | null; iqr: number | null
  zero_count: number; negative_count: number; skew: number | null
  outlier_count_iqr: number; outlier_count_robust_z: number
  histogram_bins: number[]; histogram_counts: number[]
}

export interface TopValue { value: string; count: number; percent: number }

export interface CategoricalStats {
  top_values: TopValue[]; rare_category_count: number; rare_category_rate: number
  blank_string_count: number; whitespace_issue_count: number; casing_variant_groups: number
  mean_length: number | null; max_length: number | null
}

export interface DateStats {
  earliest: string | null; latest: string | null; span_days: number | null
  invalid_count: number; future_count: number; distinct_days: number | null
  inferred_granularity: string | null; largest_gap_days: number | null
}

export interface ColumnProfile {
  name: string; label: string; position: number
  physical_type: PhysicalType; analytical_role: AnalyticalRole
  role_source: 'inferred' | 'user_override'; role_reason: string
  default_aggregation: Aggregation
  non_null_count: number; null_count: number; null_percent: number
  distinct_count: number; uniqueness_ratio: number
  is_sensitive: boolean; sensitive_kind: string | null
  hierarchy: string | null; hierarchy_level: number | null
  numeric: NumericStats | null; categorical: CategoricalStats | null; date: DateStats | null
  sample_values: string[]
}

export interface QualityComponent { name: string; weight: number; score: number; detail: string }
export interface QualityScore { score: number; grade: string; formula: string; components: QualityComponent[] }

export interface DatasetOverview {
  dataset_id: string; filename: string; created_at: string
  row_count: number; column_count: number; analyzed_row_count: number
  sampled: boolean; sample_method: string | null
  file_size_bytes: number; memory_bytes: number
  duplicate_row_count: number; duplicate_row_percent: number
  missing_cell_count: number; missing_cell_percent: number
  role_counts: Record<string, number>
  distinct_category_values: number; categorical_column_count: number
  quality: QualityScore; business_context: string; ingestion_notes: string[]
}

export interface Anomaly {
  id: string; kind: string; title: string; severity: Severity; columns: string[]
  affected_rows: number; affected_percent: number; method: string
  explanation: string; recommended_action: string; evidence_kind: EvidenceKind
  row_filter: Record<string, unknown> | null
}

export interface RelationshipResult {
  column_x: string; column_y: string; pair_kind: string; method: string
  statistic: number | null; secondary_method: string | null; secondary_statistic: number | null
  p_value: number | null; effect_size_label: string | null
  sample_size: number; missing_dropped: number; reliable: boolean
  warnings: string[]; interpretation: string
}

export interface RelationshipMatrix {
  columns: string[]; labels: string[]
  values: (number | null)[][]; methods: (string | null)[][]
  pairs: RelationshipResult[]; suppressed_pairs: number; note: string
}

export interface SegmentBreakdown {
  segment_column: string; segments: string[]; statistics: (number | null)[]
  sample_sizes: number[]; simpsons_paradox_suspected: boolean; note: string
}

export interface RelationshipDetail {
  result: RelationshipResult; chart: ChartSpec; segment: SegmentBreakdown | null
}

export interface ChartEncoding {
  x?: string | null; y?: string | null; series?: string | null; size?: string | null
  color?: string | null; facet?: string | null; value?: string | null
}

export interface QueryFilter { column: string; op: string; value: unknown }

export interface ChartSpec {
  chart_type: string; title: string; encoding: ChartEncoding; aggregation: Aggregation
  filters: QueryFilter[]; sort: 'asc' | 'desc' | 'natural'; limit: number
  drill_path: string[]; notes: string[]
}

export interface ChartOption {
  chart_type: string; tier: ComplexityTier; compatible: boolean; rank: number | null
  rationale: string; prerequisites: string[]; avoid_when: string[]; blockers: string[]
  spec: ChartSpec | null
}

export interface ChartAdviceResponse {
  selected_columns: string[]; selection_summary: string
  options: ChartOption[]; rejected: ChartOption[]
  llm_used: boolean; llm_note: string | null
  /** ENH-03: identity echoed by the backend so a stale answer can be dropped. */
  dataset_id: string
  selection_fingerprint: string
  request_id: string
  dropped_columns: string[]
  truncated_selection: boolean
}

export interface ChartData {
  spec: ChartSpec; columns: string[]; rows: Record<string, unknown>[]
  row_count: number; truncated: boolean; evidence_kind: EvidenceKind
}

export interface MetricSpec { column: string; aggregation: Aggregation; label: string | null }

export interface AnalysisPlan {
  intent: string; metrics: MetricSpec[]; dimensions: string[]
  time_dimension: string | null; time_grain: string | null
  filters: QueryFilter[]; sort_by: string | null; sort_desc: boolean; limit: number
  assumptions: string[]; clarification_needed: string | null
}

export interface PlanResult {
  plan: AnalysisPlan; columns: string[]; rows: Record<string, unknown>[]
  row_count: number; truncated: boolean; executed_ms: number; sql_like: string
}

export interface BusinessInterpretation {
  dataset_summary: string; likely_grain: string; likely_subject_area: string
  key_measures: string[]; key_dimensions: string[]
  data_quality_call_outs: string[]; open_questions: string[]; evidence_kind: EvidenceKind
}

export interface ReportRecommendation {
  title: string; business_question: string; audience: string; decision_supported: string
  required_columns: string[]; metrics: MetricSpec[]; dimensions: string[]
  filters: string[]; prompts: string[]; drill_path: string[]
  chart_type: string; tier: ComplexityTier; why_this_representation: string
  cautions: string[]; confidence: 'low' | 'medium' | 'high'
  assumptions: string[]; evidence_kind: EvidenceKind; repairs: string[]
}

export interface RecommendationSet {
  dataset_id: string; interpretation: BusinessInterpretation
  recommendations: ReportRecommendation[]; by_tier: Record<string, number[]>
  provider: string; model: string; prompt_version: string; profile_version: string
  generated_at: string; rejected_count: number; rejection_notes: string[]
}

export interface ExecutiveAnswer {
  headline: string; summary: string; evidence: string[]
  caveats: string[]; follow_up_questions: string[]; evidence_kind: EvidenceKind
}

export interface QuestionResponse {
  question: string; plan: AnalysisPlan; result: PlanResult | null
  chart: ChartSpec | null; answer: ExecutiveAnswer
  llm_used: boolean; provider: string; prompt_version: string
  dataset_id: string
  quick_ask_id: string | null
  client_request_id: string | null
  state: 'answered' | 'clarification_required' | 'empty_result'
}

/** ENH-04 */
export type QuickAskKind =
  | 'kpi_overview' | 'count_by_dimension' | 'measure_by_dimension' | 'trend' | 'top_n'
  | 'compare_measures' | 'distribution' | 'drivers'

export type QuickAskCategory =
  | 'kpi' | 'comparison' | 'trend' | 'ranking' | 'distribution' | 'relationship'

export interface QuickAskIntent {
  kind: QuickAskKind
  metrics: MetricSpec[]
  dimensions: string[]
  time_dimension: string | null
  time_grain: string | null
  target_column: string | null
  limit: number
}

export interface QuickAsk {
  id: string
  label: string
  question: string
  category: QuickAskCategory
  intent: QuickAskIntent
  required_columns: string[]
}

export interface QuickAskList {
  dataset_id: string
  schema_fingerprint: string
  items: QuickAsk[]
  note: string
}

/** ENH-01 */
export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cached_read_tokens: number | null
  cache_write_tokens: number | null
  reasoning_tokens: number | null
}

export interface UsageRecord {
  request_id: string
  at: string
  action: string
  provider: string
  model: string
  prompt_version: string
  ok: boolean
  error: string | null
  usage: TokenUsage
}

export interface UsageTotals {
  requests: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cached_read_tokens: number
  cache_write_tokens: number
  reasoning_tokens: number
}

export interface SessionUsage {
  dataset_id: string
  state: 'not_configured' | 'not_used' | 'ok' | 'error'
  requested_provider: string | null
  resolved_provider: string | null
  resolved_model: string | null
  last: UsageRecord | null
  totals: UsageTotals
  records: UsageRecord[]
  note: string
  is_session_usage: boolean
}

/** ENH-05 */
export interface ChartAdvisorState {
  selected_column_ids: string[]
  active_chart_type: string | null
  question: string
  show_rejected: boolean
  scroll_y: number
}

export interface WorkspaceState {
  dataset_id: string
  version: number
  chart_advisor: ChartAdvisorState
  ask: { last_question: string; last_quick_ask_id: string | null }
  recommendations_tier: string
  profile_role_filter: string
  schema_fingerprint: string
  updated_at: string | null
  dropped_on_restore: string[]
}

/** Governed data-quality rules */
export type RuleKind =
  | 'required' | 'unique' | 'accepted_values' | 'numeric_range' | 'regex_format' | 'date_range'

export interface QualityRule {
  id: string
  column: string
  kind: RuleKind
  enabled: boolean
  severity: Severity
  description: string
  allowed: string[]
  min_value: number | null
  max_value: number | null
  inclusive: boolean
  pattern: string | null
  earliest: string | null
  latest: string | null
  allow_future: boolean
  max_fail_percent: number
  origin: 'suggested' | 'user'
  remediation: string
}

export interface QualityRuleSet {
  dataset_id: string
  rules: QualityRule[]
  updated_at: string | null
  schema_fingerprint: string
  dropped_on_restore: string[]
}

export interface RuleResult {
  rule_id: string
  column: string
  kind: RuleKind
  label: string
  expression: string
  severity: Severity
  passed: boolean
  evaluated_rows: number
  passed_rows: number
  failed_rows: number
  failed_percent: number
  skipped_reason: string | null
  sample_values: string[]
  remediation: string
  evidence_kind: EvidenceKind
}

export interface QualityReport {
  dataset_id: string
  evaluated_at: string
  analyzed_rows: number
  sampled: boolean
  rules_evaluated: number
  rules_passed: number
  rules_failed: number
  rules_skipped: number
  results: RuleResult[]
  suspicions: string[]
  note: string
}

/** Dataset preview */
export interface PreviewRequest {
  offset?: number
  limit?: number
  sort_by?: string | null
  sort_desc?: boolean
  filter_column?: string | null
  filter_op?: string | null
  filter_value?: unknown
}

export interface DatasetPreview {
  dataset_id: string
  columns: string[]
  rows: Record<string, unknown>[]
  offset: number
  limit: number
  returned_rows: number
  total_rows: number
  filtered_rows: number
  sort_by: string | null
  sort_desc: boolean
  masked_columns: string[]
  note: string
}

/** ENH-06 */
export interface CacheReport {
  dataset_id: string
  present: boolean
  fingerprint?: string
  size_bytes?: number
  uploaded_at?: string
  cache_schema_version?: string
  analysis_code_version?: string
  app_version?: string
  overrides_hash?: string
  artifacts?: string[]
  disk_bytes?: number
  session_source?: string
  schema_fingerprint?: string
}

export interface StoryboardItem {
  id: string; title: string; description: string
  kind: 'chart' | 'insight' | 'kpi' | 'table'
  spec: ChartSpec | null; text: string | null
  evidence_kind: EvidenceKind; order: number
}

export interface Storyboard {
  dataset_id: string; title: string; description: string
  global_filters: QueryFilter[]; prompts: string[]
  items: StoryboardItem[]; updated_at: string | null
  completeness: Record<string, boolean>
}

export interface UploadResponse {
  dataset_id: string; overview: DatasetOverview
  status: { dataset_id: string; state: string; progress: number; message: string }
  available_sheets: string[]
}

export interface ProviderInfo { id: string; label: string; configured: boolean; model: string }

export interface HealthResponse {
  status: string
  providers: ProviderInfo[]
  /** The backend's LLM_PROVIDER, already resolved to a configured provider. */
  default_provider: string
  limits: {
    max_upload_mb: number; profile_row_limit: number
    query_row_limit: number; session_retention_hours: number
  }
}

export interface AnomalyPreview {
  columns: string[]; rows: Record<string, unknown>[]; row_count: number; hidden_columns: string[]
}

export interface AuditRow {
  at: string; action: string; provider: string | null; model: string | null
  prompt_version: string | null; profile_version: string | null; sampled: number; detail: string
}

export interface ApiErrorBody { error: string; detail: string; request_id: string }
