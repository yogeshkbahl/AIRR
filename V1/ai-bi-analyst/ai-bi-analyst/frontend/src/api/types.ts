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
