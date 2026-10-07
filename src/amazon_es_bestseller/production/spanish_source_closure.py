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


SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION = "spanish-source-closure-v1"
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_MOJIBAKE_RE = re.compile(r"\ufffd|(?:Ã.|Â.)")
_NON_BRAND_BYLINE_RE = re.compile(
    r"(?:^edici[oó]n\s+en\b|^de\b.*\b(?:autor|redactor|traductor|formato)\s*:|\bformato\s*:)", re.I
)
_RANKING_CATEGORY_KEYS = ("category_l1", "category_l2", "category_l3", "leaf_category", "browse_node_id")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


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
    return {
        "schema_version": SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION,
        "status": "CANDIDATE_SOURCE_GATE_BLOCKED" if not source_gate["ready"] else "CANDIDATE_UNPROMOTED",
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
    historical_path.write_text(json.dumps(result.get("source_audit") or {}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    audit_path = directory / "audit.json"
    audit_path.write_text(json.dumps({
        "schema_version": result.get("schema_version"), "status": result.get("status"),
        "candidate_manifest_hash": result.get("candidate_manifest_hash"), "binding_scope": result.get("binding_scope"),
        "source_gate": result.get("source_gate"), "closure_source_gate": result.get("closure_source_gate"),
        "source_audit_history": {"path": historical_path.name, "hash": result.get("source_audit_hash")},
        "closure_audit": result.get("closure_audit"),
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
        "artifacts": {name: _artifact_hash(directory / name) for name in ("spanish_master_5480.json", "spanish_master_5480.csv", "audit.json", "audit.md", "source_review_queue.json", "historical_source_audit.json")},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


__all__ = ["SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION", "build_spanish_source_candidate", "candidate_manifest_hash", "write_spanish_source_candidate"]
