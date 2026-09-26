"""Runtime configuration. All secrets come from the environment, never the browser."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# backend/.env, so a plain `uvicorn app.main:app` picks up the provider keys.
# Variables already in the environment (Docker env_file, --env-file) win.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    app_name: str = "AI BI Analyst"
    api_prefix: str = "/api/v1"

    workspace_dir: Path = field(default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", "./.workspace")).resolve())
    metadata_db: Path = field(
        default_factory=lambda: Path(os.getenv("METADATA_DB", "./.workspace/metadata.sqlite")).resolve()
    )

    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 200))
    profile_row_limit: int = field(default_factory=lambda: _int("PROFILE_ROW_LIMIT", 500_000))
    preview_row_limit: int = field(default_factory=lambda: _int("PREVIEW_ROW_LIMIT", 50))
    query_row_limit: int = field(default_factory=lambda: _int("QUERY_ROW_LIMIT", 5_000))
    query_timeout_seconds: int = field(default_factory=lambda: _int("QUERY_TIMEOUT_SECONDS", 30))
    session_retention_hours: int = field(default_factory=lambda: _int("SESSION_RETENTION_HOURS", 24))

    # Statistical thresholds (documented so every number in the UI is explainable).
    iqr_multiplier: float = 1.5
    robust_z_threshold: float = 3.5
    rare_category_threshold: float = 0.01
    high_cardinality_ratio: float = 0.5
    near_constant_threshold: float = 0.95
    min_pair_sample: int = 30
    max_association_columns: int = 40

    # LLM
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "heuristic"))
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"))
    llm_temperature: float = 0.1
    llm_timeout_seconds: int = field(default_factory=lambda: _int("LLM_TIMEOUT_SECONDS", 90))
    allow_masked_sample_to_llm: bool = field(default_factory=lambda: _bool("ALLOW_MASKED_SAMPLE_TO_LLM", False))

    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
            if o.strip()
        )
    )

    def ensure_dirs(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_db.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
