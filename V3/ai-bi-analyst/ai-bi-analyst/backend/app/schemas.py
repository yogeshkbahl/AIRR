"""Typed contracts for the API and for every structured LLM response.

These models are the single source of truth: the frontend types mirror them and
LLM output is rejected unless it validates against them.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------- #
# Semantics
# --------------------------------------------------------------------------- #


class PhysicalType(str, Enum):
    integer = "integer"
    float = "float"
    decimal = "decimal"
    boolean = "boolean"
    string = "string"
    date = "date"
    datetime_ = "datetime"
    unsupported = "unsupported"


class AnalyticalRole(str, Enum):
    measure = "measure"
    categorical_dimension = "categorical_dimension"
    datetime_dimension = "datetime_dimension"
    identifier = "identifier"
    free_text = "free_text"
    geography = "geography"
    currency = "currency"
    percentage = "percentage"
    sensitive = "sensitive"
    unusable = "unusable"


class Aggregation(str, Enum):
    sum = "sum"
    avg = "avg"
    min = "min"
    max = "max"
    count = "count"
    count_distinct = "count_distinct"
    median = "median"
    none = "none"


class Severity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"


class EvidenceKind(str, Enum):
    """Every statement in the product is tagged with one of these."""

    fact = "fact"
    inference = "inference"
    suggestion = "suggestion"
    assumption = "assumption"


class NumericStats(BaseModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    p25: float | None = None
    p75: float | None = None
    iqr: float | None = None
    zero_count: int = 0
    negative_count: int = 0
    skew: float | None = None
    outlier_count_iqr: int = 0
    outlier_count_robust_z: int = 0
    histogram_bins: list[float] = Field(default_factory=list)
    histogram_counts: list[int] = Field(default_factory=list)


class TopValue(BaseModel):
    value: str
    count: int
    percent: float


class CategoricalStats(BaseModel):
    top_values: list[TopValue] = Field(default_factory=list)
    rare_category_count: int = 0
    rare_category_rate: float = 0.0
    blank_string_count: int = 0
    whitespace_issue_count: int = 0
    casing_variant_groups: int = 0
    mean_length: float | None = None
    max_length: int | None = None


class DateStats(BaseModel):
    earliest: str | None = None
    latest: str | None = None
    span_days: int | None = None
    invalid_count: int = 0
    future_count: int = 0
    distinct_days: int | None = None
    inferred_granularity: str | None = None
    largest_gap_days: int | None = None


class ColumnProfile(BaseModel):
    name: str
    label: str
    position: int
    physical_type: PhysicalType
    analytical_role: AnalyticalRole
    role_source: Literal["inferred", "user_override"] = "inferred"
    role_reason: str = ""
    default_aggregation: Aggregation = Aggregation.none
    non_null_count: int
    null_count: int
    null_percent: float
    distinct_count: int
    uniqueness_ratio: float
    is_sensitive: bool = False
    sensitive_kind: str | None = None
    hierarchy: str | None = None
    hierarchy_level: int | None = None
    numeric: NumericStats | None = None
    categorical: CategoricalStats | None = None
    date: DateStats | None = None
    sample_values: list[str] = Field(default_factory=list)


class QualityComponent(BaseModel):
    name: str
    weight: float
    score: float
    detail: str


class QualityScore(BaseModel):
    score: float
    grade: str
    formula: str
    components: list[QualityComponent]


class DatasetOverview(BaseModel):
    dataset_id: str
    filename: str
    created_at: datetime
    row_count: int
    column_count: int
    analyzed_row_count: int
    sampled: bool
    sample_method: str | None = None
    file_size_bytes: int
    memory_bytes: int
    duplicate_row_count: int
    duplicate_row_percent: float
    missing_cell_count: int
    missing_cell_percent: float
    role_counts: dict[str, int]
    distinct_category_values: int = Field(
        description="Sum of distinct values across categorical columns (not the column count)."
    )
    categorical_column_count: int
    quality: QualityScore
    business_context: str = ""
    ingestion_notes: list[str] = Field(default_factory=list)


class Anomaly(BaseModel):
    id: str
    kind: str
    title: str
    severity: Severity
    columns: list[str]
    affected_rows: int
    affected_percent: float
    method: str
    explanation: str
    recommended_action: str
    evidence_kind: EvidenceKind = EvidenceKind.fact
    row_filter: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Governed data-quality rules
# --------------------------------------------------------------------------- #


RuleKind = Literal[
    "required",
    "unique",
    "accepted_values",
    "numeric_range",
    "regex_format",
    "date_range",
]


class QualityRule(BaseModel):
    """A business rule, not a statistical guess.

    A rule violation is a `fact`: the value breaks a constraint someone agreed
    to. That is what separates this engine from anomaly detection, where a
    finding is a hypothesis until a human confirms it.
    """

    id: str
    column: str
    kind: RuleKind
    enabled: bool = True
    severity: Severity = Severity.medium
    description: str = ""
    #: `accepted_values`
    allowed: list[str] = Field(default_factory=list)
    #: `numeric_range`
    min_value: float | None = None
    max_value: float | None = None
    inclusive: bool = True
    #: `regex_format`
    pattern: str | None = None
    #: `date_range`
    earliest: str | None = None
    latest: str | None = None
    allow_future: bool = True
    #: `required` / any rule: how much failure is tolerated before it is a breach
    max_fail_percent: float = 0.0
    origin: Literal["suggested", "user"] = "user"
    remediation: str = ""


class QualityRuleSet(BaseModel):
    dataset_id: str
    rules: list[QualityRule] = Field(default_factory=list)
    updated_at: datetime | None = None
    schema_fingerprint: str = ""
    dropped_on_restore: list[str] = Field(default_factory=list)


class RuleResult(BaseModel):
    rule_id: str
    column: str
    kind: RuleKind
    label: str
    expression: str
    severity: Severity
    passed: bool
    evaluated_rows: int
    passed_rows: int
    failed_rows: int
    failed_percent: float
    skipped_reason: str | None = None
    sample_values: list[str] = Field(default_factory=list)
    remediation: str = ""
    evidence_kind: EvidenceKind = EvidenceKind.fact


class QualityReport(BaseModel):
    dataset_id: str
    evaluated_at: datetime
    analyzed_rows: int
    sampled: bool
    rules_evaluated: int
    rules_passed: int
    rules_failed: int
    rules_skipped: int
    results: list[RuleResult] = Field(default_factory=list)
    #: Statistical suspicions are reported next to, and clearly apart from,
    #: confirmed rule breaches.
    suspicions: list[str] = Field(default_factory=list)
    note: str = (
        "Rule results are confirmed breaches of a stated constraint. "
        "Anomaly findings elsewhere in the app remain statistical suspicions."
    )


class PreviewRequest(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    sort_by: str | None = None
    sort_desc: bool = False
    filter_column: str | None = None
    filter_op: FilterOp | None = None
    filter_value: Any = None


class DatasetPreview(BaseModel):
    dataset_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    offset: int
    limit: int
    returned_rows: int
    total_rows: int
    filtered_rows: int
    sort_by: str | None = None
    sort_desc: bool = False
    masked_columns: list[str] = Field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------- #
# Relationships
# --------------------------------------------------------------------------- #


class RelationshipResult(BaseModel):
    column_x: str
    column_y: str
    pair_kind: str
    method: str
    statistic: float | None = None
    secondary_method: str | None = None
    secondary_statistic: float | None = None
    p_value: float | None = None
    effect_size_label: str | None = None
    sample_size: int
    missing_dropped: int
    reliable: bool = True
    warnings: list[str] = Field(default_factory=list)
    interpretation: str = ""


class RelationshipMatrix(BaseModel):
    columns: list[str]
    labels: list[str]
    values: list[list[float | None]]
    methods: list[list[str | None]]
    pairs: list[RelationshipResult]
    suppressed_pairs: int = 0
    note: str = "Association strength is scaled 0-1 for categorical methods and -1..1 for correlations."


class SegmentBreakdown(BaseModel):
    segment_column: str
    segments: list[str]
    statistics: list[float | None]
    sample_sizes: list[int]
    simpsons_paradox_suspected: bool = False
    note: str = ""


class RelationshipDetail(BaseModel):
    result: RelationshipResult
    chart: ChartSpec
    segment: SegmentBreakdown | None = None


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #


ChartType = Literal[
    "kpi_card",
    "bar",
    "column",
    "line",
    "multi_line",
    "area",
    "pie",
    "donut",
    "histogram",
    "table",
    "stacked_bar",
    "stacked_bar_100",
    "scatter",
    "bubble",
    "box",
    "heatmap",
    "pareto",
    "map",
    "pivot_table",
    "waterfall",
    "funnel",
    "cohort_heatmap",
    "small_multiples",
    "decomposition_tree",
    "cluster_scatter",
    "forecast_line",
    "driver_bar",
    "anomaly_timeline",
    "scorecard",
    "sankey",
]

ComplexityTier = Literal["easy", "medium", "complex", "very_complex"]


class ChartEncoding(BaseModel):
    x: str | None = None
    y: str | None = None
    series: str | None = None
    size: str | None = None
    color: str | None = None
    facet: str | None = None
    value: str | None = None


class ChartSpec(BaseModel):
    chart_type: ChartType
    title: str
    encoding: ChartEncoding
    aggregation: Aggregation = Aggregation.sum
    filters: list[QueryFilter] = Field(default_factory=list)
    sort: Literal["asc", "desc", "natural"] = "natural"
    limit: int = 50
    drill_path: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ChartOption(BaseModel):
    chart_type: ChartType
    tier: ComplexityTier
    compatible: bool
    rank: int | None = None
    rationale: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    spec: ChartSpec | None = None


class ChartAdviceRequest(BaseModel):
    """ENH-03: the client sends an ordered list of stable column ids plus the
    fingerprint it believes it is asking about, so a late response can be
    matched to the selection that produced it."""

    columns: list[str] = Field(min_length=1, max_length=8)
    question: str | None = None
    use_llm: bool = True
    selection_fingerprint: str | None = None


class ChartAdviceResponse(BaseModel):
    selected_columns: list[str]
    selection_summary: str
    options: list[ChartOption]
    rejected: list[ChartOption] = Field(default_factory=list)
    llm_used: bool = False
    llm_note: str | None = None
    #: Echoed identity. The UI discards any response whose fingerprint or
    #: dataset id does not match the current selection.
    dataset_id: str = ""
    selection_fingerprint: str = ""
    request_id: str = ""
    dropped_columns: list[str] = Field(default_factory=list)
    truncated_selection: bool = False


class ChartData(BaseModel):
    spec: ChartSpec
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False
    evidence_kind: EvidenceKind = EvidenceKind.fact


# --------------------------------------------------------------------------- #
# Governed query plan
# --------------------------------------------------------------------------- #


FilterOp = Literal["eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"]


class QueryFilter(BaseModel):
    column: str
    op: FilterOp
    value: Any = None


class MetricSpec(BaseModel):
    column: str
    aggregation: Aggregation
    label: str | None = None

    @field_validator("aggregation")
    @classmethod
    def _no_none_agg(cls, v: Aggregation) -> Aggregation:
        if v == Aggregation.none:
            raise ValueError("a metric needs a real aggregation")
        return v


class AnalysisPlan(BaseModel):
    """Allow-listed plan. No free-form SQL or Python is ever accepted."""

    intent: str
    metrics: list[MetricSpec] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_dimension: str | None = None
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    filters: list[QueryFilter] = Field(default_factory=list)
    sort_by: str | None = None
    sort_desc: bool = True
    limit: int = 100
    assumptions: list[str] = Field(default_factory=list)
    clarification_needed: str | None = None


class PlanResult(BaseModel):
    plan: AnalysisPlan
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    executed_ms: int
    sql_like: str


# --------------------------------------------------------------------------- #
# LLM structured outputs
# --------------------------------------------------------------------------- #


class BusinessInterpretation(BaseModel):
    dataset_summary: str
    likely_grain: str
    likely_subject_area: str
    key_measures: list[str] = Field(default_factory=list)
    key_dimensions: list[str] = Field(default_factory=list)
    data_quality_call_outs: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    evidence_kind: EvidenceKind = EvidenceKind.suggestion


class ReportRecommendation(BaseModel):
    title: str
    business_question: str
    audience: str
    decision_supported: str
    required_columns: list[str]
    metrics: list[MetricSpec] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    drill_path: list[str] = Field(default_factory=list)
    chart_type: ChartType
    tier: ComplexityTier
    why_this_representation: str
    cautions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    assumptions: list[str] = Field(default_factory=list)
    evidence_kind: EvidenceKind = EvidenceKind.suggestion
    repairs: list[str] = Field(default_factory=list)


class RecommendationSet(BaseModel):
    dataset_id: str
    interpretation: BusinessInterpretation
    recommendations: list[ReportRecommendation]
    by_tier: dict[str, list[int]] = Field(default_factory=dict)
    provider: str
    model: str
    prompt_version: str
    profile_version: str
    generated_at: datetime
    rejected_count: int = 0
    rejection_notes: list[str] = Field(default_factory=list)


class ExecutiveAnswer(BaseModel):
    headline: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    evidence_kind: EvidenceKind = EvidenceKind.inference


QuickAskKind = Literal[
    "kpi_overview",
    "count_by_dimension",
    "measure_by_dimension",
    "trend",
    "top_n",
    "compare_measures",
    "distribution",
    "drivers",
]


class QuickAskIntent(BaseModel):
    """Structured intent. The label text is never parsed to decide behaviour."""

    kind: QuickAskKind
    metrics: list[MetricSpec] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_dimension: str | None = None
    time_grain: str | None = None
    target_column: str | None = None
    limit: int = 25


class QuickAsk(BaseModel):
    id: str
    label: str
    question: str
    category: Literal["kpi", "comparison", "trend", "ranking", "distribution", "relationship"]
    intent: QuickAskIntent
    required_columns: list[str] = Field(default_factory=list)


class QuickAskList(BaseModel):
    dataset_id: str
    schema_fingerprint: str
    items: list[QuickAsk] = Field(default_factory=list)
    note: str = ""


class QuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    columns: list[str] = Field(default_factory=list)
    use_llm: bool = True
    #: ENH-04: when present, the server uses the stored structured intent for
    #: this suggestion instead of interpreting the question text.
    quick_ask_id: str | None = None
    #: Opaque token echoed back so the client can drop a superseded answer.
    client_request_id: str | None = None


class QuestionResponse(BaseModel):
    question: str
    plan: AnalysisPlan
    result: PlanResult | None = None
    chart: ChartSpec | None = None
    answer: ExecutiveAnswer
    llm_used: bool
    provider: str
    prompt_version: str
    dataset_id: str = ""
    quick_ask_id: str | None = None
    client_request_id: str | None = None
    #: 'answered' | 'clarification_required' | 'empty_result'
    state: Literal["answered", "clarification_required", "empty_result"] = "answered"


# --------------------------------------------------------------------------- #
# Storyboard & sessions
# --------------------------------------------------------------------------- #


class StoryboardItem(BaseModel):
    id: str
    title: str
    description: str = ""
    kind: Literal["chart", "insight", "kpi", "table"] = "chart"
    spec: ChartSpec | None = None
    text: str | None = None
    evidence_kind: EvidenceKind = EvidenceKind.suggestion
    order: int = 0


class Storyboard(BaseModel):
    dataset_id: str
    title: str = "Executive dashboard blueprint"
    description: str = ""
    global_filters: list[QueryFilter] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    items: list[StoryboardItem] = Field(default_factory=list)
    updated_at: datetime | None = None
    completeness: dict[str, bool] = Field(default_factory=dict)


class ChartAdvisorState(BaseModel):
    """Durable Chart Advisor state, keyed by dataset. Transient in-flight
    request state is deliberately not stored here."""

    selected_column_ids: list[str] = Field(default_factory=list, max_length=8)
    active_chart_type: str | None = None
    question: str = ""
    show_rejected: bool = True
    scroll_y: float = 0.0


class AskState(BaseModel):
    last_question: str = ""
    last_quick_ask_id: str | None = None


class WorkspaceState(BaseModel):
    dataset_id: str
    version: int = 1
    chart_advisor: ChartAdvisorState = Field(default_factory=ChartAdvisorState)
    ask: AskState = Field(default_factory=AskState)
    recommendations_tier: str = "easy"
    profile_role_filter: str = "all"
    #: Hash of the current column names and roles. State that references
    #: columns which no longer exist is dropped rather than restored.
    schema_fingerprint: str = ""
    updated_at: datetime | None = None
    dropped_on_restore: list[str] = Field(default_factory=list)


class DatasetStatus(BaseModel):
    dataset_id: str
    state: Literal["pending", "profiling", "ready", "failed"]
    progress: float = 0.0
    message: str = ""
    error: str | None = None


class ColumnOverride(BaseModel):
    name: str
    analytical_role: AnalyticalRole | None = None
    default_aggregation: Aggregation | None = None
    label: str | None = None
    is_sensitive: bool | None = None


class AuditRecord(BaseModel):
    dataset_id: str
    at: datetime
    action: str
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    profile_version: str | None = None
    sampled: bool = False
    detail: str = ""


class ErrorResponse(BaseModel):
    error: str
    detail: str
    request_id: str


RelationshipDetail.model_rebuild()
ChartSpec.model_rebuild()
