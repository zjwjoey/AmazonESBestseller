"""Conservative, non-Gold reuse candidates from historical Excel exports.

Historical Chinese cells are only references.  They are tied to the current
canonical Spanish field hash and go through the current QA again; this module
never promotes them to a provider-verified translation or a release decision.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .production_contract import (
    PRODUCTION_TRANSLATION_FIELDS,
    canonical_record,
    canonical_source_field,
    source_text,
    target_field_for,
)
from .protection import protect
from .qa import qa_field
from .service import source_hash


LEGACY_REFERENCE_CANDIDATES_SCHEMA_VERSION = "legacy-reference-candidates-v1"
LEGACY_REVIEWED_POLICY_VERSION = "legacy-reviewed-reference-v1"
LEGACY_SOURCE_KIND = "legacy_excel_reference"
LEGACY_PROVIDER = "unknown"

# Only one-cell scalar fields are supported.  Details and bullets require
# item-level boundaries, which a flattened workbook cannot prove.
SUPPORTED_LEGACY_FIELDS = frozenset({
    "title_es_raw", "category_l1", "category_l2", "category_l3",
    "leaf_category", "selected_variation_raw", "specification_es",
    "product_description",
})
UNSUPPORTED_BOUNDARY_FIELDS = frozenset({"product_details", "feature_bullets"})

LEGACY_ES_HEADERS = {
    "商品名称（西语）": "title_es_raw",
    "一级类目": "category_l1",
    "二级类目": "category_l2",
    "三级类目": "category_l3",
    "细分类目": "leaf_category",
    "当前选中规格 / 变体（西语）": "selected_variation_raw",
    "核心规格（西语）": "specification_es",
    "商品描述（西语）": "product_description",
    "产品描述（西语）": "product_description",
}
LEGACY_ZH_HEADERS = {
    "商品名称（中文）": "title_es_raw",
    "一级类目": "category_l1",
    "二级类目": "category_l2",
    "三级类目": "category_l3",
    "细分类目": "leaf_category",
    "当前选中规格 / 变体": "selected_variation_raw",
    "核心规格（中文）": "specification_es",
    "商品描述（中文）": "product_description",
    "产品描述（中文）": "product_description",
}


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonical_hash(record: Mapping[str, Any]) -> str:
    canonical = canonical_record(record)
    return _hash_json({key: source_text(value) for key, value in canonical.items()})


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_workbook(path: Path, headers: Mapping[str, str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read only the scalar reference cells, retaining exact sheet/row evidence."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    if len(workbook.sheetnames) != 1:
        raise ValueError("LEGACY_REFERENCE_WORKBOOK_SHEET_COUNT_UNSAFE")
    sheet = workbook[workbook.sheetnames[0]]
    header_values = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    indices = {str(value or "").strip(): position for position, value in enumerate(header_values)}
    if "ASIN" not in indices:
        raise ValueError("LEGACY_REFERENCE_ASIN_HEADER_MISSING")
    mapped = {field: indices[name] for name, field in headers.items() if name in indices}
    rows: dict[str, dict[str, Any]] = {}
    for row_number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        asin = str(values[indices["ASIN"]] or "").strip().upper() if indices["ASIN"] < len(values) else ""
        if not asin:
            continue
        if asin in rows:
            raise ValueError("LEGACY_REFERENCE_DUPLICATE_ASIN:%s" % asin)
        rows[asin] = {
            "row": row_number,
            "fields": {field: source_text(values[index]) for field, index in mapped.items()
                       if index < len(values) and source_text(values[index])},
        }
    return rows, {"path": str(path), "file_hash": _file_hash(path), "sheet": sheet.title,
                  "mapped_fields": sorted(mapped.values()), "row_count": len(rows)}


def load_legacy_excel_reference(spanish_path: str | Path, chinese_path: str | Path) -> dict[str, dict[str, Any]]:
    """Pair immutable Spanish/Chinese legacy scalar rows by ASIN.

    This does not assert that the old source matches the current canonical
    record.  The builder performs that required field-hash check later.
    """
    es_rows, es_meta = _read_workbook(Path(spanish_path), LEGACY_ES_HEADERS)
    zh_rows, zh_meta = _read_workbook(Path(chinese_path), LEGACY_ZH_HEADERS)
    references: dict[str, dict[str, Any]] = {}
    for asin in sorted(es_rows.keys() & zh_rows.keys()):
        references[asin] = {
            "spanish": es_rows[asin]["fields"],
            "chinese": zh_rows[asin]["fields"],
            "provenance": {
                "source_kind": LEGACY_SOURCE_KIND,
                "provider": LEGACY_PROVIDER,
                "spanish": {**es_meta, "row": es_rows[asin]["row"]},
                "chinese": {**zh_meta, "row": zh_rows[asin]["row"]},
            },
        }
    return references


def build_legacy_reference_candidates(
        records: Iterable[Mapping[str, Any]], legacy_reference: Mapping[str, Mapping[str, Any]],
        source_gate: Mapping[str, Any]) -> dict[str, Any]:
    """Build field-bound historical candidates without a formal promotion."""
    records = list(records)
    candidates: list[dict[str, Any]] = []
    preserved_source_fields: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for raw_record in records:
        record = canonical_record(raw_record)
        asin = str(record.get("asin") or "").upper()
        canonical_hash = _canonical_hash(raw_record)
        brand = source_text(record.get("brand"))
        if brand:
            preserved_source_fields.append({
                "asin": asin, "field": "brand", "value": brand,
                "reason": "BRAND_PRESERVED_SOURCE_NO_LEGACY_REPLACEMENT",
            })
        legacy = legacy_reference.get(asin)
        if not legacy:
            counts["asin_not_in_legacy_intersection"] += 1
            continue
        legacy_es = legacy.get("spanish") if isinstance(legacy.get("spanish"), Mapping) else {}
        legacy_zh = legacy.get("chinese") if isinstance(legacy.get("chinese"), Mapping) else {}
        for field in PRODUCTION_TRANSLATION_FIELDS:
            field = canonical_source_field(field)
            if field == "brand":
                continue
            if field in UNSUPPORTED_BOUNDARY_FIELDS:
                counts["unsupported_boundary_field"] += 1
                continue
            if field not in SUPPORTED_LEGACY_FIELDS:
                continue
            current_es = source_text(record.get(field))
            historical_es = source_text(legacy_es.get(field))
            historical_zh = source_text(legacy_zh.get(field))
            if not current_es or not historical_es or not historical_zh:
                counts["missing_legacy_field"] += 1
                continue
            field_hash = source_hash(current_es)
            if field_hash != source_hash(historical_es):
                counts["source_hash_mismatch"] += 1
                continue
            qa = qa_field(protect(current_es, protected_values=(brand,)), historical_zh, current_es,
                          field=field, brand=brand)
            candidate_status = "PASS" if qa["qa_status"] == "pass" else "QA_FAILED"
            candidates.append({
                "asin": asin,
                "field": field,
                "target_field": target_field_for(field),
                "legacy_es": historical_es,
                "legacy_zh": historical_zh,
                "field_hash": field_hash,
                "current_canonical_hash": canonical_hash,
                "source_kind": LEGACY_SOURCE_KIND,
                "provider": LEGACY_PROVIDER,
                "provenance": legacy.get("provenance") or {},
                "qa": qa,
                "candidate_status": candidate_status,
                "release_admission": "DIAGNOSTIC_ONLY_SOURCE_GATE_BLOCKED"
                if str(source_gate.get("status") or "").upper() != "SOURCE_READY"
                else "DIAGNOSTIC_ONLY_LEGACY_REVIEW_REQUIRED",
            })
            counts[candidate_status.lower()] += 1
    current_canonical_hash = _hash_json(sorted(_canonical_hash(record) for record in records))
    qa_issue_rows = [
        {"asin": candidate["asin"], "field": candidate["field"], **issue}
        for candidate in candidates for issue in candidate["qa"].get("issues", [])
    ]
    return {
        "schema_version": LEGACY_REFERENCE_CANDIDATES_SCHEMA_VERSION,
        "status": "DIAGNOSTIC_ONLY",
        "source_gate_status": str(source_gate.get("status") or "UNKNOWN"),
        "source_binding": {"current_canonical_hash": current_canonical_hash},
        "candidates": candidates,
        "qa_report": {
            "schema_version": "legacy-reference-qa-report-v1",
            "status": "pass" if not qa_issue_rows else "qa_failed",
            "counts": {
                "candidates": len(candidates),
                "pass": counts["pass"],
                "qa_failed": counts["qa_failed"],
                "issues": len(qa_issue_rows),
            },
            "issues": qa_issue_rows,
            "diagnostic_only": True,
        },
        "preserved_source_fields": preserved_source_fields,
        "summary": dict(sorted(counts.items())),
        "formal_promotion": {
            "policy_version_required": LEGACY_REVIEWED_POLICY_VERSION,
            "requires_source_ready_reverification": True,
            "requires_explicit_candidate_review": True,
            "legacy_is_not_provider_verified": True,
        },
    }


def evaluate_legacy_reviewed_formal_gate(
        candidate_manifest: Mapping[str, Any], source_gate: Mapping[str, Any],
        policy_evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Future audit interface; does not promote any candidate by itself."""
    if str(source_gate.get("status") or "").upper() != "SOURCE_READY":
        return {"status": "SOURCE_GATE_NOT_READY", "admitted_candidate_hashes": []}
    expected_hash = (candidate_manifest.get("source_binding") or {}).get("current_canonical_hash")
    if not expected_hash or source_gate.get("canonical_hash") != expected_hash:
        return {"status": "SOURCE_BINDING_REVERIFY_REQUIRED", "admitted_candidate_hashes": []}
    if policy_evidence.get("policy_version") != LEGACY_REVIEWED_POLICY_VERSION:
        return {"status": "LEGACY_REVIEW_POLICY_MISSING", "admitted_candidate_hashes": []}
    reviewed = set(str(value) for value in policy_evidence.get("reviewed_candidate_hashes", []))
    admitted = []
    for candidate in candidate_manifest.get("candidates") or []:
        candidate_hash = _hash_json(candidate)
        if candidate.get("candidate_status") == "PASS" and candidate_hash in reviewed:
            admitted.append(candidate_hash)
    return {"status": "LEGACY_REVIEW_REQUIRED" if not admitted else "LEGACY_REVIEWED_REFERENCE_READY",
            "admitted_candidate_hashes": admitted}
