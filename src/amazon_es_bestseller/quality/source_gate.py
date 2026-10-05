"""Bound promotion decision for :mod:`quality.source_fields`.

The decision retains a canonical digest of the audit.  Consumers must validate
that digest against the audit they received; accepting a caller-supplied PASS
string alone would make the source gate meaningless.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .source_fields import BLOCKED, REVIEW_REQUIRED, SOURCE_READY


def canonical_audit_hash(audit: Mapping) -> str:
    """Hash source-audit facts, excluding self-referential/private metadata."""
    value = {key: audit.get(key) for key in ("check", "status", "summary", "issues", "field_audits", "sku_status")}
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_source_gate(audit: Mapping) -> dict:
    """Return a serialized source-gate decision based only on field facts."""
    if not isinstance(audit, Mapping) or audit.get("check") != "source_fields":
        raise ValueError("source gate requires a source_fields audit")
    statuses = {str(value) for value in (audit.get("sku_status") or {}).values()}
    if not statuses:
        status = REVIEW_REQUIRED
    elif BLOCKED in statuses:
        status = BLOCKED
    elif REVIEW_REQUIRED in statuses:
        status = REVIEW_REQUIRED
    else:
        status = SOURCE_READY
    return {
        "check": "source_gate",
        "status": status,
        "ready": status == SOURCE_READY,
        "audit_hash": canonical_audit_hash(audit),
        "evidence": {"source_check": "source_fields", "sku_status": dict(audit.get("sku_status") or {})},
    }


def verify_source_gate(audit: Mapping, gate: Mapping) -> bool:
    """Verify a decision is for this exact audit and contains no forged PASS."""
    if not isinstance(gate, Mapping) or gate.get("check") != "source_gate":
        return False
    expected = evaluate_source_gate(audit)
    return (gate.get("audit_hash") == expected["audit_hash"]
            and gate.get("status") == expected["status"]
            and bool(gate.get("ready")) == expected["ready"])


__all__ = ["canonical_audit_hash", "evaluate_source_gate", "verify_source_gate"]
