"""Minimal saved-evidence writer used by offline identity workflows."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_evidence_snapshot(
    output_dir: str | Path, *, initial_html: str | None = None,
    rendered_html: str | None = None, client_recs: Any = None,
    acp_response: Any = None, metadata: dict[str, Any] | None = None,
    acp_response_evidence: Any = None,
    acp_response_evidence_name: str = "acp_response_evidence.json",
) -> Path:
    """Persist raw evidence without parsing or replacing existing files."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "initial_html.html": initial_html,
        "rendered_html.html": rendered_html,
    }
    for name, value in files.items():
        if value is not None:
            target = root / name
            if target.exists():
                raise FileExistsError(f"evidence 已存在，不允许覆盖: {target}")
            target.write_text(str(value), encoding="utf-8")
    for name, value in (("client_recs.json", client_recs), ("acp_response.json", acp_response),
                        (acp_response_evidence_name, acp_response_evidence)):
        if value is not None:
            target = root / name
            if target.exists():
                raise FileExistsError(f"evidence 已存在，不允许覆盖: {target}")
            target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    if metadata is not None:
        target = root / "manifest.json"
        if target.exists():
            raise FileExistsError(f"evidence manifest 已存在，不允许覆盖: {target}")
        target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return root
