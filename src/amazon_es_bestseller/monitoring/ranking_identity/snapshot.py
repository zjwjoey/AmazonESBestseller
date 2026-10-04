"""Append-only identity snapshot artifacts."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import IDENTITY_PARSER_VERSION, IDENTITY_SCHEMA_VERSION


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _snapshot_id(now: datetime | None = None) -> str:
    return "identity_snapshot_%sZ" % (now or _now()).strftime("%Y%m%dT%H%M%S%f")


def write_identity_snapshot(
    result: dict[str, Any], output_root: str | Path, *, snapshot_id: str | None = None,
    source_marketplace: str = "amazon.es", source_locale: str = "es_ES",
    created_at: datetime | str | None = None, evidence_dir: str | Path | None = None,
) -> dict[str, Any]:
    created = created_at or _now()
    if isinstance(created, datetime):
        created_text = created.isoformat(timespec="seconds")
        day = created.strftime("%Y-%m-%d")
    else:
        created_text = str(created)
        day = str(created)[:10]
    snapshot_id = snapshot_id or _snapshot_id(created if isinstance(created, datetime) else None)
    root = Path(output_root)
    target = root / day / snapshot_id
    if target.exists():
        raise FileExistsError(f"identity snapshot 已存在，不允许覆盖: {target}")
    target.mkdir(parents=True, exist_ok=False)
    records = [{"snapshot_id": snapshot_id, **dict(row)}
               for row in result.get("records", [])]
    audit = dict(result.get("audit") or {})
    audit.update({"snapshot_id": snapshot_id, "parser_version": IDENTITY_PARSER_VERSION})
    status = str(audit.get("status") or "IDENTITY_BLOCKED")
    manifest = {
        "snapshot_id": snapshot_id,
        "created_at": created_text,
        "source_marketplace": source_marketplace,
        "source_locale": source_locale,
        "parser_version": IDENTITY_PARSER_VERSION,
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "status": status,
        "identity_ready": bool(audit.get("identity_ready")),
        "identity_complete": bool(audit.get("identity_complete")),
        "expected_count": audit.get("expected_count"),
        "expected_count_source": audit.get("expected_count_source", "UNKNOWN"),
        "evidence_files": list(result.get("evidence_files") or audit.get("evidence_files") or []),
    }
    (target / "identity.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["asin", "product_url", "product_url_raw", "product_url_source", "asin_source",
              "identity_status", "rank", "rank_raw", "page_number", "card_index",
              "source_url", "evidence_type", "evidence_file"]
    with (target / "identity.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    if evidence_dir:
        source = Path(evidence_dir)
        evidence_target = target / "evidence"
        if source.is_dir():
            shutil.copytree(source, evidence_target)
        else:
            evidence_target.mkdir()
            shutil.copy2(source, evidence_target / source.name)
    return {"path": target, "manifest": manifest, "audit": audit, "records": records}


create_identity_snapshot = write_identity_snapshot
