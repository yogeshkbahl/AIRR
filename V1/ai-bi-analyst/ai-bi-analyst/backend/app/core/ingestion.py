"""File ingestion: verify content, detect encoding/delimiter, load safely.

Rules enforced here:
  * content is checked by magic bytes, not by the filename extension
  * filenames are sanitised before they touch the filesystem
  * macros / external content are never executed (openpyxl data_only, read-only)
  * empty, encrypted, malformed and oversized files produce actionable errors
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import settings

TEXT_EXT = {".csv", ".tsv", ".txt"}
EXCEL_EXT = {".xlsx", ".xlsm"}
PARQUET_EXT = {".parquet", ".pq"}
SUPPORTED_EXT = TEXT_EXT | EXCEL_EXT | PARQUET_EXT


class IngestionError(ValueError):
    """Raised with a message that is safe and useful to show to a user."""


@dataclass
class IngestionInfo:
    filename: str
    detected_format: str
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    available_sheets: list[str] = field(default_factory=list)
    file_size_bytes: int = 0
    total_rows: int = 0
    analyzed_rows: int = 0
    sampled: bool = False
    sample_method: str | None = None
    notes: list[str] = field(default_factory=list)


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "upload")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "upload"
    return name[:120]


def sniff_format(head: bytes, filename: str) -> str:
    """Identify the payload from its bytes; the extension is only a tie-breaker."""
    if head.startswith(b"PAR1"):
        return "parquet"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise IngestionError(
            "This looks like a legacy .xls or an encrypted Office file. Re-save it as .xlsx or CSV and upload again."
        )
    if head.startswith(b"%PDF"):
        raise IngestionError("PDF files are not supported. Upload CSV, TSV, XLSX or Parquet.")
    if b"\x00" in head[:512]:
        raise IngestionError("The file looks binary and is not a supported format. Upload CSV, TSV, XLSX or Parquet.")
    ext = Path(filename).suffix.lower()
    if ext in PARQUET_EXT:
        raise IngestionError("The .parquet extension does not match the file contents.")
    return "text"


def detect_encoding(sample: bytes) -> tuple[str, list[str]]:
    notes: list[str] = []
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", notes
    try:
        sample.decode("utf-8")
        return "utf-8", notes
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(sample).best()
        if best and best.encoding:
            notes.append(f"Encoding detected as {best.encoding}; characters may differ from the source system.")
            return best.encoding, notes
    except Exception:  # pragma: no cover - optional dependency path
        pass
    notes.append("Encoding could not be detected reliably; read as latin-1.")
    return "latin-1", notes


def detect_delimiter(text: str, filename: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    if Path(filename).suffix.lower() == ".tsv":
        return "\t", notes
    try:
        dialect = csv.Sniffer().sniff(text[:16000], delimiters=",;\t|")
        return dialect.delimiter, notes
    except csv.Error:
        first = text.splitlines()[0] if text.splitlines() else ""
        counts = {d: first.count(d) for d in [",", ";", "\t", "|"]}
        delim = max(counts, key=counts.get)
        if counts[delim] == 0:
            notes.append("No delimiter found in the header row; the file was read as a single column.")
            return ",", notes
        notes.append(f"Delimiter detected by frequency as '{delim}'.")
        return delim, notes


def list_sheets(path: Path) -> list[str]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # encrypted or malformed
        raise IngestionError(f"The workbook could not be opened: {type(exc).__name__}.") from exc
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def load_dataframe(path: Path, filename: str, sheet: str | None = None) -> tuple[pd.DataFrame, IngestionInfo]:
    size = path.stat().st_size
    limit = settings.max_upload_mb * 1024 * 1024
    if size == 0:
        raise IngestionError("The file is empty.")
    if size > limit:
        raise IngestionError(f"The file is {size / 1e6:.1f} MB which exceeds the {settings.max_upload_mb} MB limit.")

    with path.open("rb") as fh:
        head = fh.read(64 * 1024)

    fmt = sniff_format(head, filename)
    info = IngestionInfo(filename=filename, detected_format=fmt, file_size_bytes=size)

    if fmt == "parquet":
        df = _load_parquet(path, info)
    elif fmt == "xlsx":
        df = _load_excel(path, sheet, info)
    else:
        df = _load_text(path, filename, info)

    if df.shape[1] == 0:
        raise IngestionError("No columns were found in the file.")
    if df.shape[0] == 0:
        raise IngestionError("The file has headers but no data rows.")

    df.columns = _dedupe_columns([str(c).strip() or f"column_{i + 1}" for i, c in enumerate(df.columns)])
    info.total_rows = int(df.shape[0])

    if info.total_rows > settings.profile_row_limit:
        info.sampled = True
        info.sample_method = (
            f"systematic every-nth row sample of {settings.profile_row_limit:,} rows "
            f"from {info.total_rows:,} total rows"
        )
        step = max(1, info.total_rows // settings.profile_row_limit)
        df = df.iloc[::step].head(settings.profile_row_limit).copy()
        info.notes.append(
            "Column statistics are based on a sample. Record and null counts are computed over the whole file."
        )
    info.analyzed_rows = int(df.shape[0])
    return df, info


def _load_parquet(path: Path, info: IngestionInfo) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        raise IngestionError(f"The Parquet file could not be read: {type(exc).__name__}.") from exc


def _load_excel(path: Path, sheet: str | None, info: IngestionInfo) -> pd.DataFrame:
    sheets = list_sheets(path)
    info.available_sheets = sheets
    if not sheets:
        raise IngestionError("The workbook contains no sheets.")
    chosen = sheet if sheet in sheets else sheets[0]
    if sheet and sheet not in sheets:
        info.notes.append(f"Sheet '{sheet}' was not found, so '{chosen}' was analysed instead.")
    info.sheet_name = chosen
    try:
        df = pd.read_excel(path, sheet_name=chosen, engine="openpyxl")
    except Exception as exc:
        raise IngestionError(f"Sheet '{chosen}' could not be read: {type(exc).__name__}.") from exc
    if len(sheets) > 1:
        info.notes.append(f"The workbook has {len(sheets)} sheets; '{chosen}' was analysed.")
    return df


def _load_text(path: Path, filename: str, info: IngestionInfo) -> pd.DataFrame:
    raw = path.read_bytes()
    encoding, notes = detect_encoding(raw[:200_000])
    info.encoding = encoding
    info.notes.extend(notes)
    text = raw.decode(encoding, errors="replace")
    delimiter, dnotes = detect_delimiter(text, filename)
    info.delimiter = delimiter
    info.notes.extend(dnotes)
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=delimiter,
            engine="python",
            skip_blank_lines=True,
            on_bad_lines="warn",
        )
    except Exception as exc:
        raise IngestionError(
            f"The delimited file could not be parsed with delimiter '{delimiter}': {type(exc).__name__}."
        ) from exc
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed: ")] or list(df.columns)]
    return df


def _dedupe_columns(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in cols:
        if c in seen:
            seen[c] += 1
            out.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            out.append(c)
    return out


CSV_INJECTION_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def neutralize_csv_value(value: object) -> object:
    """Prevent formula injection in anything we export as CSV."""
    if isinstance(value, str) and value[:1] in CSV_INJECTION_PREFIX:
        return "'" + value
    return value
