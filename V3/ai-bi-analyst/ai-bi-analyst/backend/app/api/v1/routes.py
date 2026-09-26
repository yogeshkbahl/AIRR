"""Versioned API. Consistent typed errors, correlation ids, no secret leakage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ...config import settings
from ...core import export
from ...core.anomalies import affected_rows_preview
from ...core.cache import ANALYSIS_CODE_VERSION, APP_VERSION, CACHE_SCHEMA_VERSION, create_workspace
from ...core.chart_rules import (
    RULES_BY_TYPE,
    build_fingerprint,
    build_selection,
    canonical_selection,
    evaluate_selection,
    repair_chart_spec,
    validate_chart_spec,
)
from ...core.ingestion import (
    SUPPORTED_EXT,
    IngestionError,
    list_sheets,
    load_dataframe,
    sanitize_filename,
)
from ...core.pipeline import build_session, reprofile
from ...core.preview import build_preview
from ...core.quality_rules import (
    RuleError,
    evaluate_rules,
    merge_rules,
    report_csv,
    suggest_rules,
    validate_rules,
)
from ...core.query_plan import PlanError, chart_data, execute_plan
from ...core.quick_asks import driver_result, plan_for_intent
from ...core.relationships import build_matrix, pair_detail
from ...core.store import store
from ...llm.providers import available_providers, get_provider
from ...llm.service import AnalystService
from ...llm.usage import SessionUsage
from ...schemas import (
    AnalysisPlan,
    Anomaly,
    AuditRecord,
    ChartAdviceRequest,
    ChartAdviceResponse,
    ChartData,
    ChartSpec,
    ColumnOverride,
    ColumnProfile,
    DatasetOverview,
    DatasetPreview,
    DatasetStatus,
    PlanResult,
    PreviewRequest,
    QualityReport,
    QualityRule,
    QualityRuleSet,
    QuestionRequest,
    QuestionResponse,
    QuickAsk,
    QuickAskList,
    RecommendationSet,
    RelationshipDetail,
    RelationshipMatrix,
    Storyboard,
    WorkspaceState,
)

router = APIRouter()


def _fail(status: int, error: str, detail: str, request: Request) -> HTTPException:
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
    return HTTPException(
        status_code=status,
        detail={"error": error, "detail": detail, "request_id": request_id},
    )


def _session(dataset_id: str, request: Request):
    session = store.get(dataset_id)
    if session is None:
        raise _fail(
            404, "dataset_not_found", "The analysis session has expired or was deleted. Upload the file again.", request
        )
    return session


def _service(provider: str | None, dataset_id: str | None = None) -> AnalystService:
    return AnalystService(provider_name=provider, dataset_id=dataset_id)


def _usage_summary(dataset_id: str, requested_provider: str | None) -> SessionUsage:
    """ENH-01: resolved provider and model, as the backend actually used them."""
    requested = (requested_provider or settings.llm_provider or "heuristic").lower()
    resolved = get_provider(requested)
    configured = requested in ("", "heuristic") or resolved.name == requested
    return store.usage(
        dataset_id,
        requested_provider=requested,
        resolved_provider=resolved.name,
        resolved_model=resolved.model,
        provider_configured=configured,
    )


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_version": APP_VERSION,
        "providers": available_providers(),
        # LLM_PROVIDER as resolved: a provider without a server key falls back
        # to the built-in narrator, so the UI never starts on an unusable choice.
        "default_provider": get_provider().name,
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "profile_row_limit": settings.profile_row_limit,
            "query_row_limit": settings.query_row_limit,
            "session_retention_hours": settings.session_retention_hours,
        },
    }


@router.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    """Readiness detail for operators. Contains no secrets and no data values."""
    storage = store.storage_report()
    parsers: dict[str, bool] = {}
    for module, label in (("pyarrow", "parquet"), ("openpyxl", "xlsx"), ("duckdb", "query engine")):
        try:
            __import__(module)
            parsers[label] = True
        except ImportError:
            parsers[label] = False
    parsers["delimited text"] = True
    return {
        "status": "ok" if storage["writable"] and all(parsers.values()) else "degraded",
        "api": {"version": APP_VERSION, "api_prefix": settings.api_prefix},
        "storage": storage,
        "parsers": parsers,
        "supported_extensions": sorted(SUPPORTED_EXT),
        "cache": {
            "schema_version": CACHE_SCHEMA_VERSION,
            "analysis_code_version": ANALYSIS_CODE_VERSION,
        },
        "providers": available_providers(),
        "masked_sample_opt_in": settings.allow_masked_sample_to_llm,
    }


@router.get("/datasets")
def list_datasets() -> list[dict[str, Any]]:
    return store.list_sessions()


class SheetProbeResponse(BaseModel):
    sheets: list[str]


@router.post("/datasets/probe-sheets", response_model=SheetProbeResponse)
async def probe_sheets(request: Request, file: UploadFile = File(...)) -> SheetProbeResponse:
    """Let the user pick a sheet before committing to a full profile."""
    name = sanitize_filename(file.filename or "upload.xlsx")
    tmp_dir = store.session_dir("_probe")
    path = tmp_dir / name
    try:
        path.write_bytes(await file.read())
        if not name.lower().endswith((".xlsx", ".xlsm")):
            return SheetProbeResponse(sheets=[])
        return SheetProbeResponse(sheets=list_sheets(path))
    except IngestionError as exc:
        raise _fail(400, "unreadable_file", str(exc), request) from exc
    finally:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Upload & profile
# --------------------------------------------------------------------------- #


class UploadResponse(BaseModel):
    dataset_id: str
    overview: DatasetOverview
    status: DatasetStatus
    available_sheets: list[str] = Field(default_factory=list)


@router.post("/datasets", response_model=UploadResponse)
async def create_dataset(
    request: Request,
    file: UploadFile = File(...),
    business_context: str = Form(""),
    sheet: str | None = Form(None),
    provider: str | None = Form(None),
) -> UploadResponse:
    filename = sanitize_filename(file.filename or "upload.csv")
    dataset_id = store.new_id()
    target_dir = store.session_dir(dataset_id)
    path = target_dir / f"staged-{filename}"

    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    try:
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise IngestionError(f"The upload exceeds the {settings.max_upload_mb} MB limit.")
                out.write(chunk)
        df, info = load_dataframe(path, filename, sheet)
    except IngestionError as exc:
        store.delete(dataset_id)
        raise _fail(400, "invalid_file", str(exc), request) from exc
    except Exception as exc:
        store.delete(dataset_id)
        raise _fail(
            500, "ingestion_failed", f"The file could not be processed ({type(exc).__name__}).", request
        ) from exc

    context = business_context.strip()[:4000]
    # ENH-06: the accepted upload becomes a dataset workspace with a content
    # fingerprint and a versioned manifest before any analysis is cached.
    workspace, manifest = create_workspace(
        dataset_id,
        source=path,
        original_filename=file.filename or filename,
        stored_filename=filename,
        business_context=context,
        parser={
            "format": info.detected_format,
            "encoding": info.encoding,
            "delimiter": info.delimiter,
            "sheet": info.sheet_name,
        },
    )

    session = build_session(dataset_id, df, info, context)
    session.manifest = manifest
    store.put(session)
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(UTC),
            action="profile",
            profile_version="profile-v1.2",
            sampled=info.sampled,
            detail=(
                f"{info.detected_format} file, {info.total_rows} rows, {len(df.columns)} columns, "
                f"fingerprint {manifest.sha256[:16]}"
            ),
        )
    )
    return UploadResponse(
        dataset_id=dataset_id,
        overview=session.overview,
        status=session.status,
        available_sheets=info.available_sheets,
    )


@router.get("/datasets/{dataset_id}/status", response_model=DatasetStatus)
def dataset_status(dataset_id: str, request: Request) -> DatasetStatus:
    session = store.get(dataset_id)
    if session is None:
        return DatasetStatus(dataset_id=dataset_id, state="failed", message="Session not found", error="expired")
    return session.status or DatasetStatus(dataset_id=dataset_id, state="ready", progress=1.0)


@router.get("/datasets/{dataset_id}/overview", response_model=DatasetOverview)
def overview(dataset_id: str, request: Request) -> DatasetOverview:
    return _session(dataset_id, request).overview


class SummaryResponse(BaseModel):
    interpretation: dict
    llm_used: bool
    provider: str


@router.post("/datasets/{dataset_id}/summary", response_model=SummaryResponse)
def business_summary(dataset_id: str, request: Request, provider: str | None = Query(None)) -> SummaryResponse:
    session = _session(dataset_id, request)
    service = _service(provider, dataset_id)
    interpretation, used = service.business_summary(
        overview=session.overview,
        columns=session.columns,
        anomalies=session.anomalies,
        business_context=session.overview.business_context,
    )
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(UTC),
            action="summary",
            provider=service.provider_name,
            model=service.provider.model,
            prompt_version="dataset_summary@1.2",
            sampled=session.overview.sampled,
        )
    )
    store.save_usage(dataset_id)
    return SummaryResponse(
        interpretation=interpretation.model_dump(mode="json"), llm_used=used, provider=service.provider_name
    )


@router.get("/datasets/{dataset_id}/columns", response_model=list[ColumnProfile])
def columns(dataset_id: str, request: Request) -> list[ColumnProfile]:
    return _session(dataset_id, request).columns


@router.put("/datasets/{dataset_id}/columns", response_model=list[ColumnProfile])
def override_columns(
    dataset_id: str, request: Request, overrides: list[ColumnOverride] = Body(...)
) -> list[ColumnProfile]:
    session = _session(dataset_id, request)
    patch = {
        o.name: {
            k: (v.value if hasattr(v, "value") else v)
            for k, v in o.model_dump(exclude_none=True).items()
            if k != "name"
        }
        for o in overrides
    }
    unknown = [name for name in patch if name not in {c.name for c in session.columns}]
    if unknown:
        raise _fail(400, "unknown_column", f"Not a column in this dataset: {', '.join(unknown)}.", request)
    # ENH-06: only the artifacts derived from semantics are invalidated; the
    # storyboard, workspace state and usage ledger survive an override.
    store.invalidate_derived(dataset_id, "semantic override")
    rebuilt = reprofile(session, patch)
    rebuilt.manifest = session.manifest
    rebuilt.storyboard = session.storyboard
    rebuilt.workspace_state = session.workspace_state
    store.put(rebuilt)
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(UTC),
            action="column_override",
            detail=", ".join(patch),
        )
    )
    return rebuilt.columns


@router.get("/datasets/{dataset_id}/anomalies", response_model=list[Anomaly])
def anomalies(dataset_id: str, request: Request, severity: str | None = Query(None)) -> list[Anomaly]:
    found = _session(dataset_id, request).anomalies
    if severity:
        found = [a for a in found if a.severity.value == severity]
    return found


class PreviewResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    hidden_columns: list[str]


@router.get("/datasets/{dataset_id}/anomalies/{anomaly_id}/rows", response_model=PreviewResponse)
def anomaly_rows(dataset_id: str, anomaly_id: str, request: Request) -> PreviewResponse:
    session = _session(dataset_id, request)
    anomaly = next((a for a in session.anomalies if a.id == anomaly_id), None)
    if anomaly is None:
        raise _fail(404, "anomaly_not_found", "That finding is no longer in this session.", request)
    if not anomaly.row_filter:
        return PreviewResponse(columns=[], rows=[], row_count=0, hidden_columns=sorted(session.sensitive_columns))
    preview = affected_rows_preview(
        session.df, anomaly.row_filter, session.sensitive_columns, settings.preview_row_limit
    )
    from ...core.query_plan import _jsonable

    return PreviewResponse(
        columns=[str(c) for c in preview.columns],
        rows=_jsonable(preview),
        row_count=len(preview),
        hidden_columns=sorted(session.sensitive_columns),
    )


# --------------------------------------------------------------------------- #
# Dataset preview
# --------------------------------------------------------------------------- #


@router.post("/datasets/{dataset_id}/preview", response_model=DatasetPreview)
def preview(dataset_id: str, request: Request, body: PreviewRequest = Body(default=PreviewRequest())) -> DatasetPreview:
    """Paginated rows with sensitive columns masked before they leave the backend."""
    session = _session(dataset_id, request)
    try:
        return build_preview(session.df, session.semantics, body, dataset_id=dataset_id)
    except PlanError as exc:
        raise _fail(400, "invalid_preview_request", str(exc), request) from exc


# --------------------------------------------------------------------------- #
# Governed data-quality rules
# --------------------------------------------------------------------------- #


def _rule_set(session) -> QualityRuleSet:
    """Stored rules, or the suggested set on first use."""
    stored = store.load_quality_rules(session.id)
    if stored is not None:
        kept, dropped = validate_rules(stored.rules, session.semantics)
        return stored.model_copy(
            update={
                "rules": kept,
                "dropped_on_restore": dropped,
                "schema_fingerprint": session.schema_fingerprint,
            }
        )
    suggested = suggest_rules(session.semantics, session.columns)
    return QualityRuleSet(
        dataset_id=session.id,
        rules=suggested,
        schema_fingerprint=session.schema_fingerprint,
    )


@router.get("/datasets/{dataset_id}/quality-rules", response_model=QualityRuleSet)
def get_quality_rules(dataset_id: str, request: Request) -> QualityRuleSet:
    """Active rules, seeded with suggestions that nobody has enabled yet."""
    return _rule_set(_session(dataset_id, request))


@router.put("/datasets/{dataset_id}/quality-rules", response_model=QualityRuleSet)
def put_quality_rules(dataset_id: str, request: Request, rules: list[QualityRule] = Body(...)) -> QualityRuleSet:
    session = _session(dataset_id, request)
    try:
        incoming, dropped = validate_rules(rules, session.semantics)
    except RuleError as exc:
        raise _fail(400, "invalid_quality_rule", str(exc), request) from exc

    current = _rule_set(session)
    merged = merge_rules(current.rules, incoming)
    saved = store.save_quality_rules(
        QualityRuleSet(
            dataset_id=dataset_id,
            rules=merged,
            schema_fingerprint=session.schema_fingerprint,
            dropped_on_restore=dropped,
        )
    )
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(UTC),
            action="quality_rules",
            detail=f"{len([r for r in merged if r.enabled])} enabled of {len(merged)}",
        )
    )
    return saved


@router.post("/datasets/{dataset_id}/quality-rules/evaluate", response_model=QualityReport)
def evaluate_quality_rules(dataset_id: str, request: Request) -> QualityReport:
    """Run the enabled rules. Every failure is a confirmed breach, not a guess."""
    session = _session(dataset_id, request)
    rule_set = _rule_set(session)
    report = evaluate_rules(
        session.df,
        rule_set.rules,
        session.semantics,
        dataset_id=dataset_id,
        sampled=session.overview.sampled,
        anomalies=session.anomalies,
    )
    store.save_quality_report(dataset_id, report)
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(UTC),
            action="quality_evaluate",
            profile_version="profile-v1.2",
            sampled=session.overview.sampled,
            detail=f"{report.rules_failed} failed, {report.rules_passed} passed",
        )
    )
    return report


@router.get("/datasets/{dataset_id}/quality-report.csv")
def export_quality_report(dataset_id: str, request: Request) -> Response:
    """Concise report. Failing values are omitted, and formulas are neutralised."""
    session = _session(dataset_id, request)
    report = store.load_quality_report(dataset_id)
    if report is None:
        rule_set = _rule_set(session)
        report = evaluate_rules(
            session.df,
            rule_set.rules,
            session.semantics,
            dataset_id=dataset_id,
            sampled=session.overview.sampled,
            anomalies=session.anomalies,
        )
    body = report_csv(report, session.filename)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="quality-report-{dataset_id}.csv"'},
    )


# --------------------------------------------------------------------------- #
# Relationships
# --------------------------------------------------------------------------- #


class RelationshipRequest(BaseModel):
    columns: list[str] | None = None


@router.post("/datasets/{dataset_id}/relationships", response_model=RelationshipMatrix)
def relationships(
    dataset_id: str, request: Request, body: RelationshipRequest = Body(default=RelationshipRequest())
) -> RelationshipMatrix:
    session = _session(dataset_id, request)
    matrix = build_matrix(session.df, session.semantics, body.columns)
    session.relationships = matrix.pairs[:40]
    return matrix


class PairRequest(BaseModel):
    column_x: str
    column_y: str
    method: str | None = None
    segment_by: str | None = None


@router.post("/datasets/{dataset_id}/relationships/pair", response_model=RelationshipDetail)
def relationship_pair(dataset_id: str, request: Request, body: PairRequest) -> RelationshipDetail:
    session = _session(dataset_id, request)
    known = {s.name for s in session.semantics}
    missing = [c for c in (body.column_x, body.column_y) if c not in known]
    if missing:
        raise _fail(400, "unknown_column", f"Not a column in this dataset: {', '.join(missing)}.", request)
    return pair_detail(session.df, session.semantics, body.column_x, body.column_y, body.method, body.segment_by)


# --------------------------------------------------------------------------- #
# Recommendations & chart advice
# --------------------------------------------------------------------------- #


class RecommendationRequest(BaseModel):
    provider: str | None = None
    count: int = Field(default=12, ge=3, le=24)
    refresh: bool = False


@router.post("/datasets/{dataset_id}/recommendations", response_model=RecommendationSet)
def recommendations(
    dataset_id: str, request: Request, body: RecommendationRequest = Body(default=RecommendationRequest())
) -> RecommendationSet:
    session = _session(dataset_id, request)
    service = _service(body.provider, dataset_id)
    result = service.recommendations(
        overview=session.overview,
        columns=session.columns,
        semantics=session.semantics,
        anomalies=session.anomalies,
        relationships=session.relationships,
        business_context=session.overview.business_context,
        count=body.count,
    )
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(UTC),
            action="recommendations",
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version,
            profile_version=result.profile_version,
            sampled=session.overview.sampled,
            detail=f"{len(result.recommendations)} kept, {result.rejected_count} rejected",
        )
    )
    store.save_usage(dataset_id)
    return result


@router.post("/datasets/{dataset_id}/chart-advice", response_model=ChartAdviceResponse)
def chart_advice(
    dataset_id: str, request: Request, body: ChartAdviceRequest, provider: str | None = Query(None)
) -> ChartAdviceResponse:
    """ENH-03: one canonical selection drives counts, options, specs and titles.

    The requested columns are resolved to stable ids in the user's order with
    duplicates collapsed, and the response echoes the dataset id plus the
    selection fingerprint so the client can discard a superseded answer.
    """
    session = _session(dataset_id, request)
    column_ids, dropped, truncated = canonical_selection(body.columns, session.semantics)
    if not column_ids:
        raise _fail(
            400,
            "unknown_column",
            f"None of these columns exist in this dataset: {', '.join(dropped[:5])}.",
            request,
        )

    fingerprint = build_fingerprint(dataset_id, column_ids)
    if body.selection_fingerprint and body.selection_fingerprint != fingerprint:
        # Not an error: the client asked about a selection that resolves
        # differently server-side. The echoed fingerprint lets it decide.
        pass

    selection = build_selection(session.semantics, column_ids, session.overview.analyzed_row_count)
    compatible, rejected = evaluate_selection(selection)

    llm_used, note = False, None
    if body.use_llm and compatible:
        service = _service(provider, dataset_id)
        compatible, llm_used, note = service.rank_charts(
            selection=selection, options=compatible, semantics=session.semantics, question=body.question
        )
        if llm_used:
            store.audit(
                AuditRecord(
                    dataset_id=dataset_id,
                    at=datetime.now(UTC),
                    action="chart_advice",
                    provider=service.provider_name,
                    model=service.provider.model,
                    prompt_version="chart_ranking@1.2",
                    detail=f"selection {fingerprint}",
                )
            )
        store.save_usage(dataset_id)

    notes: list[str] = []
    if dropped:
        notes.append(
            f"{len(dropped)} selected column(s) are not in this dataset and were ignored: {', '.join(dropped[:3])}."
        )
    if truncated:
        notes.append("Only the first 8 selected columns were evaluated.")
    if note:
        notes.append(note)

    return ChartAdviceResponse(
        selected_columns=column_ids,
        selection_summary=selection.summary(),
        options=compatible,
        rejected=rejected,
        llm_used=llm_used,
        llm_note=" ".join(notes) or None,
        dataset_id=dataset_id,
        selection_fingerprint=fingerprint,
        request_id=getattr(request.state, "request_id", ""),
        dropped_columns=dropped,
        truncated_selection=truncated,
    )


@router.post("/datasets/{dataset_id}/chart-data", response_model=ChartData)
def chart_dataset(dataset_id: str, request: Request, spec: ChartSpec) -> ChartData:
    session = _session(dataset_id, request)
    ok, problems = validate_chart_spec(spec, session.semantics, session.overview.analyzed_row_count)
    if not ok:
        repaired, notes = repair_chart_spec(spec, session.semantics, session.overview.analyzed_row_count)
        if repaired is None:
            raise _fail(400, "invalid_chart_spec", " ".join(problems + notes), request)
        spec = repaired
    try:
        cols, rows, truncated = chart_data(session.df, spec, session.semantics)
    except PlanError as exc:
        raise _fail(400, "chart_data_failed", str(exc), request) from exc
    return ChartData(spec=spec, columns=cols, rows=rows, row_count=len(rows), truncated=truncated)


# --------------------------------------------------------------------------- #
# Ask the data
# --------------------------------------------------------------------------- #


@router.get("/datasets/{dataset_id}/quick-asks", response_model=QuickAskList)
def quick_asks(dataset_id: str, request: Request) -> QuickAskList:
    """ENH-04: suggestions generated from this dataset's own schema and roles."""
    session = _session(dataset_id, request)
    items = session.quick_asks
    return QuickAskList(
        dataset_id=dataset_id,
        schema_fingerprint=session.schema_fingerprint,
        items=items,
        note=(
            "Each suggestion names real columns from this dataset and runs a governed plan."
            if items
            else "This dataset has no measure that can be aggregated, so no suggestion is offered."
        ),
    )


@router.post("/datasets/{dataset_id}/questions", response_model=QuestionResponse)
def ask_question(
    dataset_id: str, request: Request, body: QuestionRequest, provider: str | None = Query(None)
) -> QuestionResponse:
    session = _session(dataset_id, request)
    service = _service(provider, dataset_id)

    quick_ask: QuickAsk | None = None
    if body.quick_ask_id:
        quick_ask = next((q for q in session.quick_asks if q.id == body.quick_ask_id), None)
        if quick_ask is None:
            raise _fail(
                400,
                "unknown_quick_ask",
                "That suggestion is not available for this dataset. Refresh the suggestions and try again.",
                request,
            )

    result: PlanResult | None = None
    llm_planned, plan_note = False, None

    if quick_ask is not None and quick_ask.intent.kind == "drivers":
        # Driver questions are answered by the association engine rather than by
        # an aggregate query, and the result says so explicitly.
        try:
            result = driver_result(
                session.df, session.semantics, quick_ask.intent.target_column or "", quick_ask.intent.limit
            )
            plan = result.plan
        except KeyError:
            raise _fail(
                400,
                "unknown_column",
                "The target column for that suggestion is no longer in this dataset.",
                request,
            ) from None
    else:
        if quick_ask is not None:
            plan = plan_for_intent(quick_ask.intent, quick_ask.question)
        else:
            plan, llm_planned, plan_note = service.plan_for_question(body.question, session.semantics, body.columns)
        if not plan.clarification_needed:
            try:
                result = execute_plan(session.df, plan, session.semantics)
                plan = result.plan
            except PlanError as exc:
                plan = plan.model_copy(update={"clarification_needed": str(exc)})

    answer, llm_answered, prompt_ref = service.answer(
        question=body.question, result=result, anomalies=session.anomalies
    )
    if plan_note:
        answer.caveats.append(plan_note)
    if plan.clarification_needed:
        answer.caveats.append(f"Clarification needed: {plan.clarification_needed}")

    chart = None
    if result is not None and result.rows and (quick_ask is None or quick_ask.intent.kind != "drivers"):
        from ...core.query_plan import chart_for_plan

        chart = chart_for_plan(plan, session.semantics)

    state: str = "answered"
    if plan.clarification_needed:
        state = "clarification_required"
    elif result is None or not result.rows:
        state = "empty_result"

    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(UTC),
            action="question",
            provider=service.provider_name,
            model=service.provider.model,
            prompt_version=prompt_ref,
            sampled=session.overview.sampled,
            detail=(f"quick_ask={body.quick_ask_id} " if body.quick_ask_id else "") + body.question[:250],
        )
    )
    store.save_usage(dataset_id)
    return QuestionResponse(
        question=body.question,
        plan=plan,
        result=result,
        chart=chart,
        answer=answer,
        llm_used=llm_planned or llm_answered,
        provider=service.provider_name,
        prompt_version=prompt_ref,
        dataset_id=dataset_id,
        quick_ask_id=body.quick_ask_id,
        client_request_id=body.client_request_id,
        state=state,  # type: ignore[arg-type]
    )


@router.post("/datasets/{dataset_id}/plan", response_model=PlanResult)
def run_plan(dataset_id: str, request: Request, plan: AnalysisPlan) -> PlanResult:
    session = _session(dataset_id, request)
    try:
        return execute_plan(session.df, plan, session.semantics)
    except PlanError as exc:
        raise _fail(400, "invalid_plan", str(exc), request) from exc


# --------------------------------------------------------------------------- #
# Storyboard
# --------------------------------------------------------------------------- #


def _completeness(board: Storyboard, session) -> dict[str, bool]:
    types = [i.spec.chart_type for i in board.items if i.spec]
    has_date = any(s.analytical_role.value == "datetime_dimension" for s in session.semantics)
    return {
        "three_to_six_kpis": 3 <= sum(1 for t in types if t == "kpi_card") <= 6,
        "primary_time_trend": (not has_date)
        or any(t in ("line", "multi_line", "area", "forecast_line") for t in types),
        "variance_or_driver": any(t in ("waterfall", "driver_bar", "decomposition_tree", "pareto") for t in types),
        "categorical_comparison": any(
            t in ("bar", "column", "stacked_bar", "stacked_bar_100", "heatmap") for t in types
        ),
        "anomaly_or_risk_view": any(t in ("anomaly_timeline", "box", "scorecard") for t in types)
        or any(i.kind == "insight" for i in board.items),
        "detail_table": any(t in ("table", "pivot_table") for t in types),
    }


@router.get("/datasets/{dataset_id}/storyboard", response_model=Storyboard)
def get_storyboard(dataset_id: str, request: Request) -> Storyboard:
    session = _session(dataset_id, request)
    board = session.storyboard or store.load_storyboard(dataset_id) or Storyboard(dataset_id=dataset_id)
    board.completeness = _completeness(board, session)
    return board


@router.put("/datasets/{dataset_id}/storyboard", response_model=Storyboard)
def put_storyboard(dataset_id: str, request: Request, board: Storyboard) -> Storyboard:
    session = _session(dataset_id, request)
    if board.dataset_id != dataset_id:
        board = board.model_copy(update={"dataset_id": dataset_id})
    kept = []
    for index, item in enumerate(board.items):
        if item.spec is not None:
            ok, problems = validate_chart_spec(item.spec, session.semantics, session.overview.analyzed_row_count)
            if not ok:
                repaired, _ = repair_chart_spec(item.spec, session.semantics, session.overview.analyzed_row_count)
                if repaired is None:
                    raise _fail(400, "invalid_chart_spec", f"Item '{item.title}': {problems[0]}", request)
                item = item.model_copy(update={"spec": repaired})
        kept.append(item.model_copy(update={"order": index}))
    board = board.model_copy(update={"items": kept})
    saved = store.save_storyboard(board)
    saved.completeness = _completeness(saved, session)
    return saved


@router.get("/datasets/{dataset_id}/storyboard/export.json")
def export_storyboard_json(dataset_id: str, request: Request) -> JSONResponse:
    session = _session(dataset_id, request)
    board = session.storyboard or store.load_storyboard(dataset_id) or Storyboard(dataset_id=dataset_id)
    payload = export.storyboard_definition(board, session)
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="storyboard-{dataset_id}.json"'},
    )


@router.get("/datasets/{dataset_id}/storyboard/export.html", response_class=HTMLResponse)
def export_storyboard_html(dataset_id: str, request: Request) -> HTMLResponse:
    session = _session(dataset_id, request)
    board = session.storyboard or store.load_storyboard(dataset_id) or Storyboard(dataset_id=dataset_id)
    return HTMLResponse(export.storyboard_html(board, session))


# --------------------------------------------------------------------------- #
# LLM usage, workspace state and cache diagnostics
# --------------------------------------------------------------------------- #


@router.get("/datasets/{dataset_id}/llm-usage", response_model=SessionUsage)
def llm_usage(dataset_id: str, request: Request, provider: str | None = Query(None)) -> SessionUsage:
    """ENH-01: provider-reported tokens for this dataset session.

    Contains no keys, no prompts and no data values: provider, model, counts and
    a state the panel can render.
    """
    _session(dataset_id, request)
    return _usage_summary(dataset_id, provider)


@router.get("/datasets/{dataset_id}/workspace-state", response_model=WorkspaceState)
def get_workspace_state(dataset_id: str, request: Request) -> WorkspaceState:
    """ENH-05: durable per-dataset UI state, pruned against the live schema."""
    session = _session(dataset_id, request)
    state = session.workspace_state or store.load_workspace_state(dataset_id, session)
    session.workspace_state = state
    return state


@router.put("/datasets/{dataset_id}/workspace-state", response_model=WorkspaceState)
def put_workspace_state(dataset_id: str, request: Request, state: WorkspaceState) -> WorkspaceState:
    session = _session(dataset_id, request)
    if state.dataset_id != dataset_id:
        # State can never be written across datasets, whatever the body says.
        state = state.model_copy(update={"dataset_id": dataset_id})

    known = {s.name for s in session.semantics}
    selected = [c for c in state.chart_advisor.selected_column_ids if c in known]
    dropped = [c for c in state.chart_advisor.selected_column_ids if c not in known]
    advisor = state.chart_advisor.model_copy(update={"selected_column_ids": selected})
    if advisor.active_chart_type and advisor.active_chart_type not in RULES_BY_TYPE:
        advisor = advisor.model_copy(update={"active_chart_type": None})

    cleaned = state.model_copy(
        update={
            "chart_advisor": advisor,
            "schema_fingerprint": session.schema_fingerprint,
            "dropped_on_restore": dropped,
        }
    )
    return store.save_workspace_state(cleaned)


@router.get("/datasets/{dataset_id}/cache")
def cache_report(dataset_id: str, request: Request) -> dict[str, Any]:
    """ENH-06: what is cached for this dataset, as metadata only."""
    session = _session(dataset_id, request)
    report = store.cache_report(dataset_id)
    report["session_source"] = session.cache_source
    report["schema_fingerprint"] = session.schema_fingerprint
    return report


@router.get("/datasets/{dataset_id}/audit")
def audit_trail(dataset_id: str, request: Request) -> list[dict[str, Any]]:
    _session(dataset_id, request)
    return store.audit_trail(dataset_id)


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, request: Request) -> dict[str, Any]:
    deleted = store.delete(dataset_id)
    if not deleted:
        raise _fail(404, "dataset_not_found", "Nothing to delete for that id.", request)
    return {"deleted": True, "dataset_id": dataset_id}
