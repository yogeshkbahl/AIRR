from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

WORKSPACE = Path(tempfile.mkdtemp(prefix="bi-tests-"))
os.environ.setdefault("WORKSPACE_DIR", str(WORKSPACE))
os.environ.setdefault("METADATA_DB", str(WORKSPACE / "metadata.sqlite"))
os.environ.setdefault("QUERY_LOG_DIR", str(WORKSPACE / "query-logs"))
os.environ["LLM_PROVIDER"] = "heuristic"
# Empty rather than unset: config loads backend/.env without overriding, so an
# unset key would be refilled from a developer's real .env.
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""


@pytest.fixture(scope="session")
def sample_frame() -> pd.DataFrame:
    """Small frame with one deliberate defect per column family."""
    rng = np.random.default_rng(7)
    n = 240
    dates = pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 300, n), unit="D")
    region = rng.choice(["North", "South", "East", "north "], n, p=[0.4, 0.3, 0.25, 0.05])
    units = rng.integers(1, 30, n)
    price = np.round(rng.gamma(3, 5, n) + 3, 2)
    df = pd.DataFrame(
        {
            "order_id": [f"O-{1000 + i}" for i in range(n)],
            "order_date": dates,
            "customer_id": rng.integers(500, 560, n),
            "customer_email": [f"person{i}@example.com" for i in range(n)],
            "postal_code": rng.integers(10000, 99999, n),
            "region": region,
            "product_category": rng.choice(["Beverages", "Snacks", "Dairy"], n),
            "units_sold": units,
            "unit_price": price,
            "revenue_amount": np.round(units * price, 2),
            "discount_pct": np.round(rng.uniform(0, 30, n), 2),
            "currency_code": "USD",
            "review_text": ["The delivery arrived on time and the packaging was undamaged overall." for _ in range(n)],
        }
    )
    df.loc[0:4, "revenue_amount"] = np.nan
    df.loc[5:7, "revenue_amount"] = 999_999.0
    df.loc[8, "order_date"] = pd.Timestamp("2099-01-01")
    df.loc[9:11, "units_sold"] = -5
    return pd.concat([df, df.head(3)], ignore_index=True)


@pytest.fixture(scope="session")
def csv_path(sample_frame: pd.DataFrame) -> Path:
    path = WORKSPACE / "orders.csv"
    sample_frame.to_csv(path, index=False)
    return path


@pytest.fixture(scope="session")
def session_obj(sample_frame: pd.DataFrame):
    from app.core.ingestion import IngestionInfo
    from app.core.pipeline import build_session

    info = IngestionInfo(
        filename="orders.csv",
        detected_format="text",
        file_size_bytes=12345,
        total_rows=len(sample_frame),
        analyzed_rows=len(sample_frame),
    )
    return build_session("testsession", sample_frame.copy(), info, "Revenue review for the retail team")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
