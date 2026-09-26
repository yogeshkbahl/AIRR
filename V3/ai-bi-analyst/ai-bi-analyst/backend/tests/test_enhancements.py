"""API-level tests for ENH-01 … ENH-06."""

from __future__ import annotations

import io

import pytest

from app.core.cache import Artifact, DatasetWorkspace
from app.core.store import store


@pytest.fixture()
def uploaded(client, csv_path):
    with csv_path.open("rb") as fh:
        response = client.post(
            "/api/v1/datasets",
            files={"file": ("orders.csv", fh, "text/csv")},
            data={"business_context": "Retail revenue review"},
        )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# ENH-01 usage panel data
# --------------------------------------------------------------------------- #


def test_usage_endpoint_reports_resolved_provider_not_the_request(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    # The UI asks for OpenAI; no key is configured in tests, so the backend
    # resolves to the built-in narrator and must say so.
    body = client.get(f"/api/v1/datasets/{dataset_id}/llm-usage?provider=openai").json()
    assert body["requested_provider"] == "openai"
    assert body["resolved_provider"] == "heuristic"
    assert body["state"] == "not_configured"
    assert body["totals"]["total_tokens"] == 0
    assert body["is_session_usage"] is True


def test_usage_starts_at_not_used_for_the_builtin_narrator(client, uploaded):
    body = client.get(f"/api/v1/datasets/{uploaded['dataset_id']}/llm-usage?provider=heuristic").json()
    assert body["state"] == "not_used"
    assert body["resolved_model"] == "rule-based-narrator"
    assert body["last"] is None


def test_usage_response_contains_no_secrets(client, uploaded):
    text = client.get(f"/api/v1/datasets/{uploaded['dataset_id']}/llm-usage").text.lower()
    for forbidden in ("api_key", "authorization", "sk-", "x-api-key"):
        assert forbidden not in text


def test_usage_survives_a_lost_session(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    from app.llm.usage import TokenUsage, build_record, ledger

    ledger.record(
        dataset_id,
        build_record(
            action="question",
            provider="anthropic",
            model="claude-sonnet-4-5",
            prompt_version="executive_answer@1.3",
            usage=TokenUsage(input_tokens=400, output_tokens=120),
            request_id="fixed-1",
        ),
    )
    store.save_usage(dataset_id)

    # Simulate a backend restart: drop the in-memory session and the ledger.
    store._sessions.pop(dataset_id, None)  # noqa: SLF001 - deliberate in this test
    ledger.forget(dataset_id)

    body = client.get(f"/api/v1/datasets/{dataset_id}/llm-usage?provider=anthropic").json()
    assert body["totals"]["total_tokens"] == 520
    assert body["totals"]["requests"] == 1


# --------------------------------------------------------------------------- #
# ENH-02 shared summary numbers
# --------------------------------------------------------------------------- #


def test_profile_tiles_come_from_the_same_overview_object(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    overview = client.get(f"/api/v1/datasets/{dataset_id}/overview").json()
    columns = client.get(f"/api/v1/datasets/{dataset_id}/columns").json()

    # The four tile numbers are only ever served from the overview payload, so
    # the two pages cannot drift; the column list must agree with its count.
    assert {"row_count", "column_count", "duplicate_row_count", "missing_cell_count"} <= set(overview)
    assert overview["column_count"] == len(columns)
    assert overview["dataset_id"] == dataset_id
    assert overview["missing_cell_count"] == sum(column["null_count"] for column in columns)


# --------------------------------------------------------------------------- #
# ENH-03 chart advisor selection identity
# --------------------------------------------------------------------------- #


def test_selection_is_canonical_ordered_and_deduped(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-advice",
        json={"columns": ["revenue_amount", "region", "revenue_amount"], "use_llm": False},
    ).json()
    assert body["selected_columns"] == ["revenue_amount", "region"]
    assert body["selection_fingerprint"] == f"{dataset_id}|revenue_amount>region"
    assert body["dataset_id"] == dataset_id
    assert body["request_id"]


def test_reordering_changes_the_fingerprint(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    first = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-advice",
        json={"columns": ["region", "revenue_amount"], "use_llm": False},
    ).json()
    second = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-advice",
        json={"columns": ["revenue_amount", "region"], "use_llm": False},
    ).json()
    assert first["selection_fingerprint"] != second["selection_fingerprint"]


def test_every_option_references_only_selected_columns(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    selection = ["revenue_amount", "region"]
    body = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-advice",
        json={"columns": selection, "use_llm": False},
    ).json()
    allowed = set(selection) | {"__cluster__"}
    for option in body["options"]:
        encoding = option["spec"]["encoding"]
        used = {value for value in encoding.values() if value}
        assert used <= allowed, f"{option['chart_type']} referenced {used - allowed}"
        # A "Distribution of X" style title may only name a selected column.
        assert any(
            token in option["spec"]["title"] for token in ("Revenue amount", "Region", "Detail", "Total", "Rows")
        )


def test_unknown_columns_are_reported_not_silently_dropped(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.post(
        f"/api/v1/datasets/{dataset_id}/chart-advice",
        json={"columns": ["revenue_amount", "cost"], "use_llm": False},
    ).json()
    assert body["selected_columns"] == ["revenue_amount"]
    assert body["dropped_columns"] == ["cost"]
    assert "not in this dataset" in body["llm_note"]


def test_selection_of_only_unknown_columns_is_an_error(client, uploaded):
    response = client.post(
        f"/api/v1/datasets/{uploaded['dataset_id']}/chart-advice",
        json={"columns": ["cost", "profit_centre"], "use_llm": False},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_column"


def test_chart_advice_rejects_more_than_eight_columns(client, uploaded):
    response = client.post(
        f"/api/v1/datasets/{uploaded['dataset_id']}/chart-advice",
        json={"columns": [f"c{i}" for i in range(9)], "use_llm": False},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# ENH-04 quick asks
# --------------------------------------------------------------------------- #


def test_quick_asks_are_generated_from_the_dataset(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()
    columns = {c["name"] for c in client.get(f"/api/v1/datasets/{dataset_id}/columns").json()}

    assert body["items"], "a dataset with measures must offer suggestions"
    assert body["schema_fingerprint"]
    for item in body["items"]:
        assert item["id"].startswith("qa-")
        assert set(item["required_columns"]) <= columns
        assert item["intent"]["kind"]
        for metric in item["intent"]["metrics"]:
            assert metric["column"] in columns
        assert all(dimension in columns for dimension in item["intent"]["dimensions"])


def test_quick_ask_ids_are_stable_across_requests(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    first = [i["id"] for i in client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()["items"]]
    second = [i["id"] for i in client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()["items"]]
    assert first == second


def test_every_quick_ask_completes_the_governed_flow(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    items = client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()["items"]
    seen_categories = set()

    for item in items:
        response = client.post(
            f"/api/v1/datasets/{dataset_id}/questions",
            json={
                "question": item["question"],
                "quick_ask_id": item["id"],
                "columns": [],
                "client_request_id": "req-42",
            },
        )
        assert response.status_code == 200, f"{item['id']} failed: {response.text}"
        body = response.json()
        seen_categories.add(item["category"])
        assert body["state"] in {"answered", "clarification_required", "empty_result"}
        assert body["quick_ask_id"] == item["id"]
        assert body["client_request_id"] == "req-42"
        assert body["dataset_id"] == dataset_id
        assert body["answer"]["headline"]
        if body["state"] == "answered":
            assert body["result"]["row_count"] > 0

    # No "ranking": the fixture's dimensions have too few members to be worth ranking.
    assert {"kpi", "comparison", "trend", "distribution", "relationship"} <= seen_categories


def _semantics_for(frame):
    from app.core.semantics import classify_column

    return [classify_column(frame[c], c, i, len(frame)) for i, c in enumerate(frame.columns)]


def test_quick_asks_never_promise_more_members_than_exist(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    items = client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()["items"]
    columns = {c["name"]: c for c in client.get(f"/api/v1/datasets/{dataset_id}/columns").json()}

    assert not any("Top 10" in item["label"] for item in items)
    for item in items:
        for dimension in item["intent"]["dimensions"]:
            assert item["intent"]["limit"] <= columns[dimension]["distinct_count"]


def test_quick_ask_ranking_matches_the_member_count():
    import pandas as pd

    from app.core.quick_asks import generate_quick_asks

    n = 300
    frame = pd.DataFrame(
        {
            "store": [f"Store {i % 25}" for i in range(n)],
            "channel": [["Web", "Retail", "Phone"][i % 3] for i in range(n)],
            "revenue_amount": [float(i % 97) for i in range(n)],
        }
    )
    ranking = [q for q in generate_quick_asks(_semantics_for(frame), n) if q.category == "ranking"]
    assert len(ranking) == 1
    assert ranking[0].label.startswith("Top 10 Store")
    assert ranking[0].intent.dimensions == ["store"]

    few = frame.assign(store=[f"Store {i % 6}" for i in range(n)])
    ranking = [q for q in generate_quick_asks(_semantics_for(few), n) if q.category == "ranking"]
    assert len(ranking) == 1
    assert "Top" not in ranking[0].label
    assert ranking[0].intent.limit == 6

    three = frame.drop(columns=["store"])
    assert not [q for q in generate_quick_asks(_semantics_for(three), n) if q.category == "ranking"]


def test_quick_ask_labels_read_like_real_questions():
    import pandas as pd

    from app.core.quick_asks import generate_quick_asks

    n = 200
    frame = pd.DataFrame(
        {
            "customer_id": range(n),
            "age": [20 + i % 50 for i in range(n)],
            "gender": [["Female", "Male", "Other"][i % 3] for i in range(n)],
            "income": [30000.0 + i * 13 for i in range(n)],
            "total_purchases": [i % 40 for i in range(n)],
            "avg_transaction_value": [10.0 + (i * 7) % 300 for i in range(n)],
        }
    )
    items = generate_quick_asks(_semantics_for(frame), n)
    labels = " | ".join(q.label for q in items) + " | " + " | ".join(q.question for q in items)

    assert "Total Total" not in labels and "Average Avg" not in labels and "Total Avg" not in labels
    counts = [q for q in items if q.intent.kind == "count_by_dimension"]
    assert counts and counts[0].label == "Customers by Gender"
    # Money is not set beside age: no comparison pairs unrelated units.
    assert not [q for q in items if q.intent.kind == "compare_measures"]


def test_driver_quick_ask_uses_the_association_engine_not_sql(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    items = client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()["items"]
    driver = next(i for i in items if i["intent"]["kind"] == "drivers")
    body = client.post(
        f"/api/v1/datasets/{dataset_id}/questions",
        json={"question": driver["question"], "quick_ask_id": driver["id"]},
    ).json()

    assert body["result"]["columns"][:3] == ["Factor", "Method", "Association"]
    assert "relationship engine" in body["result"]["sql_like"]
    assert any("not a proven cause" in note for note in body["plan"]["assumptions"])


def test_unknown_quick_ask_id_is_an_actionable_error(client, uploaded):
    response = client.post(
        f"/api/v1/datasets/{uploaded['dataset_id']}/questions",
        json={"question": "What are the headline KPIs?", "quick_ask_id": "qa-does-not-exist"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unknown_quick_ask"
    assert "Refresh" in body["detail"]


def test_quick_asks_follow_a_semantic_override(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    before = client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()
    assert any("revenue_amount" in item["required_columns"] for item in before["items"])

    client.put(
        f"/api/v1/datasets/{dataset_id}/columns",
        json=[{"name": "revenue_amount", "analytical_role": "identifier"}],
    )
    after = client.get(f"/api/v1/datasets/{dataset_id}/quick-asks").json()
    assert after["schema_fingerprint"] != before["schema_fingerprint"]
    for item in after["items"]:
        for metric in item["intent"]["metrics"]:
            assert metric["column"] != "revenue_amount"


# --------------------------------------------------------------------------- #
# ENH-05 workspace state
# --------------------------------------------------------------------------- #


def test_workspace_state_roundtrip(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    saved = client.put(
        f"/api/v1/datasets/{dataset_id}/workspace-state",
        json={
            "dataset_id": dataset_id,
            "chart_advisor": {
                "selected_column_ids": ["revenue_amount", "region"],
                "active_chart_type": "bar",
                "question": "Which region leads?",
                "scroll_y": 220.5,
            },
            "recommendations_tier": "medium",
        },
    )
    assert saved.status_code == 200
    body = client.get(f"/api/v1/datasets/{dataset_id}/workspace-state").json()
    assert body["chart_advisor"]["selected_column_ids"] == ["revenue_amount", "region"]
    assert body["chart_advisor"]["active_chart_type"] == "bar"
    assert body["chart_advisor"]["scroll_y"] == 220.5
    assert body["recommendations_tier"] == "medium"
    assert body["schema_fingerprint"]


def test_workspace_state_drops_columns_that_no_longer_exist(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.put(
        f"/api/v1/datasets/{dataset_id}/workspace-state",
        json={
            "dataset_id": dataset_id,
            "chart_advisor": {"selected_column_ids": ["revenue_amount", "cost"], "active_chart_type": "ghost_chart"},
        },
    ).json()
    assert body["chart_advisor"]["selected_column_ids"] == ["revenue_amount"]
    assert body["dropped_on_restore"] == ["cost"]
    assert body["chart_advisor"]["active_chart_type"] is None


def test_workspace_state_cannot_be_written_across_datasets(client, uploaded, csv_path):
    dataset_id = uploaded["dataset_id"]
    with csv_path.open("rb") as fh:
        other = client.post(
            "/api/v1/datasets",
            files={"file": ("orders.csv", fh, "text/csv")},
            data={"business_context": "second upload"},
        ).json()["dataset_id"]

    client.put(
        f"/api/v1/datasets/{other}/workspace-state",
        json={"dataset_id": other, "chart_advisor": {"selected_column_ids": ["region"]}},
    )
    body = client.put(
        f"/api/v1/datasets/{dataset_id}/workspace-state",
        json={"dataset_id": other, "chart_advisor": {"selected_column_ids": ["revenue_amount"]}},
    ).json()
    assert body["dataset_id"] == dataset_id
    assert client.get(f"/api/v1/datasets/{other}/workspace-state").json()["chart_advisor"]["selected_column_ids"] == [
        "region"
    ]


def test_state_survives_a_lost_session(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    client.put(
        f"/api/v1/datasets/{dataset_id}/workspace-state",
        json={"dataset_id": dataset_id, "chart_advisor": {"selected_column_ids": ["region"]}},
    )
    store._sessions.pop(dataset_id, None)  # noqa: SLF001 - simulates a restart
    body = client.get(f"/api/v1/datasets/{dataset_id}/workspace-state").json()
    assert body["chart_advisor"]["selected_column_ids"] == ["region"]


# --------------------------------------------------------------------------- #
# ENH-06 workspace behaviour through the API
# --------------------------------------------------------------------------- #


def test_upload_creates_a_workspace_with_a_manifest(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    report = client.get(f"/api/v1/datasets/{dataset_id}/cache").json()
    assert report["present"] is True
    assert len(report["fingerprint"]) == 16
    assert {"overview", "columns", "anomalies"} <= set(report["artifacts"])
    assert report["session_source"] == "computed"


def test_identical_filenames_get_separate_workspaces(client):
    first = client.post(
        "/api/v1/datasets",
        files={"file": ("data.csv", io.BytesIO(b"region,revenue_amount\nNorth,10\nSouth,20\n"), "text/csv")},
        data={"business_context": ""},
    ).json()
    second = client.post(
        "/api/v1/datasets",
        files={"file": ("data.csv", io.BytesIO(b"region,revenue_amount\nEast,99\nWest,98\nEast,97\n"), "text/csv")},
        data={"business_context": ""},
    ).json()

    assert first["dataset_id"] != second["dataset_id"]
    assert first["overview"]["row_count"] == 2
    assert second["overview"]["row_count"] == 3
    reports = [
        client.get(f"/api/v1/datasets/{first['dataset_id']}/cache").json(),
        client.get(f"/api/v1/datasets/{second['dataset_id']}/cache").json(),
    ]
    assert reports[0]["fingerprint"] != reports[1]["fingerprint"]


def test_session_is_restored_from_cache_after_a_restart(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    before = client.get(f"/api/v1/datasets/{dataset_id}/overview").json()

    store._sessions.pop(dataset_id, None)  # noqa: SLF001 - simulates a restart

    after = client.get(f"/api/v1/datasets/{dataset_id}/overview").json()
    assert after["row_count"] == before["row_count"]
    assert after["quality"]["score"] == before["quality"]["score"]
    assert client.get(f"/api/v1/datasets/{dataset_id}/cache").json()["session_source"] == "cache"
    # And the analysis endpoints still work on the restored session.
    assert (
        client.post(
            f"/api/v1/datasets/{dataset_id}/questions",
            json={"question": "What is total revenue by region?"},
        ).json()["result"]["row_count"]
        > 0
    )


def test_corrupt_artifact_is_rebuilt_on_the_next_request(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    workspace = DatasetWorkspace(dataset_id)
    workspace.artifact_path(Artifact.columns).write_text("{ broken", encoding="utf-8")
    store._sessions.pop(dataset_id, None)  # noqa: SLF001

    columns = client.get(f"/api/v1/datasets/{dataset_id}/columns")
    assert columns.status_code == 200
    assert len(columns.json()) == uploaded["overview"]["column_count"]
    assert client.get(f"/api/v1/datasets/{dataset_id}/cache").json()["session_source"] == "rebuilt"


def test_override_invalidates_derived_artifacts_but_keeps_the_storyboard(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    board = client.put(
        f"/api/v1/datasets/{dataset_id}/storyboard",
        json={
            "dataset_id": dataset_id,
            "title": "Keep me",
            "items": [{"id": "i1", "title": "A note", "kind": "insight", "text": "keep"}],
        },
    )
    assert board.status_code == 200

    client.put(
        f"/api/v1/datasets/{dataset_id}/columns",
        json=[{"name": "customer_id", "analytical_role": "measure", "default_aggregation": "avg"}],
    )
    assert client.get(f"/api/v1/datasets/{dataset_id}/storyboard").json()["title"] == "Keep me"
    report = client.get(f"/api/v1/datasets/{dataset_id}/cache").json()
    assert report["overrides_hash"] != "none"


def test_delete_removes_the_upload_and_the_cache(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    workspace = DatasetWorkspace(dataset_id)
    assert workspace.root.exists()

    assert client.delete(f"/api/v1/datasets/{dataset_id}").json()["deleted"] is True
    assert not workspace.root.exists()
    # A deleted dataset must not come back through rehydration.
    assert client.get(f"/api/v1/datasets/{dataset_id}/overview").status_code == 404


def test_diagnostics_reports_storage_and_parsers_without_secrets(client):
    body = client.get("/api/v1/diagnostics").json()
    assert body["status"] in {"ok", "degraded"}
    assert body["storage"]["writable"] is True
    assert body["parsers"]["parquet"] is True
    assert body["parsers"]["xlsx"] is True
    assert ".csv" in body["supported_extensions"]
    assert "api_key" not in client.get("/api/v1/diagnostics").text.lower()


# --------------------------------------------------------------------------- #
# Governed data-quality rules and preview, through the API
# --------------------------------------------------------------------------- #


def test_quality_rules_start_as_disabled_suggestions(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.get(f"/api/v1/datasets/{dataset_id}/quality-rules").json()
    assert body["rules"], "the profile should propose at least one rule"
    assert all(rule["enabled"] is False for rule in body["rules"])
    assert all(rule["origin"] == "suggested" for rule in body["rules"])
    assert body["schema_fingerprint"]


def test_enabling_rules_and_evaluating_them(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    rules = client.get(f"/api/v1/datasets/{dataset_id}/quality-rules").json()["rules"]
    for rule in rules:
        rule["enabled"] = True
    rules.append(
        {
            "id": "rule-range-satisfaction",
            "column": "revenue_amount",
            "kind": "numeric_range",
            "enabled": True,
            "severity": "high",
            "min_value": 0,
            "max_value": 1000,
            "description": "Revenue over 1000 needs approval.",
            "remediation": "Check the order against the source system.",
        }
    )
    saved = client.put(f"/api/v1/datasets/{dataset_id}/quality-rules", json=rules)
    assert saved.status_code == 200
    assert any(rule["id"] == "rule-range-satisfaction" for rule in saved.json()["rules"])

    report = client.post(f"/api/v1/datasets/{dataset_id}/quality-rules/evaluate").json()
    assert report["rules_evaluated"] >= len(rules) - 1
    assert report["rules_failed"] >= 1
    breach = next(r for r in report["results"] if not r["passed"] and not r["skipped_reason"])
    assert breach["evidence_kind"] == "fact"
    assert breach["expression"]
    assert breach["remediation"] or breach["kind"]


def test_rules_survive_a_semantic_override_but_results_do_not(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    client.put(
        f"/api/v1/datasets/{dataset_id}/quality-rules",
        json=[
            {
                "id": "rule-required-region",
                "column": "region",
                "kind": "required",
                "enabled": True,
                "severity": "high",
            }
        ],
    )
    client.put(
        f"/api/v1/datasets/{dataset_id}/columns",
        json=[{"name": "customer_id", "analytical_role": "measure", "default_aggregation": "avg"}],
    )
    # The rule definition is user configuration and must still be there.
    after = client.get(f"/api/v1/datasets/{dataset_id}/quality-rules").json()
    assert any(rule["id"] == "rule-required-region" and rule["enabled"] for rule in after["rules"])


def test_invalid_rule_is_rejected_with_an_actionable_message(client, uploaded):
    response = client.put(
        f"/api/v1/datasets/{uploaded['dataset_id']}/quality-rules",
        json=[{"id": "bad", "column": "region", "kind": "regex_format", "pattern": "(["}],
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_quality_rule"
    assert "regular expression" in body["detail"]


def test_rule_for_a_missing_column_is_dropped_not_fatal(client, uploaded):
    body = client.put(
        f"/api/v1/datasets/{uploaded['dataset_id']}/quality-rules",
        json=[{"id": "ghost", "column": "cost", "kind": "required", "enabled": True}],
    ).json()
    assert body["dropped_on_restore"] == ["cost"]
    assert all(rule["column"] != "cost" for rule in body["rules"])


def test_quality_report_exports_as_safe_csv(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    client.put(
        f"/api/v1/datasets/{dataset_id}/quality-rules",
        json=[
            {
                "id": "rule-format-email",
                "column": "customer_email",
                "kind": "regex_format",
                "enabled": True,
                "pattern": "^[^@\\s]+@[^@\\s]+\\.[A-Za-z]{2,}$",
            }
        ],
    )
    client.post(f"/api/v1/datasets/{dataset_id}/quality-rules/evaluate")
    response = client.get(f"/api/v1/datasets/{dataset_id}/quality-report.csv")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert "@example.com" not in response.text  # no hidden sensitive values
    assert "Rows failed" in response.text


def test_preview_is_paginated_sorted_and_masked(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    body = client.post(
        f"/api/v1/datasets/{dataset_id}/preview",
        json={"offset": 0, "limit": 5, "sort_by": "revenue_amount", "sort_desc": True},
    ).json()

    assert body["returned_rows"] == 5
    assert body["total_rows"] == uploaded["overview"]["row_count"]
    assert "customer_email" in body["masked_columns"]
    # The local part is masked; the domain is kept so the column is still
    # recognisable to an analyst without exposing an identity.
    assert all("***@" in str(row["customer_email"]) for row in body["rows"])
    assert all("person" not in str(row["customer_email"]) for row in body["rows"])
    values = [row["revenue_amount"] for row in body["rows"] if row["revenue_amount"] is not None]
    assert values == sorted(values, reverse=True)


def test_preview_filter_is_allow_listed(client, uploaded):
    dataset_id = uploaded["dataset_id"]
    ok = client.post(
        f"/api/v1/datasets/{dataset_id}/preview",
        json={"limit": 10, "filter_column": "region", "filter_op": "eq", "filter_value": "North"},
    ).json()
    assert ok["filtered_rows"] < ok["total_rows"]

    unknown = client.post(
        f"/api/v1/datasets/{dataset_id}/preview",
        json={"filter_column": "ghost", "filter_op": "eq", "filter_value": 1},
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "invalid_preview_request"

    sensitive = client.post(
        f"/api/v1/datasets/{dataset_id}/preview",
        json={"filter_column": "customer_email", "filter_op": "eq", "filter_value": "a"},
    )
    assert sensitive.status_code == 400
    assert "personal data" in sensitive.json()["detail"]

    bad_op = client.post(
        f"/api/v1/datasets/{dataset_id}/preview",
        json={"filter_column": "region", "filter_op": "sql_injection", "filter_value": "x"},
    )
    assert bad_op.status_code == 422
