"""Versioned API. Consistent typed errors, correlation ids, no secret leakage."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ...config import settings
from ...core import export
from ...core.anomalies import affected_rows_preview
from ...core.chart_rules import build_selection, evaluate_selection, repair_chart_spec, validate_chart_spec
from ...core.ingestion import IngestionError, list_sheets, load_dataframe, sanitize_filename
from ...core.pipeline import build_session, reprofile
from ...core.query_plan import PlanError, chart_data, execute_plan, plan_from_chart
from ...core.relationships import build_matrix, pair_detail
from ...core.store import store
from ...llm.providers import available_providers
from ...llm.service import AnalystService
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
    DatasetStatus,
    PlanResult,
    QuestionRequest,
    QuestionResponse,
    RecommendationSet,
    RelationshipDetail,
    RelationshipMatrix,
    Storyboard,
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
        raise _fail(404, "dataset_not_found", "The analysis session has expired or was deleted. Upload the file again.", request)
    return session


def _service(provider: str | None) -> AnalystService:
    return AnalystService(provider_name=provider)


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "providers": available_providers(),
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "profile_row_limit": settings.profile_row_limit,
            "query_row_limit": settings.query_row_limit,
            "session_retention_hours": settings.session_retention_hours,
        },
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
    path = target_dir / filename

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
        raise _fail(500, "ingestion_failed", f"The file could not be processed ({type(exc).__name__}).", request) from exc

    session = build_session(dataset_id, df, info, business_context.strip()[:4000])
    store.put(session)
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(timezone.utc),
            action="profile",
            profile_version="profile-v1.2",
            sampled=info.sampled,
            detail=f"{info.detected_format} file, {info.total_rows} rows, {len(df.columns)} columns",
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
    service = _service(provider)
    interpretation, used = service.business_summary(
        overview=session.overview,
        columns=session.columns,
        anomalies=session.anomalies,
        business_context=session.overview.business_context,
    )
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(timezone.utc),
            action="summary",
            provider=service.provider_name,
            model=service.provider.model,
            prompt_version="dataset_summary@1.2",
            sampled=session.overview.sampled,
        )
    )
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
        o.name: {k: (v.value if hasattr(v, "value") else v) for k, v in o.model_dump(exclude_none=True).items() if k != "name"}
        for o in overrides
    }
    unknown = [name for name in patch if name not in {c.name for c in session.columns}]
    if unknown:
        raise _fail(400, "unknown_column", f"Not a column in this dataset: {', '.join(unknown)}.", request)
    rebuilt = reprofile(session, patch)
    store.put(rebuilt)
    store.save_overrides(dataset_id, patch)
    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(timezone.utc),
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
def recommendations(dataset_id: str, request: Request, body: RecommendationRequest = Body(default=RecommendationRequest())) -> RecommendationSet:
    session = _session(dataset_id, request)
    service = _service(body.provider)
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
            at=datetime.now(timezone.utc),
            action="recommendations",
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version,
            profile_version=result.profile_version,
            sampled=session.overview.sampled,
            detail=f"{len(result.recommendations)} kept, {result.rejected_count} rejected",
        )
    )
    return result


@router.post("/datasets/{dataset_id}/chart-advice", response_model=ChartAdviceResponse)
def chart_advice(
    dataset_id: str, request: Request, body: ChartAdviceRequest, provider: str | None = Query(None)
) -> ChartAdviceResponse:
    session = _session(dataset_id, request)
    known = {s.name for s in session.semantics}
    unknown = [c for c in body.columns if c not in known]
    if unknown:
        raise _fail(400, "unknown_column", f"Not a column in this dataset: {', '.join(unknown)}.", request)

    selection = build_selection(session.semantics, body.columns, session.overview.analyzed_row_count)
    compatible, rejected = evaluate_selection(selection)

    llm_used, note = False, None
    if body.use_llm and compatible:
        service = _service(provider)
        compatible, llm_used, note = service.rank_charts(
            selection=selection, options=compatible, semantics=session.semantics, question=body.question
        )
        if llm_used:
            store.audit(
                AuditRecord(
                    dataset_id=dataset_id,
                    at=datetime.now(timezone.utc),
                    action="chart_advice",
                    provider=service.provider_name,
                    model=service.provider.model,
                    prompt_version="chart_ranking@1.2",
                )
            )
    return ChartAdviceResponse(
        selected_columns=body.columns,
        selection_summary=selection.summary(),
        options=compatible,
        rejected=rejected,
        llm_used=llm_used,
        llm_note=note,
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


@router.post("/datasets/{dataset_id}/questions", response_model=QuestionResponse)
def ask_question(
    dataset_id: str, request: Request, body: QuestionRequest, provider: str | None = Query(None)
) -> QuestionResponse:
    session = _session(dataset_id, request)
    service = _service(provider)
    plan, llm_planned, plan_note = service.plan_for_question(body.question, session.semantics, body.columns)

    result: PlanResult | None = None
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
    if result is not None and result.rows:
        from ...core.query_plan import chart_for_plan

        chart = chart_for_plan(plan, session.semantics)

    store.audit(
        AuditRecord(
            dataset_id=dataset_id,
            at=datetime.now(timezone.utc),
            action="question",
            provider=service.provider_name,
            model=service.provider.model,
            prompt_version=prompt_ref,
            sampled=session.overview.sampled,
            detail=body.question[:300],
        )
    )
    return QuestionResponse(
        question=body.question,
        plan=plan,
        result=result,
        chart=chart,
        answer=answer,
        llm_used=llm_planned or llm_answered,
        provider=service.provider_name,
        prompt_version=prompt_ref,
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
        "primary_time_trend": (not has_date) or any(t in ("line", "multi_line", "area", "forecast_line") for t in types),
        "variance_or_driver": any(t in ("waterfall", "driver_bar", "decomposition_tree", "pareto") for t in types),
        "categorical_comparison": any(t in ("bar", "column", "stacked_bar", "stacked_bar_100", "heatmap") for t in types),
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
