"""Fail-closed Chinese QA gate derived from canonical QA evidence."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from .chinese import canonical_qa_row, qa_payload_hash


def evaluate_chinese_gate(rows: Iterable[Mapping]) -> dict:
    """Recompute readiness from rows; caller-provided ready flags are ignored."""
    fields = [canonical_qa_row(row) for row in rows if isinstance(row, Mapping)]
    statuses = [str(row.get("status") or "BLOCKED").upper() for row in fields]
    passed = statuses.count("PASS")
    repair = statuses.count("REPAIR")
    manual = statuses.count("MANUAL_REVIEW")
    blocked = statuses.count("BLOCKED")
    if fields and set(statuses) <= {"PASS"}:
        status, ready = "SKU_ZH_READY", True
    elif blocked or repair:
        status, ready = "BLOCKED", False
    else:
        status, ready = "REVIEW_REQUIRED", False
    return {"check": "chinese_gate", "produced_stage": "chinese_gate", "status": status,
            "ready": ready, "qa_payload_hash": qa_payload_hash(fields), "fields_checked": len(fields),
            "passed": passed, "review_required": manual, "blocked": blocked + repair,
            "fields": fields}
