"""Request evidence and completion policy, independent of provider dispatch."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .evidence import EvidenceReader, digest

STATES = (
    "SOURCE_MISSING", "SOURCE_REVIEW_BLOCKED", "PRECLEAN_BLOCKED", "NOT_STARTED",
    "CLAIMED_NOT_ENTERED", "ENTERED_NOT_SENT", "SENT_PENDING_OR_UNKNOWN",
    "TEXT_AVAILABLE_UNREVIEWED", "QA_FAILED", "PARTIAL", "QA_PASS", "COMPLETE", "EVIDENCE_CONFLICT",
    "TRANSPORT_FAILED", "UNKNOWN",
)


def semantic(key: str) -> str:
    parts = str(key).split("|")
    return "|".join(parts[:6]) if len(parts) >= 6 else ""


def origin(value: dict, *, reused: bool = False, direct_proven: bool = False) -> str:
    resolution = str(value.get("resolution_source", ""))
    model = str(value.get("model", ""))
    provider = str(value.get("provider", ""))
    if "legacy" in resolution or "legacy" in model or value.get("source_kind") == "legacy_excel_reference":
        return "LEGACY_REVIEWED_REFERENCE"
    if resolution == "source_preserved" or model == "identity-v1":
        return "IDENTITY_PRESERVED"
    if "dictionary" in resolution or model == "dictionary-v1":
        return "DICTIONARY"
    if provider == "deterministic":
        return "DETERMINISTIC_RULE"
    if reused or resolution in {"cached", "translation_memory"}:
        return "TM_REUSED"
    if value.get("translation_status") == "cached" or "cache" in resolution:
        return "CACHE_REUSED"
    if provider == "qwen-mt" and direct_proven:
        return "QWEN_DIRECT"
    return "UNKNOWN"


def state(*, text: bool, qa: str = "UNKNOWN", saved_status: str = "", evidence: dict | None = None,
          source_missing: bool = False, source_blocked: bool = False, preclean_blocked: bool = False,
          excluded: bool = False, complete: bool = False, conflict: bool = False) -> str:
    if conflict:
        return "EVIDENCE_CONFLICT"
    if excluded or source_blocked:
        return "SOURCE_REVIEW_BLOCKED"
    if source_missing:
        return "SOURCE_MISSING"
    if text and qa == "qa_failed":
        return "QA_FAILED"
    if saved_status == "partial":
        return "PARTIAL"
    if text:
        if qa == "pass":
            return "COMPLETE" if complete else "QA_PASS"
        return "TEXT_AVAILABLE_UNREVIEWED"
    if qa == "qa_failed":
        return "QA_FAILED"
    if preclean_blocked:
        return "PRECLEAN_BLOCKED"
    ev = evidence or {}
    if ev.get("sends"):
        if ev.get("responses") and all(r.get("status_code") not in {200, None} for r in ev["responses"]):
            return "TRANSPORT_FAILED"
        return "SENT_PENDING_OR_UNKNOWN"
    if ev.get("entries"):
        return "ENTERED_NOT_SENT"
    if ev.get("claims"):
        return "CLAIMED_NOT_ENTERED"
    if saved_status == "pending":
        return "UNKNOWN"
    return "NOT_STARTED"


def requests(batch_id: str, events: list[dict], event_path: str, reader: EvidenceReader,
             *, eligible_live: bool) -> tuple[list[dict], dict[str, dict]]:
    """No ASIN is inferred from an HTTP count. Attempt and dispatch differ."""
    rows = []
    groups: dict[str, dict] = defaultdict(lambda: {"claims": [], "entries": [], "sends": [],
                                                  "responses": [], "qa": [], "provider_results": []})
    names = {"DURABLE_CLAIM_SAVED": "claims", "PROVIDER_ENTRY": "entries",
             "HTTP_ATTEMPT_BEFORE_POST": "sends", "HTTP_RESULT": "responses",
             "QA_RESULT_SAVED": "qa", "PROVIDER_RESULT": "provider_results"}
    if not eligible_live:
        return rows, {}
    for event in events:
        kind = names.get(event.get("event"))
        if not kind:
            continue
        key = str(event.get("canonical_dispatch_key") or "")
        stable = semantic(key)
        if not stable:
            reader.conflict("REQUEST_SEMANTIC_KEY_UNKNOWN", event_path, line=event.get("_evidence_line"))
        group = groups[key]
        group[kind].append(event)
    for key, group in groups.items():
        used = set()
        for event in group["sends"]:
            alias = event.get("alias", "UNKNOWN")
            matching = [(i, r) for i, r in enumerate(group["responses"])
                        if i not in used and r.get("alias", "UNKNOWN") == alias]
            sends_for_alias = [r for r in group["sends"] if r.get("alias", "UNKNOWN") == alias]
            # A missing response identifier must not invent an association when
            # repeated attempts overlap. Single attempt per alias is unambiguous.
            response = None
            if len(sends_for_alias) == 1 and len(matching) == 1:
                i, response = matching[0]
                used.add(i)
            elif matching:
                reader.conflict("HTTP_RESPONSE_ASSOCIATION_AMBIGUOUS", event_path, batch_id=batch_id, key=key)
            line = event.get("_evidence_line")
            receipts = reader.receipts[str(reader.resolve(event_path))]
            row = {"batch_id": batch_id, "canonical_key": key, "semantic_key": semantic(key),
                   "provider_alias": alias, "http_attempt": event.get("attempt", "UNKNOWN"),
                   "http_ordinal": event.get("total_attempts", "UNKNOWN"),
                   "sent_at": event.get("time"), "response_at": (response or {}).get("time"),
                   "http_status": (response or {}).get("status_code", "UNKNOWN"),
                   "usage": (response or {}).get("usage"),
                   "response_path": (response or {}).get("response_path", "UNKNOWN"),
                   "event_path": event_path, "send_line": line,
                   "response_line": (response or {}).get("_evidence_line"),
                   "event_file_hash": receipts.get("sha256"),
                   "claim_lines": [r.get("_evidence_line") for r in group["claims"]],
                   "entry_lines": [r.get("_evidence_line") for r in group["entries"]],
                   "duplicate_semantic_send": len(group["sends"]) > 1,
                   "send_outcome": "RESPONSE_OBSERVED" if response else "SENT_PENDING_OR_UNKNOWN",
                   "original_qa_status": "UNKNOWN", "derived_qa_status": "UNKNOWN",
                   "latest_qa_status": "UNKNOWN", "beneficiary_asins": [], "request_owner": "UNKNOWN"}
            earlier_entries = [r for r in group["entries"] if r.get("_evidence_line", 0) < (line or 0)]
            earlier_claims = [r for r in group["claims"] if r.get("_evidence_line", 0) < (line or 0)]
            if not earlier_entries or not earlier_claims:
                reader.conflict("SEND_WITHOUT_PRIOR_CLAIM_OR_ENTRY", event_path, batch_id=batch_id, key=key, line=line)
            if response and response.get("_evidence_line", 0) < (line or 0):
                reader.conflict("RESPONSE_PRECEDES_SEND", event_path, key=key)
            rows.append(row)
        for i, response in enumerate(group["responses"]):
            if i not in used:
                reader.conflict("UNMATCHED_HTTP_RESPONSE", event_path, batch_id=batch_id, key=key,
                                line=response.get("_evidence_line"))
    return rows, dict(groups)


def text_facts(envelope: dict) -> dict[str, Any]:
    text = str(envelope.get("translated_text") or "")
    candidate = str(envelope.get("candidate_text") or text)
    return {"has_text": bool(text.strip()), "text_hash": digest(text.encode("utf-8")) if text else "",
            "candidate_hash": digest(candidate.encode("utf-8")) if candidate else "",
            "has_candidate": bool(candidate.strip()), "text_characters": len(text)}
