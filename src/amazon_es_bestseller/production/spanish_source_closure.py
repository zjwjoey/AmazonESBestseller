"""Offline, evidence-bound Spanish source candidate construction.

This module deliberately stops short of :mod:`spanish_master` promotion.  It
creates a reviewable candidate from a frozen ranking manifest, detail evidence,
and an existing source-fields audit.  A candidate can be useful evidence while
its SourceGate remains blocked; it is never labelled ``SOURCE_READY`` here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..models import merge_ranking_and_detail, normalize_asin
from ..normalization.brand import clean_brand, normalize_brand_case
from ..pipeline import normalize_product
from ..quality.source_fields import audit_source_fields
from ..quality.source_gate import canonical_audit_hash, evaluate_source_gate
from ..translation.full_detail import render_details_es


SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION = "spanish-source-closure-v1"
OWNER_EXCLUSION_SCOPE_SCHEMA_VERSION = "owner-exclusion-scope-v1"
CURRENT_SOURCE_GATE_SCHEMA_VERSION = "current-source-gate-v1"
BUILDER_UNRESOLVED_DECISION_ARTIFACT_DOMAIN = "spanish-source-builder-unresolved-decisions-v1"
_OWNER_EXCLUSIONS = {
    "B07F6LYVT6": "owner exclusion: damaged special-function source text is unrecoverable; whole SKU must not be used",
    "B077H1MZ35": "owner exclusion: damaged special-function source text is unrecoverable; whole SKU must not be used",
}
_OWNER_OPTIONAL_ATTRIBUTE_EXCLUSIONS = {
    "B08BYLMK7C": {
        "field": "speaker_type",
        "labels": {"tipo de altavoz", "tipo de altavoces", "speaker type"},
        "owner_decision": "owner-approved-optional-speaker-type-exclusion-v1",
    },
    "B017WK9SSK": {
        "field": "manufacturer",
        "labels": {"fabricante", "manufacturer"},
        "owner_decision": "owner-approved-optional-manufacturer-exclusion-v1",
    },
    "B015YK51H2": {
        "field": "brand",
        "labels": {"marca", "brand"},
        "owner_decision": "owner-approved-optional-brand-exclusion-v1",
    },
}
OWNER_OPTIONAL_ATTRIBUTE_EXCLUSION_SCHEMA_VERSION = "owner-optional-attribute-exclusion-v1"
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_MOJIBAKE_RE = re.compile(r"\ufffd|(?:Ã.|Â.)")
_NON_BRAND_BYLINE_RE = re.compile(
    r"(?:^edici[oó]n\s+en\b|^de\b.*\b(?:autor|redactor|traductor|formato)\s*:|\bformato\s*:)", re.I
)
_RANKING_CATEGORY_KEYS = ("category_l1", "category_l2", "category_l3", "leaf_category", "browse_node_id")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    # ``iterencode`` emits exactly the same JSON chunks as ``dumps`` with the
    # same encoder options, without first allocating one large canonical string.
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def candidate_manifest_hash(manifest: Iterable[Mapping]) -> str:
    """Return the repository canonical hash for a candidate manifest."""
    return _hash(list(manifest or ()))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _label(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _attrs(detail: Mapping) -> list[dict]:
    value = detail.get("attributes") or []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _attribute_values(detail: Mapping, labels: set[str]) -> list[tuple[int, dict]]:
    return [(index, attr) for index, attr in enumerate(_attrs(detail)) if _label(attr.get("label_raw")) in labels]


def _damaged(value: Any) -> bool:
    return bool(_MOJIBAKE_RE.search(_text(value)))


def _locator(asin: str, field: str, attribute: Mapping | None = None, position: int | None = None) -> dict:
    locator = {"asin": asin, "field": field, "source": "detail_attributes"}
    if attribute is not None:
        locator.update({"label_raw": attribute.get("label_raw"), "value_raw": attribute.get("value_raw")})
    if position is not None:
        locator["position"] = position
    return locator


def _queue(queue: list[dict], *, asin: str, field: str, classification: str, reason: str,
           locator: Mapping | None = None, status: str = "REVIEW_REQUIRED") -> None:
    item = {"asin": asin, "field": field, "classification": classification, "status": status, "reason": reason}
    if locator:
        item["evidence_locator"] = dict(locator)
    queue.append(item)


def _explicit_brand(detail: Mapping, asin: str, queue: list[dict]) -> str:
    """Return only explicit Marca/Brand evidence; a generic byline is not one."""
    values = _attribute_values(detail, {"marca", "brand"})
    for position, attr in values:
        raw = _text(attr.get("value_raw"))
        if not raw:
            continue
        if _damaged(raw):
            _queue(queue, asin=asin, field="brand", classification="EVIDENCE_UNAVAILABLE",
                   reason="owner-approved-optional-exclusion: damaged explicit brand value preserved only in raw evidence",
                   locator=_locator(asin, "brand", attr, position))
            return ""
        brand = clean_brand(raw)
        return normalize_brand_case(brand) if brand else ""

    raw_brand = _text(detail.get("brand_raw"))
    if raw_brand and _NON_BRAND_BYLINE_RE.search(raw_brand):
        _queue(queue, asin=asin, field="brand", classification="EVIDENCE_UNAVAILABLE",
               reason="brand byline has no explicit Marca/Brand label; canonical brand intentionally blank",
               locator={"asin": asin, "field": "brand", "source": "brand_raw", "value_raw": detail.get("brand_raw")})
        return ""
    if raw_brand and not _damaged(raw_brand):
        # A parsed plain Amazon byline remains usable evidence.  The explicit
        # label above wins, while author/format/editorial strings are rejected
        # by the evidence rule rather than an ASIN-specific exception.
        return normalize_brand_case(clean_brand(raw_brand))
    if raw_brand:
        _queue(queue, asin=asin, field="brand", classification="EVIDENCE_UNAVAILABLE",
               reason="damaged brand byline preserved only in raw evidence",
               locator={"asin": asin, "field": "brand", "source": "brand_raw", "value_raw": detail.get("brand_raw")})
    return ""


def _optional_attribute(detail: Mapping, asin: str, field: str, labels: set[str], queue: list[dict]) -> str:
    for position, attr in _attribute_values(detail, labels):
        raw = _text(attr.get("value_raw"))
        if not raw:
            continue
        if _damaged(raw):
            _queue(queue, asin=asin, field=field, classification="EVIDENCE_UNAVAILABLE",
                   reason="owner-approved-optional-exclusion: damaged optional value preserved only in raw evidence",
                   locator=_locator(asin, field, attr, position))
            return ""
        return raw
    return ""


def _owner_optional_exclusion_records(parent_records: Iterable[Mapping], *, parent_dataset_hash: str) -> tuple[list[dict], list[dict]]:
    """Derive a narrow, hash-bound display view without mutating raw evidence."""
    records: list[dict] = []
    repair_log: list[dict] = []
    for parent_record in parent_records:
        record = deepcopy(dict(parent_record))
        asin = normalize_asin(record.get("asin"))
        decision = _OWNER_OPTIONAL_ATTRIBUTE_EXCLUSIONS.get(asin)
        if not decision:
            records.append(record)
            continue
        raw_attributes = record.get("rawattributes_raw")
        if not isinstance(raw_attributes, list):
            raw_attributes = record.get("attributes") or []
        raw_attributes = [dict(attr) for attr in raw_attributes if isinstance(attr, Mapping)]
        labels = set(decision["labels"])
        excluded = [attr for attr in raw_attributes if _label(attr.get("label_raw")) in labels]
        if not excluded:
            records.append(record)
            continue
        eligible = [attr for attr in raw_attributes if _label(attr.get("label_raw")) not in labels]
        parent_record_hash = _hash(parent_record)
        record["rawattributes_raw"] = deepcopy(raw_attributes)
        record["eligibleattributes"] = deepcopy(eligible)
        # Only this display/translation-derived field is rebuilt. ``attributes``
        # remains raw evidence so no source fact is deleted or overwritten.
        record["product_details_es"] = render_details_es(eligible)
        evidence = {
            "schema_version": OWNER_OPTIONAL_ATTRIBUTE_EXCLUSION_SCHEMA_VERSION,
            "owner_decision": decision["owner_decision"],
            "field": decision["field"],
            "parent_dataset_canonical_hash": parent_dataset_hash,
            "parent_record_hash": parent_record_hash,
            "raw_attributes_hash": _hash(raw_attributes),
            "eligible_attributes_hash": _hash(eligible),
            "excluded_attributes": deepcopy(excluded),
        }
        record["owner_optional_exclusion"] = evidence
        record_for_hash = {key: value for key, value in record.items()
                           if key not in {"source_record_hash", "owner_optional_exclusion"}}
        source_record_hash = _hash(record_for_hash)
        excluded_locator_hashes = [_hash(_locator(asin, decision["field"], attr, position))
                                   for position, attr in enumerate(raw_attributes)
                                   if _label(attr.get("label_raw")) in labels]
        repair_chain = {
            "policy": "owner-approved-optional-attribute-exclusion-v1",
            "owner_decision": decision["owner_decision"],
            "old_source_hash": parent_record_hash,
            "locator_hashes": excluded_locator_hashes,
            "new_record_hash": source_record_hash,
        }
        evidence["repair_chain"] = repair_chain
        record["source_record_hash"] = source_record_hash
        repair_log.append({
            "asin": asin,
            "field": decision["field"],
            "owner_decision": decision["owner_decision"],
            "parent_dataset_canonical_hash": parent_dataset_hash,
            "parent_record_hash": parent_record_hash,
            "source_record_hash": source_record_hash,
            "repair_chain": repair_chain,
            "excluded_attribute_count": len(excluded),
        })
        records.append(record)
    return records, repair_log


def _has_multilingual_support(detail: Mapping) -> bool:
    language = _attribute_values(detail, {"idioma", "language"})
    isbn = _attribute_values(detail, {"isbn"})
    return bool(isbn and any("japones" in _label(attr.get("value_raw")) or "japanese" in _label(attr.get("value_raw"))
                         for _, attr in language))


def _multilingual_reviews(detail: Mapping, asin: str, queue: list[dict]) -> None:
    supported = _has_multilingual_support(detail)
    for position, attr in enumerate(_attrs(detail)):
        if not _CJK_RE.search(_text(attr.get("value_raw"))):
            continue
        if supported:
            _queue(queue, asin=asin, field="attributes", classification="VALID_MULTILINGUAL_EVIDENCE",
                   status="PASS", reason="CJK text retained: Japanese language plus ISBN are explicit source evidence",
                   locator=_locator(asin, "attributes", attr, position))
        else:
            _queue(queue, asin=asin, field="attributes", classification="MULTILINGUAL_ATTRIBUTE_REVIEW",
                   reason="CJK attribute retained as raw evidence; source language support was not established",
                   locator=_locator(asin, "attributes", attr, position))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _spanish_only(record: dict) -> dict:
    """Remove display-language overlays from a source-only candidate."""
    for key in list(record):
        if key.endswith("_zh") or key == "��\u96c6\u76ee\u5206\u7c7b":
            record.pop(key, None)
    return record


def _confirmed_self_parent(detail: Mapping, asin: str, cache_root: Path | None) -> bool:
    """Accept self-parent only from parser evidence tied to saved HTML bytes."""
    evidence = detail.get("variation_evidence")
    if not isinstance(evidence, Mapping) or str(evidence.get("producer") or "").casefold() != "parser":
        return False
    family = {normalize_asin(value) for value in evidence.get("family_asins") or ()}
    if normalize_asin(evidence.get("current_asin")) != asin or normalize_asin(evidence.get("parent_asin")) != asin:
        return False
    if not any(value and value != asin for value in family):
        return False
    raw_path = _text(evidence.get("cache_html_path"))
    expected_hash = _text(evidence.get("cache_html_sha256"))
    if not raw_path or not re.fullmatch(r"[0-9a-f]{64}", expected_hash.casefold()):
        return False
    path = Path(raw_path)
    if not path.is_absolute() and cache_root is not None:
        path = cache_root / path
    return path.is_file() and _file_sha256(path) == expected_hash.casefold()


def _ranking_category_provenance(primary: Mapping) -> dict:
    """Bind canonical categories to the selected ranking context, not detail inference."""
    category_path = _text(primary.get("ranking_source_category_path"))
    return {
        "source": "ranking_context",
        "ranking_context_hash": _hash(dict(primary)),
        "ranking_source_url": _text(primary.get("ranking_source_url")),
        "ranking_page_number": primary.get("ranking_page_number"),
        "browse_node_id": _text(primary.get("browse_node_id")),
        "ranking_source_category_path": category_path,
        "levels": {key: primary.get(key) for key in _RANKING_CATEGORY_KEYS[:-1]},
        "leaf_category": primary.get("leaf_category"),
    }


def _restore_ranking_categories(record: dict, primary: Mapping) -> None:
    """Undo normalizer breadcrumb enrichment for this ranking-bound candidate."""
    for key in _RANKING_CATEGORY_KEYS:
        record[key] = primary.get(key)
    record["research_category"] = primary.get("research_category")
    record["category_provenance"] = _ranking_category_provenance(primary)


def _merged_records(candidate_manifest: list[Mapping], details: list[Mapping], rankings: list[Mapping], scope: set[str]) -> list[dict]:
    selected_candidates = [dict(record) for record in candidate_manifest if normalize_asin(record.get("asin")) in scope]
    selected_details = [dict(record) for record in details if normalize_asin(record.get("asin")) in scope]
    merged = merge_ranking_and_detail(selected_candidates, selected_details)
    contexts: dict[str, list[dict]] = defaultdict(list)
    for ranking in rankings:
        asin = normalize_asin(ranking.get("asin"))
        if asin in scope:
            context = dict(ranking)
            if context not in contexts[asin]:
                contexts[asin].append(context)
    for record in merged:
        record["ranking_contexts"] = contexts.get(normalize_asin(record.get("asin")), [])
    return merged


def _require_scope(candidate_manifest: list[Mapping], details: list[Mapping], source_audit: Mapping) -> tuple[set[str], dict[str, Mapping]]:
    bindings = source_audit.get("record_bindings")
    if not isinstance(bindings, Mapping) or not bindings:
        raise ValueError("source audit has no record_bindings")
    scope = {normalize_asin(asin) for asin in bindings if normalize_asin(asin)}
    detail_map: dict[str, Mapping] = {}
    for detail in details:
        asin = normalize_asin(detail.get("asin"))
        if asin in scope and asin not in detail_map:
            identity = _text(detail.get("identity_status_code") or detail.get("identity_status")).upper()
            if identity not in {"MATCH", "IDENTITY_MATCH"}:
                continue
            detail_map[asin] = detail
    candidate_set = {normalize_asin(record.get("asin")) for record in candidate_manifest if normalize_asin(record.get("asin"))}
    if not scope <= candidate_set:
        raise ValueError("source audit scope is not contained in candidate manifest")
    if set(detail_map) != scope:
        missing = sorted(scope - set(detail_map))
        extra = sorted(set(detail_map) - scope)
        raise ValueError(f"identity-MATCH detail scope differs from source audit: missing={missing[:5]} extra={extra[:5]}")
    return scope, detail_map


def build_spanish_source_candidate(
    candidate_manifest: Iterable[Mapping],
    details: Iterable[Mapping],
    rankings: Iterable[Mapping],
    source_audit: Mapping,
    *,
    expected_candidate_hash: str,
    cache_root: str | Path | None = None,
    snapshot_provenance: Mapping | None = None,
) -> dict:
    """Build a Spanish evidence candidate without promoting it to Master."""
    candidates = [dict(record) for record in candidate_manifest if isinstance(record, Mapping)]
    detail_rows = [dict(record) for record in details if isinstance(record, Mapping)]
    ranking_rows = [dict(record) for record in rankings if isinstance(record, Mapping)]
    actual_hash = candidate_manifest_hash(candidates)
    if actual_hash != expected_candidate_hash:
        raise ValueError(f"candidate manifest hash mismatch: expected {expected_candidate_hash}, got {actual_hash}")
    scope, detail_map = _require_scope(candidates, detail_rows, source_audit)
    cache_path = Path(cache_root) if cache_root else None
    snapshot_ref = _hash(snapshot_provenance or {}) if snapshot_provenance else "UNKNOWN"
    primary_candidates = {
        normalize_asin(item.get("asin")): item for item in candidates
        if normalize_asin(item.get("asin")) in scope
    }
    merged = _merged_records(candidates, detail_rows, ranking_rows, scope)
    if {normalize_asin(record.get("asin")) for record in merged} != scope:
        raise ValueError("merged candidate scope is not exact")

    queue: list[dict] = []
    records: list[dict] = []
    for source in merged:
        asin = normalize_asin(source.get("asin"))
        detail = detail_map[asin]
        # Reuse canonical price/identity/spec normalization, then explicitly
        # remove display-language overlays: this artifact is Spanish source
        # evidence, not a translation output.
        record = _spanish_only(normalize_product(deepcopy(source)))
        # ``normalize_product`` may enrich blank ranking L3/leaf values from a
        # product-detail breadcrumb.  That is useful for a display helper but
        # is not permitted to rewrite this source candidate's ranking taxonomy.
        _restore_ranking_categories(record, primary_candidates[asin])
        record["attributes"] = _attrs(detail)
        record["feature_bullets_raw"] = deepcopy(detail.get("feature_bullets_raw") or [])
        record["detail_bullets_raw"] = deepcopy(detail.get("detail_bullets_raw") or [])
        record["brand"] = _explicit_brand(detail, asin, queue)
        record["manufacturer"] = _optional_attribute(detail, asin, "manufacturer", {"fabricante", "manufacturer"}, queue)
        record["speaker_type"] = _optional_attribute(detail, asin, "speaker_type", {"tipo de altavoz", "speaker type"}, queue)
        if normalize_asin(detail.get("parent_asin")) == asin and not _confirmed_self_parent(detail, asin, cache_path):
            record["parent_asin"] = ""
            record["parent_asin_status"] = "unconfirmed"
            _queue(queue, asin=asin, field="parent_asin", classification="EVIDENCE_UNAVAILABLE",
                   reason="self-parent cleared: parser variation evidence tied to an actual saved HTML hash is unavailable",
                   locator={"asin": asin, "field": "parent_asin", "source": "detail", "value_raw": detail.get("parent_asin")})
        _multilingual_reviews(detail, asin, queue)
        primary = primary_candidates.get(asin, {})
        record["metadata"] = {
            "research_category": _text(primary.get("research_category")) or "UNKNOWN",
            "collection_batch": _text(primary.get("collection_batch")) or "UNKNOWN",
            "collection_time": _text(primary.get("collection_time")) or "UNKNOWN",
            "ranking_provenance": {"context_count": len(record.get("ranking_contexts") or []), "hash": _hash(record.get("ranking_contexts") or [])},
            "detail_provenance": {"detail_hash": _hash(detail), "detail_schema_version": detail.get("detail_schema_version") or "UNKNOWN",
                                  "parser_version": detail.get("detail_parser_version") or detail.get("parser_version") or "UNKNOWN"},
            # The full snapshot/report evidence is retained once at the top
            # level.  Per-SKU rows carry a compact stable reference rather
            # than duplicating a multi-megabyte report thousands of times.
            "source_snapshot_provenance": {
                "reference_hash": snapshot_ref,
                "ranking_source_url": _text(primary.get("ranking_page_url") or primary.get("ranking_source_url")) or "UNKNOWN",
            },
        }
        records.append(record)

    # This recomputation is diagnostic evidence only.  The supplied audit remains
    # the promotion authority because it is bound to the reviewed 5,480 scope.
    closure_audit = audit_source_fields(records)
    source_gate = evaluate_source_gate(source_audit)
    closure_gate = evaluate_source_gate(closure_audit)
    for field_audit in closure_audit.get("field_audits") or []:
        if not isinstance(field_audit, Mapping) or field_audit.get("classification") in {"PASS", "WARN"}:
            continue
        item = dict(field_audit)
        item["origin"] = "closure_field_audit"
        queue.append(item)
    for issue in source_audit.get("issues") or []:
        if isinstance(issue, Mapping):
            item = dict(issue)
            # Reviewed source-audit issues and closure findings use different
            # key names.  Normalize only the queue envelope; retain the full
            # original issue as evidence instead of mutating it.
            item.setdefault("field", item.get("field_name") or item.get("field") or "unspecified")
            item.setdefault("classification", item.get("issue_code") or item.get("classification") or "SOURCE_AUDIT_ISSUE")
            item["origin"] = "reviewed_source_audit"
            queue.append(item)
    binding_scope = sorted(scope)
    queue.sort(key=lambda item: (str(item.get("asin") or ""), str(item.get("field") or ""), str(item.get("classification") or item.get("issue") or "")))
    candidate_status = (
        "CANDIDATE_SOURCE_GATE_BLOCKED" if not source_gate["ready"]
        else "CANDIDATE_CLOSURE_GATE_BLOCKED" if not closure_gate["ready"]
        else "CANDIDATE_UNPROMOTED"
    )
    return {
        "schema_version": SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION,
        "status": candidate_status,
        "candidate_manifest_hash": actual_hash,
        "binding_scope": {"count": len(binding_scope), "asins": binding_scope, "exact_match": True},
        "source_gate": source_gate,
        "closure_source_gate": closure_gate,
        "source_audit_hash": canonical_audit_hash(source_audit),
        "source_audit": dict(source_audit),
        "closure_audit": closure_audit,
        "snapshot_provenance": dict(snapshot_provenance or {}),
        "records": records,
        "source_review_queue": queue,
    }


def _artifact_hash(path: Path) -> str:
    return _file_sha256(path)


def _audit_markdown(result: Mapping) -> str:
    queue = result.get("source_review_queue") or []
    fields = Counter(str(item.get("field") or "unspecified") for item in queue if isinstance(item, Mapping))
    kinds = Counter(str(item.get("classification") or item.get("issue") or "unspecified") for item in queue if isinstance(item, Mapping))
    closure = result.get("closure_audit") or {}
    issues = [item for item in closure.get("issues") or [] if isinstance(item, Mapping)]
    field_audits = [item for item in closure.get("field_audits") or [] if isinstance(item, Mapping)]
    code_severity = Counter((str(item.get("severity") or "UNKNOWN"), str(item.get("issue_code") or "UNKNOWN")) for item in issues)
    field_severity = Counter((str(item.get("severity") or "UNKNOWN"), str(item.get("field") or "unspecified")) for item in field_audits)
    blocking = [item for item in issues if item.get("status") == "BLOCK" and item.get("severity") in {"P0", "P1"}]
    review = [item for item in issues if item.get("status") == "REVIEW"]
    lines = [
        "# Spanish source closure audit",
        "",
        f"- Candidate status: `{result.get('status')}`",
        f"- SourceGate: `{(result.get('source_gate') or {}).get('status')}` (not promoted)",
        f"- Exact binding scope: `{(result.get('binding_scope') or {}).get('count')}` ASINs",
        f"- Candidate manifest hash: `{result.get('candidate_manifest_hash')}`",
        "",
        "## Review queue by field",
        "",
    ]
    lines.extend(f"- `{field}`: {count}" for field, count in sorted(fields.items()))
    lines.extend(["", "## Review queue by classification", ""])
    lines.extend(f"- `{kind}`: {count}" for kind, count in sorted(kinds.items()))
    lines.extend(["", "## Current closure audit: code and severity", ""])
    lines.extend(f"- `{severity}` `{code}`: {count}" for (severity, code), count in sorted(code_severity.items()))
    lines.extend(["", "## Current closure audit: field and severity", ""])
    lines.extend(f"- `{severity}` `{field}`: {count}" for (severity, field), count in sorted(field_severity.items()))
    lines.extend([
        "", "## Current closure disposition", "",
        f"- Blocking P0/P1 findings (`status=BLOCK`): {len(blocking)} across {len({item.get('asin') for item in blocking if item.get('asin')})} SKUs",
        f"- Review findings (`status=REVIEW`, including P1): {len(review)} across {len({item.get('asin') for item in review if item.get('asin')})} SKUs",
        "- P2 findings are reported above but are not counted as blocking.",
        "", "## Historical audit", "",
        "The reviewed input source audit is retained separately as `historical_source_audit.json`; it is historical evidence, not a newly re-authorized SourceGate report.",
        "", "## Regression note", "", "Rank/Detail-BSR separation and unit semantics are re-audited from current code; this candidate does not suppress remaining source blockers.", ""])
    return "\n".join(lines)


def write_spanish_source_candidate(output_dir: str | Path, result: Mapping) -> dict:
    """Write a new immutable closure directory and return its manifest."""
    directory = Path(output_dir)
    if directory.exists():
        raise FileExistsError(f"immutable output already exists: {directory}")
    directory.mkdir(parents=True)
    records = list(result.get("records") or [])
    master_path = directory / "spanish_master_5480.json"
    master_path.write_text(json.dumps({key: value for key, value in result.items() if key != "source_review_queue"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    csv_path = directory / "spanish_master_5480.csv"
    columns = ["asin", "parent_asin", "parent_asin_status", "title_es_raw", "brand", "manufacturer", "speaker_type", "current_price", "original_price", "discount_rate", "bestseller_rank", "detail_bsr_raw", "research_category", "collection_batch", "collection_time", "attributes", "feature_bullets_raw", "ranking_contexts"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            metadata = row.get("metadata") or {}
            for key in ("research_category", "collection_batch", "collection_time"):
                row[key] = metadata.get(key, "UNKNOWN")
            for key in ("attributes", "feature_bullets_raw", "ranking_contexts"):
                row[key] = json.dumps(row.get(key), ensure_ascii=False, sort_keys=True, default=str)
            writer.writerow(row)
    historical_path = directory / "historical_source_audit.json"
    historical_path.write_text(json.dumps(result.get("historical_source_audit") or result.get("source_audit") or {}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    audit_path = directory / "audit.json"
    audit_path.write_text(json.dumps({
        "schema_version": result.get("schema_version"), "status": result.get("status"),
        "candidate_manifest_hash": result.get("candidate_manifest_hash"), "binding_scope": result.get("binding_scope"),
        "source_gate": result.get("source_gate"), "closure_source_gate": result.get("closure_source_gate"),
        "current_source_audit": result.get("source_audit"),
        "source_audit_history": {"path": historical_path.name, "hash": canonical_audit_hash(result.get("historical_source_audit") or result.get("source_audit") or {})},
        "closure_audit": result.get("closure_audit"),
        "current_source_gate": result.get("current_source_gate"),
        "rank_matrix_diagnostic": result.get("rank_matrix_diagnostic"),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (directory / "audit.md").write_text(_audit_markdown(result), encoding="utf-8")
    queue_path = directory / "source_review_queue.json"
    queue_path.write_text(json.dumps(result.get("source_review_queue") or [], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    manifest = {
        "schema_version": SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION,
        "status": result.get("status"),
        "dataset_canonical_hash": _hash(records),
        "frozen_candidate_manifest_canonical_hash": result.get("candidate_manifest_hash"),
        "binding_scope": result.get("binding_scope"), "source_gate": result.get("source_gate"),
        "code_versions": result.get("code_versions") or {},
        "artifacts": {name: _artifact_hash(directory / name) for name in ("spanish_master_5480.json", "spanish_master_5480.csv", "audit.json", "audit.md", "source_review_queue.json", "historical_source_audit.json")},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def derive_owner_excluded_scope(parent_records: Iterable[Mapping], *, parent_dataset_canonical_hash: str) -> dict:
    """Derive a non-destructive owner-excluded ASIN scope from a frozen parent.

    This does not modify raw records or fill replacement products.  The parent
    data set remains the immutable 5,480-record evidence set; consumers must
    opt into the returned effective 5,478-ASIN scope.
    """
    records = [dict(record) for record in parent_records if isinstance(record, Mapping)]
    actual_hash = _hash(records)
    if parent_dataset_canonical_hash != actual_hash:
        raise ValueError("parent dataset canonical hash does not match supplied records")
    asins = [normalize_asin(record.get("asin")) for record in records]
    if not all(asins) or len(asins) != len(set(asins)):
        raise ValueError("parent records must contain one valid unique ASIN per record")
    missing = sorted(set(_OWNER_EXCLUSIONS) - set(asins))
    if missing:
        raise ValueError(f"owner exclusion ASINs are absent from parent scope: {missing}")
    effective_asins = sorted(set(asins) - set(_OWNER_EXCLUSIONS))
    return {
        "schema_version": OWNER_EXCLUSION_SCOPE_SCHEMA_VERSION,
        "parent_scope": {
            "record_count": len(asins),
            "dataset_canonical_hash": parent_dataset_canonical_hash,
        },
        "owner_exclusions": [
            {"asin": asin, "reason": reason, "raw_evidence_preserved": True}
            for asin, reason in sorted(_OWNER_EXCLUSIONS.items())
        ],
        "effective_scope": {
            "record_count": len(effective_asins),
            "asins": effective_asins,
            "canonical_asin_hash": _hash(effective_asins),
            "replacement_products_added": 0,
        },
    }


def write_owner_excluded_scope(output_dir: str | Path, scope: Mapping) -> dict:
    """Write a new immutable owner-exclusion scope artifact."""
    directory = Path(output_dir)
    if directory.exists():
        raise FileExistsError(f"immutable output already exists: {directory}")
    directory.mkdir(parents=True)
    payload = dict(scope)
    artifact = directory / "owner_exclusions.json"
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": OWNER_EXCLUSION_SCOPE_SCHEMA_VERSION,
        "parent_scope": payload.get("parent_scope"),
        "effective_scope": {
            key: (payload.get("effective_scope") or {}).get(key)
            for key in ("record_count", "canonical_asin_hash", "replacement_products_added")
        },
        "artifacts": {artifact.name: _artifact_hash(artifact)},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def rank_matrix_diagnostics(rankings: Iterable[Mapping], *, exact_scope: set[str], owner_scope: Mapping) -> dict:
    """Audit rank gaps against the complete matrix, not an owner-excluded view."""
    slots: dict[tuple[str, int], set[int]] = defaultdict(set)
    excluded: list[dict] = []
    owner_reasons = {normalize_asin(item.get("asin")): item for item in owner_scope.get("owner_exclusions") or []
                     if isinstance(item, Mapping) and normalize_asin(item.get("asin"))}
    parent_asins = set(owner_scope.get("effective_scope", {}).get("asins") or ()) | set(owner_reasons)
    for ranking in rankings:
        if not isinstance(ranking, Mapping):
            continue
        asin = normalize_asin(ranking.get("asin"))
        source = _text(ranking.get("ranking_source_url") or ranking.get("source_url"))
        try:
            page, rank = int(ranking.get("ranking_page_number", ranking.get("page_number"))), int(ranking.get("bestseller_rank", ranking.get("ranking_rank")))
        except (TypeError, ValueError):
            continue
        slots[(source, page)].add(rank)
        if asin and asin not in exact_scope and asin in parent_asins:
            excluded.append({
                "asin": asin, "classification": "OUT_OF_EXACT_SCOPE", "status": "INFO",
                "ranking_slot": {"source": source, "page": page, "rank": rank},
                "exclusion": owner_reasons.get(asin, {"asin": asin, "reason": "exact-identity exclusion"}),
            })
    issues = []
    for (source, page), ranks in sorted(slots.items()):
        if len(ranks) > 1:
            missing = sorted(set(range(min(ranks), max(ranks) + 1)) - ranks)
            if missing:
                issues.append({"issue_code": "RANK_GAP", "status": "REVIEW", "severity": "P2",
                               "field": "bestseller_rank", "evidence": {"source": source, "page": page, "missing": missing}})
    return {"complete_matrix_slot_count": sum(len(ranks) for ranks in slots.values()),
            "out_of_exact_scope": excluded, "issues": issues}


def load_builder_unresolved_decision_artifact(queue_path: str | Path, manifest_path: str | Path) -> dict:
    """Load a builder queue only when its immutable manifest binds its bytes.

    The runner owns these two paths.  A caller cannot substitute a new expected
    queue hash because it is read from the reviewed manifest, not from CLI data.
    """
    queue_file, manifest_file = Path(queue_path), Path(manifest_path)
    if not queue_file.is_file() or not manifest_file.is_file():
        raise ValueError("builder unresolved decision queue and immutable manifest are required")
    try:
        queue = json.loads(queue_file.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("builder unresolved decision artifact is unreadable") from exc
    if not isinstance(queue, list) or not queue or not all(isinstance(item, Mapping) for item in queue):
        raise ValueError("builder unresolved decision queue must be a non-empty list of records")
    if not isinstance(manifest, Mapping):
        raise ValueError("builder unresolved decision manifest must be an object")
    if manifest.get("artifact_domain") != BUILDER_UNRESOLVED_DECISION_ARTIFACT_DOMAIN:
        raise ValueError("builder unresolved decision manifest domain is not trusted")
    expected_canonical_hash = _text(manifest.get("expected_queue_canonical_hash"))
    expected_byte_hash = _text(manifest.get("expected_queue_sha256"))
    if not expected_canonical_hash and not expected_byte_hash:
        raise ValueError("builder unresolved decision manifest has no expected queue hash")
    if expected_canonical_hash and expected_canonical_hash != _hash(queue):
        raise ValueError("builder unresolved decision queue canonical hash does not match immutable manifest")
    actual_byte_hash = _file_sha256(queue_file)
    if expected_byte_hash and expected_byte_hash != actual_byte_hash:
        raise ValueError("builder unresolved decision queue byte hash does not match immutable manifest")
    parent_hash = _text(manifest.get("parent_dataset_canonical_hash"))
    if not parent_hash:
        raise ValueError("builder unresolved decision manifest has no parent canonical hash")
    return {
        "artifact_domain": BUILDER_UNRESOLVED_DECISION_ARTIFACT_DOMAIN,
        "parent_dataset_canonical_hash": parent_hash,
        "queue_canonical_hash": _hash(queue),
        "queue_sha256": actual_byte_hash,
        "manifest_canonical_hash": _hash(manifest),
        "decisions": [dict(item) for item in queue],
    }


def _validated_builder_decision_artifact(artifact: Mapping | None, *, parent_hash: str) -> tuple[list[dict], dict]:
    if not isinstance(artifact, Mapping):
        raise ValueError("verified builder unresolved decision artifact is required")
    if artifact.get("artifact_domain") != BUILDER_UNRESOLVED_DECISION_ARTIFACT_DOMAIN:
        raise ValueError("builder unresolved decision artifact domain is not trusted")
    if _text(artifact.get("parent_dataset_canonical_hash")) != parent_hash:
        raise ValueError("builder unresolved decision artifact parent hash does not match supplied parent records")
    decisions = artifact.get("decisions")
    if not isinstance(decisions, list) or not decisions or not all(isinstance(item, Mapping) for item in decisions):
        raise ValueError("builder unresolved decision queue must be a verified non-empty list")
    queue = [dict(item) for item in decisions]
    if _text(artifact.get("queue_canonical_hash")) != _hash(queue):
        raise ValueError("builder unresolved decision artifact queue hash does not match decisions")
    return queue, {
        "artifact_domain": BUILDER_UNRESOLVED_DECISION_ARTIFACT_DOMAIN,
        "parent_canonical_hash": parent_hash,
        "queue_hash": _hash(queue),
        "queue_sha256": _text(artifact.get("queue_sha256")),
        "manifest_canonical_hash": _text(artifact.get("manifest_canonical_hash")),
    }


def _locator_present(record: Mapping, locator: Mapping) -> bool:
    expected = (str(locator.get("label_raw") or ""), str(locator.get("value_raw") or ""))
    return expected in [(str(attr.get("label_raw") or ""), str(attr.get("value_raw") or ""))
                        for attr in (record.get("attributes") or []) if isinstance(attr, Mapping)]


def _approved_optional_repair_chain(record: Mapping, item: Mapping, *, parent_hash: str) -> Mapping | None:
    """Return the sole approved repair evidence, never infer repair from absence."""
    asin = normalize_asin(record.get("asin"))
    policy = _OWNER_OPTIONAL_ATTRIBUTE_EXCLUSIONS.get(asin)
    evidence = record.get("owner_optional_exclusion")
    if not policy or not isinstance(evidence, Mapping) or item.get("field") != policy["field"]:
        return None
    chain = evidence.get("repair_chain")
    locator = item.get("evidence_locator") or {}
    if not isinstance(chain, Mapping) or not isinstance(locator, Mapping):
        return None
    required = {
        "policy": "owner-approved-optional-attribute-exclusion-v1",
        "owner_decision": policy["owner_decision"],
        "old_source_hash": evidence.get("parent_record_hash"),
        "new_record_hash": record.get("source_record_hash"),
    }
    if evidence.get("parent_dataset_canonical_hash") != parent_hash or any(chain.get(key) != value for key, value in required.items()):
        return None
    hashes = chain.get("locator_hashes")
    if not isinstance(hashes, list) or _hash(dict(locator)) not in hashes:
        return None
    return chain


def _builder_issue(item: Mapping, *, issue_code: str, message: str, locator: Mapping, parent_hash: str) -> dict:
    return {
        "asin": normalize_asin(item.get("asin")), "field": item.get("field") or "attributes",
        "stage": "builder_unresolved", "check": "source_fields", "severity": "P1", "status": "REVIEW",
        "issue_code": issue_code, "field_classification": "REVIEW_REQUIRED", "message": message,
        "source_file": "builder_unresolved_decisions",
        "evidence": {"locator": dict(locator), "parent_canonical_hash": parent_hash},
    }


def _merge_builder_unresolved_decisions(audit: Mapping, records: Iterable[Mapping], decisions: Iterable[Mapping], *, parent_hash: str,
                                        artifact_state: Mapping) -> tuple[dict, dict]:
    """Carry current builder facts forward under explicit repair/report policy."""
    merged = deepcopy(dict(audit))
    record_map = {normalize_asin(row.get("asin")): row for row in records if isinstance(row, Mapping)}
    queue = [dict(item) for item in decisions if isinstance(item, Mapping)]
    inherited, resolved, reports = [], [], []
    for item in queue:
        asin = normalize_asin(item.get("asin"))
        if asin not in record_map:
            reports.append({**item, "decision": "OUT_OF_EXACT_SCOPE"})
            continue
        classification = str(item.get("classification") or "")
        locator = item.get("evidence_locator") or {}
        record = record_map[asin]
        locator = locator if isinstance(locator, Mapping) else {}
        present = _locator_present(record, locator)
        repair_chain = _approved_optional_repair_chain(record, item, parent_hash=parent_hash)
        if classification == "MULTILINGUAL_ATTRIBUTE_REVIEW" and present:
            issue = _builder_issue(item, issue_code=classification, message=item.get("reason") or classification,
                                  locator=locator, parent_hash=parent_hash)
            merged.setdefault("issues", []).append(issue)
            merged.setdefault("field_audits", []).append({"asin": asin, "field": "attributes", "classification": "REVIEW_REQUIRED", "severity": "P1", "message": issue["message"], "evidence": issue["evidence"]})
            merged.setdefault("sku_status", {})[asin] = "REVIEW_REQUIRED"
            inherited.append(issue)
        elif classification == "VALID_MULTILINGUAL_EVIDENCE" and present and _has_multilingual_support(record):
            reports.append({**item, "decision": "VALID_MULTILINGUAL_EVIDENCE"})
        elif repair_chain:
            resolved.append({**item, "decision": "REPAIR_CHAIN_RESOLVED", "repair_chain": dict(repair_chain)})
        elif classification == "EVIDENCE_UNAVAILABLE" and item.get("field") in {"parent_asin", "brand"} and not _text(record.get(item.get("field"))):
            reports.append({**item, "decision": "SOURCE_MISSING_P2_REPORT", "severity": "P2",
                            "field_classification": "SOURCE_MISSING"})
        else:
            code = "EVIDENCE_LOST" if classification == "MULTILINGUAL_ATTRIBUTE_REVIEW" else classification or "UNCLASSIFIED_BUILDER_DECISION"
            issue = _builder_issue(item, issue_code=code,
                                  message="source locator is no longer available without an approved repair chain" if code == "EVIDENCE_LOST" else (item.get("reason") or code),
                                  locator=locator, parent_hash=parent_hash)
            merged.setdefault("issues", []).append(issue)
            merged.setdefault("field_audits", []).append({"asin": asin, "field": item.get("field") or "attributes", "classification": "REVIEW_REQUIRED", "severity": "P1", "message": issue["message"], "evidence": issue["evidence"]})
            merged.setdefault("sku_status", {})[asin] = "REVIEW_REQUIRED"
            inherited.append(issue)
    merged["summary"] = {**dict(merged.get("summary") or {}), "issue_count": len(merged.get("issues") or [])}
    merged["status"] = "BLOCK" if "BLOCKED" in (merged.get("sku_status") or {}).values() else "REVIEW" if "REVIEW_REQUIRED" in (merged.get("sku_status") or {}).values() else "PASS"
    return merged, {**dict(artifact_state), "inherited": inherited, "resolved": resolved, "reports": reports}


def build_current_source_gate_candidate(
    candidate_manifest: Iterable[Mapping], details: Iterable[Mapping], rankings: Iterable[Mapping],
    parent_records: Iterable[Mapping], owner_scope: Mapping, *, expected_input_hashes: Mapping,
    historical_source_audit: Mapping | None = None, cache_root: str | Path | None = None,
    snapshot_provenance: Mapping | None = None, progress=None,
    builder_decision_artifact: Mapping | None = None,
) -> dict:
    """Revalidate a hash-bound owner subset; history is retained but not authoritative."""
    emit = progress or (lambda *_args, **_kwargs: None)
    emit("CURRENT_GATE_VALIDATE_START")
    candidates = [item for item in candidate_manifest if isinstance(item, Mapping)]
    detail_rows = [item for item in details if isinstance(item, Mapping)]
    ranking_rows = [item for item in rankings if isinstance(item, Mapping)]
    parents = [item for item in parent_records if isinstance(item, Mapping)]
    emit("CURRENT_GATE_HASH_START")
    observed_hashes = {"candidate_manifest": _hash(candidates), "details": _hash(detail_rows), "rankings": _hash(ranking_rows)}
    for name, observed in observed_hashes.items():
        if str(expected_input_hashes.get(name) or "") != observed:
            raise ValueError(f"input hash mismatch for {name}")
    emit("CURRENT_GATE_HASH_DONE")
    parent = owner_scope.get("parent_scope") or {}
    if parent.get("dataset_canonical_hash") != _hash(parents):
        raise ValueError("owner scope parent hash does not match supplied parent records")
    parent_asins = {normalize_asin(item.get("asin")) for item in parents if normalize_asin(item.get("asin"))}
    expected_scope = {normalize_asin(item) for item in (owner_scope.get("effective_scope") or {}).get("asins") or () if normalize_asin(item)}
    exclusions = {normalize_asin(item.get("asin")) for item in owner_scope.get("owner_exclusions") or []
                  if isinstance(item, Mapping) and normalize_asin(item.get("asin"))}
    if not expected_scope or expected_scope | exclusions != parent_asins or expected_scope & exclusions:
        raise ValueError("owner scope is not an exact parent-minus-exclusions subset")
    if (owner_scope.get("effective_scope") or {}).get("canonical_asin_hash") != _hash(sorted(expected_scope)):
        raise ValueError("owner scope effective ASIN hash does not match")
    candidate_asins = {normalize_asin(item.get("asin")) for item in candidates if normalize_asin(item.get("asin"))}
    detail_map = {normalize_asin(item.get("asin")): item for item in detail_rows
                  if normalize_asin(item.get("asin")) in expected_scope
                  and _text(item.get("identity_status_code") or item.get("identity_status")).upper() in {"MATCH", "IDENTITY_MATCH"}}
    if not expected_scope <= candidate_asins or set(detail_map) != expected_scope:
        raise ValueError("current scope does not have exact candidate and identity-MATCH detail evidence")
    ranking_contexts: dict[str, set[str]] = defaultdict(set)
    for ranking in ranking_rows:
        asin = normalize_asin(ranking.get("asin"))
        if asin in expected_scope:
            ranking_contexts[asin].add(_hash(dict(ranking)))
    selected_parent_records = [record for record in parents if normalize_asin(record.get("asin")) in expected_scope]
    records, owner_optional_repair_log = _owner_optional_exclusion_records(
        selected_parent_records,
        parent_dataset_hash=str(parent.get("dataset_canonical_hash") or ""),
    )
    if {normalize_asin(record.get("asin")) for record in records} != expected_scope:
        raise ValueError("owner scope does not select an exact parent record set")
    for index, record in enumerate(records, start=1):
        if index % 100 == 0:
            emit("CURRENT_GATE_VALIDATE_PROGRESS", records=index)
        asin = normalize_asin(record.get("asin"))
        if not record.get("ranking_contexts") or not all(_hash(dict(ctx)) in ranking_contexts[asin]
                                                          for ctx in record.get("ranking_contexts") if isinstance(ctx, Mapping)):
            raise ValueError(f"saved canonical record has unbound ranking context for {asin}")
        detail = detail_map[asin]
        if normalize_asin(detail.get("parent_asin")) == asin and not _confirmed_self_parent(detail, asin, Path(cache_root) if cache_root else None):
            if _text(record.get("parent_asin")):
                raise ValueError(f"unconfirmed self-parent was retained for {asin}")
    emit("CURRENT_AUDIT_START", records=len(records))
    current_audit = audit_source_fields(records, ranking_matrix=ranking_rows, progress=emit)
    parent_hash = str(parent.get("dataset_canonical_hash") or "")
    builder_queue, builder_state = _validated_builder_decision_artifact(
        builder_decision_artifact, parent_hash=parent_hash)
    current_audit, builder_state = _merge_builder_unresolved_decisions(
        current_audit, records, builder_queue, parent_hash=parent_hash, artifact_state=builder_state)
    emit("CURRENT_AUDIT_DONE", issues=len(current_audit.get("issues") or []))
    current_gate = evaluate_source_gate(current_audit)
    rank_diagnostic = rank_matrix_diagnostics(ranking_rows, exact_scope=expected_scope, owner_scope=owner_scope)
    queue = [dict(item, origin="current_field_audit") for item in current_audit.get("field_audits") or []
             if isinstance(item, Mapping) and item.get("classification") not in {"PASS", "WARN"}]
    result = {
        "schema_version": CURRENT_SOURCE_GATE_SCHEMA_VERSION,
        "candidate_manifest_hash": candidate_manifest_hash(candidates),
        "binding_scope": {"count": len(expected_scope), "asins": sorted(expected_scope), "exact_match": True},
        "historical_source_audit": dict(historical_source_audit or {}),
        "source_audit": current_audit,
        "current_input_hashes": observed_hashes,
        "current_source_audit": current_audit,
        "current_source_gate": current_gate,
        "source_audit_hash": canonical_audit_hash(current_audit),
        "closure_audit": current_audit,
        "rank_matrix_diagnostic": rank_diagnostic,
        "source_gate": current_gate,
        "promotion_state": {"candidate": True, "reviewed_master": False, "eligible": current_gate["ready"]},
        "status": "CANDIDATE_CURRENT_GATE_READY" if current_gate["ready"] else "CANDIDATE_CURRENT_GATE_BLOCKED",
        "snapshot_provenance": dict(snapshot_provenance or {}),
        "records": records,
        "owner_optional_exclusion_repair_log": owner_optional_repair_log,
        "source_review_queue": queue,
        "builder_unresolved_decisions": builder_state,
    }
    return result


__all__ = [
    "SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION", "OWNER_EXCLUSION_SCOPE_SCHEMA_VERSION", "CURRENT_SOURCE_GATE_SCHEMA_VERSION",
    "build_spanish_source_candidate", "candidate_manifest_hash", "write_spanish_source_candidate",
    "derive_owner_excluded_scope", "write_owner_excluded_scope", "rank_matrix_diagnostics",
    "build_current_source_gate_candidate", "load_builder_unresolved_decision_artifact",
]
