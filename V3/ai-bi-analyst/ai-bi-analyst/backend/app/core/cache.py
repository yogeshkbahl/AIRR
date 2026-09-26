"""ENH-06 — dataset-scoped cache package.

Every upload gets an opaque dataset id and its own directory containing the
sanitised original file plus a versioned manifest and typed artifacts:

    <workspace>/<dataset_id>/
        manifest.json                  fingerprint, versions, artifact registry
        original/<sanitised name>      the uploaded bytes, unchanged
        artifacts/<name>.json          one file per artifact, atomically written
        .lock                          advisory lock for concurrent writers

Guarantees implemented here:
  * identity is the dataset id plus a SHA-256 content fingerprint, never the
    user-supplied filename
  * writes go to a temporary file and are then renamed, so a crash mid-write
    can never leave a half-written artifact in place
  * a concurrent writer waits on a lock file rather than interleaving writes
  * before reuse, an artifact is checked against the content fingerprint, the
    cache schema version, the analysis code version, the semantic-override
    hash and the relevant configuration hash
  * a corrupt or truncated artifact is logged, discarded and rebuilt rather
    than crashing a request
  * artifact names come from an enum, so no request can steer a path
  * directories are 0o700 and files 0o600
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import settings

log = logging.getLogger("ai_bi_analyst.cache")

APP_VERSION = "1.3.0"
CACHE_SCHEMA_VERSION = "cache-v1"
#: Bump when any deterministic calculation changes, so cached artifacts written
#: by older code are rebuilt instead of trusted.
ANALYSIS_CODE_VERSION = "analysis-v1.2"

DATASET_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")
LOCK_TIMEOUT_SECONDS = 15.0


class Artifact(str, Enum):
    """The only names that can become a file path inside a workspace."""

    overview = "overview"
    columns = "columns"
    anomalies = "anomalies"
    relationships = "relationships"
    semantic_overrides = "semantic_overrides"
    workspace_state = "workspace_state"
    quick_asks = "quick_asks"
    storyboard = "storyboard"
    llm_usage = "llm_usage"
    quality_rules = "quality_rules"
    quality_results = "quality_results"


#: Artifacts that are derived from the profiling code and therefore invalid
#: once semantics change. Workspace state, storyboard and usage survive.
DERIVED_ARTIFACTS = (
    Artifact.overview,
    Artifact.columns,
    Artifact.anomalies,
    Artifact.relationships,
    Artifact.quick_asks,
    #: Rule *results* are derived. The rule definitions themselves are user
    #: configuration and are never invalidated by a semantic change.
    Artifact.quality_results,
)


class CacheError(RuntimeError):
    """Raised only for programming errors, never for a corrupt artifact."""


@dataclass
class ArtifactMeta:
    name: str
    checksum: str
    bytes: int
    created_at: str
    cache_schema_version: str = CACHE_SCHEMA_VERSION
    analysis_code_version: str = ANALYSIS_CODE_VERSION
    overrides_hash: str = ""
    config_hash: str = ""


@dataclass
class Manifest:
    dataset_id: str
    sha256: str
    size_bytes: int
    original_filename: str
    stored_filename: str
    uploaded_at: str
    parser: dict[str, Any] = field(default_factory=dict)
    profile_version: str = ""
    analysis_timestamp: str = ""
    total_rows: int = 0
    analyzed_rows: int = 0
    sampled: bool = False
    sample_method: str | None = None
    business_context: str = ""
    overrides_hash: str = ""
    config_hash: str = ""
    cache_schema_version: str = CACHE_SCHEMA_VERSION
    analysis_code_version: str = ANALYSIS_CODE_VERSION
    app_version: str = APP_VERSION
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Manifest:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def file_fingerprint(path: Path) -> tuple[str, int]:
    """SHA-256 and size, read in chunks so a large upload does not load fully."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def payload_checksum(payload: Any) -> str:
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()


def overrides_fingerprint(overrides: dict[str, dict] | None) -> str:
    """Stable hash of the semantic overrides that derived artifacts depend on."""
    if not overrides:
        return "none"
    return hashlib.sha256(_dumps(overrides).encode("utf-8")).hexdigest()[:32]


def config_fingerprint() -> str:
    """Hash of the settings that change deterministic results."""
    relevant = {
        "iqr_multiplier": settings.iqr_multiplier,
        "robust_z_threshold": settings.robust_z_threshold,
        "rare_category_threshold": settings.rare_category_threshold,
        "high_cardinality_ratio": settings.high_cardinality_ratio,
        "near_constant_threshold": settings.near_constant_threshold,
        "min_pair_sample": settings.min_pair_sample,
        "max_association_columns": settings.max_association_columns,
        "profile_row_limit": settings.profile_row_limit,
    }
    return hashlib.sha256(_dumps(relevant).encode("utf-8")).hexdigest()[:32]


def _dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)


@contextmanager
def _lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Cross-platform advisory lock: exclusive create, with a stale-lock break."""
    deadline = time.monotonic() + timeout
    handle = None
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > timeout:
                log.warning("Breaking a stale cache lock older than %.0fs: %s", age, path.parent.name)
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise CacheError("Timed out waiting for the dataset cache lock.") from None
            time.sleep(0.05)
    try:
        os.write(handle, str(os.getpid()).encode())
        yield
    finally:
        os.close(handle)
        path.unlink(missing_ok=True)


class DatasetWorkspace:
    """One uploaded file, its manifest and its cached artifacts."""

    def __init__(self, dataset_id: str) -> None:
        if not DATASET_ID_RE.match(dataset_id):
            raise CacheError("Invalid dataset id.")
        self.dataset_id = dataset_id
        self.root = (settings.workspace_dir / dataset_id).resolve()
        if settings.workspace_dir.resolve() not in self.root.parents:
            raise CacheError("Refusing to operate outside the workspace directory.")

    # ------------------------------------------------------------------ paths

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def original_dir(self) -> Path:
        return self.root / "original"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def lock_path(self) -> Path:
        return self.root / ".lock"

    def artifact_path(self, artifact: Artifact) -> Path:
        # `artifact` is an enum member, so the filename cannot be user-controlled.
        return self.artifacts_dir / f"{Artifact(artifact).value}.json"

    @property
    def exists(self) -> bool:
        return self.manifest_path.exists()

    # ------------------------------------------------------------- lifecycle

    def ensure_dirs(self) -> None:
        for directory in (self.root, self.original_dir, self.artifacts_dir):
            directory.mkdir(parents=True, exist_ok=True)
            with suppress(OSError):
                directory.chmod(0o700)

    def store_original(self, source: Path, filename: str) -> Path:
        """Move the validated upload into the workspace under a safe name."""
        self.ensure_dirs()
        target = self.original_dir / filename
        if source.resolve() != target.resolve():
            shutil.move(str(source), str(target))
        with suppress(OSError):
            target.chmod(0o600)
        return target

    def original_file(self) -> Path | None:
        manifest = self.read_manifest()
        if manifest is None:
            return None
        candidate = self.original_dir / manifest.stored_filename
        return candidate if candidate.exists() else None

    def delete(self) -> bool:
        """Remove the upload and every cache artifact together."""
        if not self.root.exists():
            return False
        shutil.rmtree(self.root, ignore_errors=True)
        log.info("cache delete dataset=%s", self.dataset_id)
        return True

    def size_on_disk(self) -> int:
        if not self.root.exists():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    # --------------------------------------------------------------- manifest

    def write_manifest(self, manifest: Manifest) -> None:
        self.ensure_dirs()
        with _lock(self.lock_path):
            self._atomic_write(self.manifest_path, manifest.to_json())

    def read_manifest(self) -> Manifest | None:
        try:
            payload = json.loads(self.manifest_path.read_text("utf-8"))
            return Manifest.from_json(payload)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("cache manifest unreadable dataset=%s error=%s", self.dataset_id, type(exc).__name__)
            return None

    def update_manifest(self, **fields: Any) -> Manifest | None:
        with _lock(self.lock_path):
            manifest = self.read_manifest()
            if manifest is None:
                return None
            for key, value in fields.items():
                if hasattr(manifest, key):
                    setattr(manifest, key, value)
            self._atomic_write(self.manifest_path, manifest.to_json())
            return manifest

    # -------------------------------------------------------------- artifacts

    def write_artifact(self, artifact: Artifact, payload: Any, *, overrides_hash: str = "") -> ArtifactMeta:
        """Atomically write an artifact and register it in the manifest."""
        self.ensure_dirs()
        artifact = Artifact(artifact)
        serialized = _dumps(payload)
        meta = ArtifactMeta(
            name=artifact.value,
            checksum=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            bytes=len(serialized.encode("utf-8")),
            created_at=now_iso(),
            overrides_hash=overrides_hash,
            config_hash=config_fingerprint(),
        )
        with _lock(self.lock_path):
            self._atomic_write(self.artifact_path(artifact), payload)
            manifest = self.read_manifest()
            if manifest is not None:
                manifest.artifacts[artifact.value] = asdict(meta)
                self._atomic_write(self.manifest_path, manifest.to_json())
        log.info("cache write dataset=%s artifact=%s bytes=%s", self.dataset_id, artifact.value, meta.bytes)
        return meta

    def read_artifact(
        self,
        artifact: Artifact,
        *,
        overrides_hash: str | None = None,
        require_versions: bool = True,
    ) -> Any | None:
        """Return a validated artifact, or None with the reason logged.

        A corrupt artifact is removed so the next request rebuilds it.
        """
        artifact = Artifact(artifact)
        path = self.artifact_path(artifact)
        manifest = self.read_manifest()
        if manifest is None:
            self._miss(artifact, "no manifest")
            return None

        meta_raw = manifest.artifacts.get(artifact.value)
        if meta_raw is None or not path.exists():
            self._miss(artifact, "not cached")
            return None

        if require_versions:
            if meta_raw.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
                return self._invalidate(artifact, "cache schema changed")
            if meta_raw.get("analysis_code_version") != ANALYSIS_CODE_VERSION:
                return self._invalidate(artifact, "analysis code changed")
            if meta_raw.get("config_hash") != config_fingerprint():
                return self._invalidate(artifact, "analysis configuration changed")
        if overrides_hash is not None and meta_raw.get("overrides_hash", "") != overrides_hash:
            return self._invalidate(artifact, "semantic overrides changed")

        try:
            raw = path.read_text("utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            return self._invalidate(artifact, f"unreadable ({type(exc).__name__})")

        if hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest() != meta_raw.get("checksum"):
            return self._invalidate(artifact, "checksum mismatch")

        log.info("cache hit dataset=%s artifact=%s", self.dataset_id, artifact.value)
        return payload

    def invalidate(self, artifacts: tuple[Artifact, ...] | list[Artifact], reason: str = "explicit") -> None:
        for artifact in artifacts:
            self._invalidate(Artifact(artifact), reason)

    def cache_report(self) -> dict[str, Any]:
        """Safe summary for diagnostics: no data values, only metadata."""
        manifest = self.read_manifest()
        if manifest is None:
            return {"dataset_id": self.dataset_id, "present": False}
        return {
            "dataset_id": self.dataset_id,
            "present": True,
            "fingerprint": manifest.sha256[:16],
            "size_bytes": manifest.size_bytes,
            "uploaded_at": manifest.uploaded_at,
            "cache_schema_version": manifest.cache_schema_version,
            "analysis_code_version": manifest.analysis_code_version,
            "app_version": manifest.app_version,
            "overrides_hash": manifest.overrides_hash,
            "artifacts": sorted(manifest.artifacts),
            "disk_bytes": self.size_on_disk(),
        }

    # ---------------------------------------------------------------- helpers

    def _atomic_write(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(_dumps(payload))
                stream.flush()
                os.fsync(stream.fileno())
            with suppress(OSError):
                tmp.chmod(0o600)
            os.replace(tmp, path)  # atomic on POSIX and Windows
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _miss(self, artifact: Artifact, reason: str) -> None:
        log.info("cache miss dataset=%s artifact=%s reason=%s", self.dataset_id, artifact.value, reason)

    def _invalidate(self, artifact: Artifact, reason: str) -> None:
        log.info("cache rebuild dataset=%s artifact=%s reason=%s", self.dataset_id, artifact.value, reason)
        self.artifact_path(artifact).unlink(missing_ok=True)
        with suppress(CacheError), _lock(self.lock_path):
            manifest = self.read_manifest()
            if manifest and artifact.value in manifest.artifacts:
                manifest.artifacts.pop(artifact.value, None)
                self._atomic_write(self.manifest_path, manifest.to_json())
        return None


def create_workspace(
    dataset_id: str,
    *,
    source: Path,
    original_filename: str,
    stored_filename: str,
    business_context: str,
    parser: dict[str, Any],
) -> tuple[DatasetWorkspace, Manifest]:
    """Move an accepted upload into its own workspace and write the manifest."""
    workspace = DatasetWorkspace(dataset_id)
    workspace.ensure_dirs()
    stored = workspace.store_original(source, stored_filename)
    sha256, size = file_fingerprint(stored)
    manifest = Manifest(
        dataset_id=dataset_id,
        sha256=sha256,
        size_bytes=size,
        original_filename=original_filename,
        stored_filename=stored_filename,
        uploaded_at=now_iso(),
        parser=parser,
        business_context=business_context,
        overrides_hash=overrides_fingerprint(None),
        config_hash=config_fingerprint(),
    )
    workspace.write_manifest(manifest)
    log.info("cache create dataset=%s fingerprint=%s size=%s", dataset_id, sha256[:16], size)
    return workspace, manifest


def list_workspaces() -> list[DatasetWorkspace]:
    settings.ensure_dirs()
    out: list[DatasetWorkspace] = []
    for child in settings.workspace_dir.iterdir():
        if child.is_dir() and DATASET_ID_RE.match(child.name):
            out.append(DatasetWorkspace(child.name))
    return out
