"""ENH-01 and ENH-06 unit tests: cache isolation, invalidation, recovery, usage."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.core.cache import (
    ANALYSIS_CODE_VERSION,
    Artifact,
    CacheError,
    DatasetWorkspace,
    create_workspace,
    file_fingerprint,
    overrides_fingerprint,
)
from app.llm.usage import (
    TokenUsage,
    UsageLedger,
    build_record,
    extract_anthropic_usage,
    extract_openai_usage,
)


def make_workspace(tmp_path: Path, dataset_id: str, content: str, filename: str = "orders.csv"):
    source = tmp_path / f"{dataset_id}-{filename}"
    source.write_text(content, encoding="utf-8")
    return create_workspace(
        dataset_id,
        source=source,
        original_filename=filename,
        stored_filename=filename,
        business_context="ctx",
        parser={"format": "text", "delimiter": ","},
    )


# --------------------------------------------------------------------------- #
# ENH-06 cache package
# --------------------------------------------------------------------------- #


def test_dataset_id_is_validated_against_path_traversal():
    with pytest.raises(CacheError):
        DatasetWorkspace("../../etc")
    with pytest.raises(CacheError):
        DatasetWorkspace("not-hex-id!")


def test_artifact_path_cannot_escape_the_workspace(tmp_path):
    workspace, _ = make_workspace(tmp_path, "aaaa1111", "a,b\n1,2\n")
    path = workspace.artifact_path(Artifact.overview)
    assert path.parent == workspace.artifacts_dir
    assert path.name == "overview.json"


def test_same_filename_different_content_stays_isolated(tmp_path):
    first, manifest_a = make_workspace(tmp_path, "aaaa1111", "a,b\n1,2\n")
    second, manifest_b = make_workspace(tmp_path, "bbbb2222", "a,b\n9,9\n")

    assert manifest_a.original_filename == manifest_b.original_filename == "orders.csv"
    assert manifest_a.sha256 != manifest_b.sha256
    assert first.root != second.root

    first.write_artifact(Artifact.overview, {"row_count": 1}, overrides_hash="none")
    second.write_artifact(Artifact.overview, {"row_count": 99}, overrides_hash="none")

    assert first.read_artifact(Artifact.overview, overrides_hash="none") == {"row_count": 1}
    assert second.read_artifact(Artifact.overview, overrides_hash="none") == {"row_count": 99}


def test_fingerprint_matches_the_stored_bytes(tmp_path):
    workspace, manifest = make_workspace(tmp_path, "cccc3333", "a,b\n1,2\n")
    sha, size = file_fingerprint(workspace.original_file())
    assert (sha, size) == (manifest.sha256, manifest.size_bytes)


def test_artifact_is_reused_for_the_same_dataset_and_version(tmp_path):
    workspace, _ = make_workspace(tmp_path, "dddd4444", "a\n1\n")
    workspace.write_artifact(Artifact.columns, [{"name": "a"}], overrides_hash="none")
    assert workspace.read_artifact(Artifact.columns, overrides_hash="none") == [{"name": "a"}]
    report = workspace.cache_report()
    assert "columns" in report["artifacts"]
    assert report["analysis_code_version"] == ANALYSIS_CODE_VERSION


def test_semantic_override_invalidates_derived_artifacts(tmp_path):
    workspace, _ = make_workspace(tmp_path, "eeee5555", "a\n1\n")
    before = overrides_fingerprint(None)
    workspace.write_artifact(Artifact.overview, {"score": 90}, overrides_hash=before)

    after = overrides_fingerprint({"a": {"analytical_role": "measure"}})
    assert workspace.read_artifact(Artifact.overview, overrides_hash=after) is None
    # The stale artifact is dropped rather than left to be served later.
    assert workspace.read_artifact(Artifact.overview, overrides_hash=before) is None


def test_analysis_version_change_invalidates(tmp_path, monkeypatch):
    workspace, _ = make_workspace(tmp_path, "ffff6666", "a\n1\n")
    workspace.write_artifact(Artifact.anomalies, [], overrides_hash="none")
    manifest = json.loads(workspace.manifest_path.read_text())
    manifest["artifacts"]["anomalies"]["analysis_code_version"] = "analysis-v0.0"
    workspace.manifest_path.write_text(json.dumps(manifest))
    assert workspace.read_artifact(Artifact.anomalies, overrides_hash="none") is None


def test_corrupt_artifact_is_rebuilt_not_fatal(tmp_path):
    workspace, _ = make_workspace(tmp_path, "1111aaaa", "a\n1\n")
    workspace.write_artifact(Artifact.overview, {"score": 90}, overrides_hash="none")
    workspace.artifact_path(Artifact.overview).write_text("{ truncated", encoding="utf-8")

    assert workspace.read_artifact(Artifact.overview, overrides_hash="none") is None
    assert not workspace.artifact_path(Artifact.overview).exists()
    # Writing again recovers cleanly.
    workspace.write_artifact(Artifact.overview, {"score": 91}, overrides_hash="none")
    assert workspace.read_artifact(Artifact.overview, overrides_hash="none") == {"score": 91}


def test_tampered_artifact_fails_the_checksum(tmp_path):
    workspace, _ = make_workspace(tmp_path, "2222bbbb", "a\n1\n")
    workspace.write_artifact(Artifact.overview, {"score": 90}, overrides_hash="none")
    workspace.artifact_path(Artifact.overview).write_text('{"score": 10}', encoding="utf-8")
    assert workspace.read_artifact(Artifact.overview, overrides_hash="none") is None


def test_writes_are_atomic_and_leave_no_temporary_files(tmp_path):
    workspace, _ = make_workspace(tmp_path, "3333cccc", "a\n1\n")
    for index in range(20):
        workspace.write_artifact(Artifact.overview, {"n": index}, overrides_hash="none")
    leftovers = list(workspace.artifacts_dir.glob(".tmp-*"))
    assert leftovers == []
    assert workspace.read_artifact(Artifact.overview, overrides_hash="none") == {"n": 19}


def test_concurrent_writers_do_not_corrupt_the_manifest(tmp_path):
    workspace, _ = make_workspace(tmp_path, "4444dddd", "a\n1\n")
    errors: list[Exception] = []

    def writer(index: int) -> None:
        try:
            workspace.write_artifact(Artifact.columns, [{"n": index}], overrides_hash="none")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert workspace.read_manifest() is not None
    assert workspace.read_artifact(Artifact.columns, overrides_hash="none") is not None
    assert not workspace.lock_path.exists()


def test_delete_removes_upload_and_every_artifact(tmp_path):
    workspace, _ = make_workspace(tmp_path, "5555eeee", "a\n1\n")
    workspace.write_artifact(Artifact.overview, {"score": 1}, overrides_hash="none")
    workspace.write_artifact(Artifact.storyboard, {"items": []})
    assert workspace.original_file() is not None

    assert workspace.delete() is True
    assert not workspace.root.exists()
    assert workspace.read_manifest() is None
    assert workspace.cache_report() == {"dataset_id": "5555eeee", "present": False}


def test_cache_report_carries_no_data_values(tmp_path):
    workspace, _ = make_workspace(tmp_path, "6666ffff", "secret_column\nsensitive-value\n")
    workspace.write_artifact(Artifact.overview, {"score": 1}, overrides_hash="none")
    assert "sensitive-value" not in json.dumps(workspace.cache_report())


# --------------------------------------------------------------------------- #
# ENH-01 usage ledger
# --------------------------------------------------------------------------- #


def test_openai_usage_extraction_uses_reported_numbers_only():
    usage, model = extract_openai_usage(
        {
            "model": "gpt-4.1-mini-2025-04-14",
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "total_tokens": 1500,
                "prompt_tokens_details": {"cached_tokens": 900},
                "completion_tokens_details": {"reasoning_tokens": 64},
            },
        }
    )
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (1200, 300, 1500)
    assert usage.cached_read_tokens == 900
    assert usage.reasoning_tokens == 64
    assert model == "gpt-4.1-mini-2025-04-14"


def test_anthropic_usage_extraction_totals_and_cache_fields():
    usage, model = extract_anthropic_usage(
        {
            "model": "claude-sonnet-4-5-20250929",
            "usage": {
                "input_tokens": 800,
                "output_tokens": 210,
                "cache_read_input_tokens": 512,
                "cache_creation_input_tokens": 64,
            },
        }
    )
    assert usage.total_tokens == 1010  # derived only when the provider omits it
    assert usage.cached_read_tokens == 512
    assert usage.cache_write_tokens == 64
    assert model.startswith("claude-sonnet-4-5")


def test_repeated_request_id_is_not_double_counted():
    ledger = UsageLedger()
    record = build_record(
        action="recommendations",
        provider="openai",
        model="gpt-4.1-mini",
        prompt_version="report_recommendations@1.3",
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        request_id="req-1",
    )
    ledger.record("ds1", record)
    ledger.record("ds1", record)  # a retried frontend call replaying the same id

    summary = ledger.summary("ds1", resolved_provider="openai", resolved_model="gpt-4.1-mini")
    assert summary.totals.requests == 1
    assert summary.totals.total_tokens == 150
    assert summary.state == "ok"


def test_usage_is_scoped_per_dataset():
    ledger = UsageLedger()
    ledger.record(
        "ds1",
        build_record(
            action="question",
            provider="openai",
            model="m",
            prompt_version="p",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        ),
    )
    assert ledger.summary("ds2", resolved_provider="openai").totals.total_tokens == 0
    assert ledger.summary("ds1", resolved_provider="openai").totals.total_tokens == 15


def test_failed_request_preserves_the_confirmed_total():
    ledger = UsageLedger()
    ledger.record(
        "ds1",
        build_record(
            action="question",
            provider="anthropic",
            model="m",
            prompt_version="p",
            usage=TokenUsage(input_tokens=200, output_tokens=100),
        ),
    )
    ledger.record(
        "ds1",
        build_record(
            action="question",
            provider="anthropic",
            model="m",
            prompt_version="p",
            ok=False,
            error="Anthropic rate limited (HTTP 429).",
        ),
    )
    summary = ledger.summary("ds1", resolved_provider="anthropic", resolved_model="m")
    assert summary.state == "error"
    assert summary.totals.total_tokens == 300  # unchanged by the failure
    assert summary.last is not None and summary.last.ok is False
    assert "rate limited" in summary.note


def test_unconfigured_provider_reports_a_configuration_state():
    summary = UsageLedger().summary(
        "ds1",
        requested_provider="openai",
        resolved_provider="heuristic",
        resolved_model="rule-based-narrator",
        provider_configured=False,
    )
    assert summary.state == "not_configured"
    assert "no server-side key" in summary.note


def test_heuristic_provider_reports_no_tokens():
    summary = UsageLedger().summary(
        "ds1", requested_provider="heuristic", resolved_provider="heuristic", resolved_model="rule-based-narrator"
    )
    assert summary.state == "not_used"
    assert summary.totals.total_tokens == 0


def test_usage_records_never_carry_prompts_or_keys():
    record = build_record(
        action="recommendations",
        provider="openai",
        model="gpt-4.1-mini",
        prompt_version="report_recommendations@1.3",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )
    payload = record.model_dump_json()
    for forbidden in ("api_key", "authorization", "sk-", "system", "prompt_text"):
        assert forbidden not in payload


def test_reply_cut_off_at_the_output_limit_is_reported_plainly(monkeypatch):
    """A truncated reply is half a JSON document; say so rather than blame the JSON."""
    from app.llm import providers
    from app.llm.providers import AnthropicProvider, LLMError, OpenAIProvider

    anthropic_reply = {
        "stop_reason": "max_tokens",
        "content": [{"type": "text", "text": '{"recommendations": [{"title": "Rev'}],
        "usage": {"input_tokens": 10, "output_tokens": 8000},
    }
    monkeypatch.setattr(providers, "_post_with_retry", lambda url, **kw: anthropic_reply)
    with pytest.raises(LLMError) as caught:
        AnthropicProvider(api_key="test-key").complete_json("system", "user", max_tokens=8000)
    assert "8,000-token output limit" in str(caught.value)
    assert "JSON" not in str(caught.value)  # so the service does not re-ask at the same limit

    openai_reply = {"choices": [{"finish_reason": "length", "message": {"content": '{"a": '}}]}
    monkeypatch.setattr(providers, "_post_with_retry", lambda url, **kw: openai_reply)
    with pytest.raises(LLMError, match="output limit"):
        OpenAIProvider(api_key="test-key").complete_json("system", "user", max_tokens=100)
