from __future__ import annotations

import json

import pandas as pd
import pytest

from app.config import settings
from app.core.query_log import logged
from app.core.query_plan import PlanError, _run_sql


@pytest.fixture()
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "query_log_dir", tmp_path)
    monkeypatch.setattr(settings, "query_log_enabled", True)
    return tmp_path


def _files(root):
    return sorted(root.rglob("*.json"))


def test_each_run_writes_its_own_timestamped_file(log_dir):
    for _ in range(3):
        with logged("question", "ds1") as log:
            log.set(question="Average income by segment")
    files = _files(log_dir)
    assert len(files) == 3
    assert len({f.name for f in files}) == 3
    # <YYYY-MM-DD>/<YYYYMMDDTHHMMSS.ffffff+HHMM>_<action>_<id>.json
    stamp, action, _ = files[0].stem.split("_")
    assert action == "question" and stamp[22] in "+-" and len(stamp) == 27
    assert files[0].parent.name == f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["status"] == "ok" and record["question"] == "Average income by segment"
    assert record["duration_ms"] >= 0


def test_a_failed_query_logs_the_engine_error_and_sql(log_dir):
    df = pd.DataFrame({"segment": ["a", "b"], "total_purchases": [1, 2]})
    sql = 'SELECT "segment", SUM("total_purchases") AS s FROM dataset GROUP BY "segment" ORDER BY "total_purchases"'
    with pytest.raises(PlanError), logged("plan", "ds1"):
        _run_sql(df, sql, [])
    record = json.loads(_files(log_dir)[0].read_text(encoding="utf-8"))
    assert record["status"] == "error"
    assert record["error"]["type"] == "PlanError"
    assert "Binder Error" in record["error"]["detail"]
    assert record["error"]["sql"] == sql


def test_logging_can_be_switched_off(log_dir, monkeypatch):
    monkeypatch.setattr(settings, "query_log_enabled", False)
    with logged("question", "ds1"):
        pass
    assert _files(log_dir) == []
