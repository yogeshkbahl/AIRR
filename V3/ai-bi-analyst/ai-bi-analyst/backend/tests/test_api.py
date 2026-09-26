from __future__ import annotations

import io

import pandas as pd
import pytest


@pytest.fixture()
def uploaded(client, csv_path):
    with csv_path.open("rb") as fh:
        response = client.post(
            "/api/v1/datasets",
            files={"file": ("orders.csv", fh, "text/csv")},
            data={"business_context": "Retail revenue review for regional managers"},
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_health_exposes_providers_without_secrets(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert {p["id"] for p in body["providers"]} == {"heuristic", "openai", "anthropic"}
    text = str(body).lower()
    assert "api_key" not in text and "sk-" not in text
    assert body["limits"]["max_upload_mb"] > 0


def test_upload_returns_exact_counts(uploaded):
    overview = uploaded["overview"]
    assert overview["row_count"] == 243
    assert overview["column_count"] == 13
    assert overview["quality"]["components"]
    assert overview["business_context"].startswith("Retail revenue")
    assert uploaded["status"]["state"] == "ready"


def test_unsupported_file_is_rejected_with_a_useful_error(client):
    response = client.post(
        "/api/v1/datasets",
        files={"file": ("notes.pdf", io.BytesIO(b"%PDF-1.7 fake"), "application/pdf")},
        data={"business_context": ""},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_file"
    assert "CSV" in body["detail"]
    assert body["request_id"]


def test_columns_anomalies_and_drillthrough(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    columns = client.get(f"/api/v1/datasets/{dataset_id}/columns").json()
    assert len(columns) == 13
    assert {c["analytical_role"] for c in columns} >= {"currency", "categorical_dimension", "datetime_dimension"}

    anomalies = client.get(f"/api/v1/datasets/{dataset_id}/anomalies").json()
    assert anomalies
    with_rows = next(a for a in anomalies if a["row_filter"])
    preview = client.get(f"/api/v1/datasets/{dataset_id}/anomalies/{with_rows['id']}/rows").json()
    assert "customer_email" in preview["hidden_columns"]
    assert "customer_email" not in preview["columns"]


def test_column_override_changes_the_profile(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    response = client.put(
        f"/api/v1/datasets/{dataset_id}/columns",
        json=[{"name": "customer_id", "analytical_role": "measure", "default_aggregation": "avg"}],
    )
    assert response.status_code == 200
    changed = next(c for c in response.json() if c["name"] == "customer_id")
    assert changed["analytical_role"] == "measure"
    assert changed["role_source"] in ("inferred", "user_override")
    assert changed["default_aggregation"] == "avg"


def test_relationships_matrix_and_pair(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    matrix = client.post(f"/api/v1/datasets/{dataset_id}/relationships", json={}).json()
    assert matrix["columns"]
    assert matrix["pairs"]
    methods = {p["method"] for p in matrix["pairs"]}
    assert "pearson" in methods

    detail = client.post(
        f"/api/v1/datasets/{dataset_id}/relationships/pair",
        json={"column_x": "units_sold", "column_y": "revenue_amount", "segment_by": "region"},
    ).json()
    assert detail["result"]["method"] == "pearson"
    assert detail["chart"]["chart_type"] == "scatter"

    bad = client.post(
        f"/api/v1/datasets/{dataset_id}/relationships/pair",
        json={"column_x": "nope", "column_y": "revenue_amount"},
    )
    assert bad.status_code == 400


def test_recommendations_are_tiered_and_grounded(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.post(f"/api/v1/datasets/{dataset_id}/recommendations", json={"count": 12}).json()
    names = {c["name"] for c in client.get(f"/api/v1/datasets/{dataset_id}/columns").json()}
    assert body["provider"] == "heuristic"
    assert body["prompt_version"].startswith("report_recommendations@")
    assert set(body["by_tier"]) <= {"easy", "medium", "complex", "very_complex"}
    for rec in body["recommendations"]:
        assert set(rec["required_columns"]) <= names
        assert rec["tier"] in {"easy", "medium", "complex", "very_complex"}


def test_chart_advice_only_returns_compatible_options(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-advice",
        json={"columns": ["region", "revenue_amount"], "use_llm": False},
    ).json()
    assert all(o["compatible"] for o in body["options"])
    assert "scatter" not in {o["chart_type"] for o in body["options"]}
    assert any(o["chart_type"] == "scatter" and o["blockers"] for o in body["rejected"])

    spec = next(o["spec"] for o in body["options"] if o["chart_type"] == "bar")
    data = client.post(f"/api/v1/datasets/{dataset_id}/chart-data", json=spec).json()
    assert data["row_count"] > 0
    assert data["evidence_kind"] == "fact"


def test_chart_data_rejects_invented_columns(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-data",
        json={
            "chart_type": "bar",
            "title": "Invented",
            "encoding": {"x": "ghost_column", "y": "ghost_measure"},
            "aggregation": "sum",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_chart_spec"


def test_question_exposes_its_plan_and_uses_computed_values(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.post(
        f"/api/v1/datasets/{dataset_id}/questions",
        json={"question": "What is total revenue by region?", "columns": []},
    ).json()
    assert body["plan"]["metrics"][0]["column"] == "revenue_amount"
    assert body["plan"]["dimensions"] == ["region"]
    assert body["result"]["row_count"] > 0
    assert "GROUP BY" in body["result"]["sql_like"]
    assert body["answer"]["evidence"]
    assert body["answer"]["caveats"]


def test_storyboard_roundtrip_and_exports(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    advice = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-advice",
        json={"columns": ["region", "revenue_amount"], "use_llm": False},
    ).json()
    kpi = next(o["spec"] for o in advice["options"] if o["chart_type"] == "kpi_card")
    bar = next(o["spec"] for o in advice["options"] if o["chart_type"] == "bar")

    board = {
        "dataset_id": dataset_id,
        "title": "Regional revenue blueprint",
        "description": "Draft for the monthly review",
        "items": [
            {"id": "i1", "title": "Total revenue", "kind": "kpi", "spec": kpi, "order": 0},
            {"id": "i2", "title": "Revenue by region", "kind": "chart", "spec": bar, "order": 1},
            {
                "id": "i3",
                "title": "Duplicate rows need a decision",
                "kind": "insight",
                "text": "1.2% duplicates",
                "order": 2,
            },
        ],
    }
    saved = client.put(f"/api/v1/datasets/{dataset_id}/storyboard", json=board)
    assert saved.status_code == 200, saved.text
    assert set(saved.json()["completeness"]) >= {"detail_table", "primary_time_trend"}

    fetched = client.get(f"/api/v1/datasets/{dataset_id}/storyboard").json()
    assert len(fetched["items"]) == 3

    exported = client.get(f"/api/v1/datasets/{dataset_id}/storyboard/export.json").json()
    assert exported["format"] == "ai-bi-analyst.storyboard/v1"
    assert exported["semantic_layer"]

    html = client.get(f"/api/v1/datasets/{dataset_id}/storyboard/export.html").text
    assert "Regional revenue blueprint" in html
    assert "require" in html and "validation" in html


def test_storyboard_rejects_an_invalid_item(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    response = client.put(
        f"/api/v1/datasets/{dataset_id}/storyboard",
        json={
            "dataset_id": dataset_id,
            "items": [
                {
                    "id": "x",
                    "title": "Broken",
                    "spec": {
                        "chart_type": "scatter",
                        "title": "Broken",
                        "encoding": {"x": "region", "y": "product_category"},
                        "aggregation": "none",
                    },
                }
            ],
        },
    )
    assert response.status_code == 400


def test_audit_trail_records_provider_and_prompt(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    client.post(f"/api/v1/datasets/{dataset_id}/recommendations", json={})
    trail = client.get(f"/api/v1/datasets/{dataset_id}/audit").json()
    actions = {row["action"] for row in trail}
    assert {"profile", "recommendations"} <= actions
    assert all("api_key" not in str(row).lower() for row in trail)


def test_session_deletion_removes_the_data(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    assert client.delete(f"/api/v1/datasets/{dataset_id}").json()["deleted"] is True
    assert client.get(f"/api/v1/datasets/{dataset_id}/overview").status_code == 404
    assert client.delete(f"/api/v1/datasets/{dataset_id}").status_code == 404


def test_missing_dataset_returns_typed_error(client):
    body = client.get("/api/v1/datasets/doesnotexist/overview").json()
    assert body["error"] == "dataset_not_found"
    assert body["request_id"]


def test_validation_errors_are_typed(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    response = client.post(f"/api/v1/datasets/{dataset_id}/questions", json={"question": "a"})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_failed"


def test_excel_sheet_selection(client, tmp_path, sample_frame: pd.DataFrame):
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        sample_frame.head(60).to_excel(writer, sheet_name="Orders", index=False)
        sample_frame.head(30).to_excel(writer, sheet_name="Archive", index=False)

    with path.open("rb") as fh:
        sheets = client.post(
            "/api/v1/datasets/probe-sheets",
            files={"file": ("book.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        ).json()
    assert sheets["sheets"] == ["Orders", "Archive"]

    with path.open("rb") as fh:
        body = client.post(
            "/api/v1/datasets",
            files={"file": ("book.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"business_context": "", "sheet": "Archive"},
        ).json()
    assert body["overview"]["row_count"] == 30
    assert body["available_sheets"] == ["Orders", "Archive"]
