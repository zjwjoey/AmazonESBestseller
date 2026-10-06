"""Small immutable-artifact helpers for phased reviewed collection.

The helpers deliberately use canonical JSON bytes.  This keeps a resume bound
to the actual reviewed plan and frozen candidate set rather than to formatting
or Windows line endings.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping


PHASES = frozenset({"all", "ranking", "detail"})
PHASE_SCHEMA_VERSION = 1


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def git_head(project_root: str | Path | None) -> str:
    """Return a locally observable HEAD, never an invented provenance value."""
    if project_root is None:
        return "UNKNOWN"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(project_root), check=True,
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    value = result.stdout.strip()
    return value if len(value) == 40 else "UNKNOWN"


def phase_error(code: str) -> ValueError:
    """Stable machine-readable phase failures without a parallel exception tree."""
    return ValueError(code)


def require_phase(value: str) -> str:
    phase = str(value or "all").strip().lower()
    if phase not in PHASES:
        raise phase_error("TASK_COLLECTION_PHASE_INVALID")
    return phase


def asins(rows: object) -> set[str]:
    if not isinstance(rows, list):
        return set()
    return {str(row.get("asin") or "").strip().upper()
            for row in rows if isinstance(row, Mapping) and str(row.get("asin") or "").strip()}


__all__ = ["PHASES", "PHASE_SCHEMA_VERSION", "asins", "canonical_json_bytes",
           "canonical_json_sha256", "git_head", "phase_error", "read_json", "require_phase"]
