# -*- coding: utf-8 -*-
"""Deterministic Translation V2 input preparation and audit.

Pre-clean never overwrites source evidence.  It emits a derived translation
input view plus auditable quality reports, and it never constructs a provider.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .dictionary_only import load_records
from .dictionary_service import DictionaryService, is_identity_attribute, normalize_key


CLEAN_SCHEMA_VERSION = "preclean-v1"
FIELD_ALIASES = {
    "title_es_raw": ("title_es_raw", "title_es", "title"),
    "selected_variation_raw": ("selected_variation_raw", "selected_variant_es", "variation_es"),
    "category_l1": ("category_l1", "category_l1_es"),
    "category_l2": ("category_l2", "category_l2_es"),
    "category_l3": ("category_l3", "category_l3_es"),
    "leaf_category": ("leaf_category", "leaf_category_es"),
    "specification_es": ("specification_es", "spec_v2"),
    "product_details": ("attributes", "product_details_es", "detail_attributes_raw"),
    "feature_bullets": ("feature_bullets_raw", "feature_bullets_es", "features_es"),
    "product_description": ("product_description_raw", "description_es", "product_description_es"),
    "brand": ("brand", "brand_es"),
}
AUDIT_FIELDS = tuple(FIELD_ALIASES)
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
UI_RE = re.compile(r"(?:…|\.\.\.)?\s*(?:Ver\s+(?:m[aá]s|menos)|查看更多|展开|收起)\b", re.I)
PROTECTED_TOKEN_RE = re.compile(
    r"(?<!\w)(?:USB[- ]?[A-Z]|PD\s*\d+(?:\.\d+)?|QC\s*\d+(?:\.\d+)?|"
    r"IP\w+|E\d{2}|A\d|M\d+|\d+(?:[.,]\d+)?\s*(?:mAh|Ah|PSI|bar|V|W|kW|Hz|MHz|GHz|°C|%)|"
    r"\d+(?:[-/]\d+)+)(?!\w)", re.I)
NUMERIC_UNIT_RE = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>mAh|Ah|MHz|GHz|kHz|Hz|kW|W|V|PSI|bar|°C|kg|mg|ml|cl|L|g|km|cm|mm|m|A|%)\b",
    re.I,
)
SPANISH_WORDS = {"para", "del", "de", "con", "sin", "producto", "color", "tamaño", "modelo", "material", "peso", "número"}
ENGLISH_WORDS = {"the", "for", "with", "without", "product", "color", "size", "model", "material", "weight", "number"}
GERMAN_WORDS = {"und", "der", "die", "das", "gewicht", "farbe", "größe", "modell"}
FRENCH_WORDS = {"pour", "avec", "sans", "produit", "couleur", "taille", "modèle", "poids"}
ITALIAN_WORDS = {"per", "con", "senza", "prodotto", "colore", "taglia", "modello", "peso"}


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


def _source_value(record: Mapping[str, Any], field: str) -> Any:
    for alias in FIELD_ALIASES[field]:
        value = record.get(alias)
        if value not in (None, "", [], {}):
            return value
    return ""


def _collapse_repeated(text: str) -> tuple[str, bool]:
    stripped = text.strip()
    if len(stripped) < 4 or len(stripped) % 2:
        return stripped, False
    half = len(stripped) // 2
    left, right = stripped[:half].strip(), stripped[half:].strip()
    if left and left == right:
        return left, True
    return stripped, False


def clean_text(value: Any, *, field: str = "") -> dict[str, Any]:
    """Clean UI/control noise while retaining source and an action ledger."""
    source = _text(value)
    if not source.strip():
        return {"source_text": source, "clean_text": "", "clean_status": "SOURCE_MISSING",
                "actions": [], "issues": [], "translate_allowed": False,
                "removed_ui_artifacts": 0, "removed_zero_width": 0,
                "html_detected": False}
    text = html.unescape(source)
    actions: list[str] = []
    issues: list[str] = []
    html_detected = bool(HTML_TAG_RE.search(text))
    if html_detected:
        text = HTML_TAG_RE.sub(" ", text)
        actions.append("REMOVE_HTML_TAGS")
    ui_hits = len(UI_RE.findall(text))
    if ui_hits:
        text = UI_RE.sub(" ", text)
        actions.append("REMOVE_UI_ARTIFACT")
    zero_hits = len(ZERO_WIDTH_RE.findall(text))
    if zero_hits:
        text = ZERO_WIDTH_RE.sub("", text)
        actions.append("REMOVE_ZERO_WIDTH")
    if CONTROL_RE.search(text):
        text = CONTROL_RE.sub("", text)
        actions.append("REMOVE_CONTROL_CHARS")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    collapsed, repeated = _collapse_repeated(text)
    if repeated:
        text = collapsed
        actions.append("DEDUPLICATE_CONTIGUOUS_REPEAT")
    if not text:
        issues.append("EMPTY_AFTER_CLEAN")
    status = "NORMALIZED" if actions or text != source else "CLEAN"
    return {"source_text": source, "clean_text": text, "clean_status": status,
            "actions": actions, "issues": issues, "translate_allowed": not issues,
            "removed_ui_artifacts": ui_hits, "removed_zero_width": zero_hits,
            "html_detected": html_detected}


def parse_details(value: Any) -> dict[str, Any]:
    """Parse details without discarding malformed source rows."""
    source = _text(value)
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    if isinstance(value, Mapping):
        iterable = list(value.items())
    elif isinstance(value, list):
        iterable = []
        for item in value:
            if isinstance(item, Mapping):
                label = item.get("label_raw") or item.get("label") or item.get("name")
                raw_value = item.get("value_raw") or item.get("value")
                iterable.append((label, raw_value))
            else:
                iterable.append((None, item))
    else:
        iterable = []
        for line in source.splitlines():
            if not line.strip():
                continue
            if ":" not in line and "：" not in line:
                issues.append("VALUE_WITHOUT_LABEL")
                continue
            label, raw_value = re.split(r"[:：]", line, maxsplit=1)
            iterable.append((label, raw_value))
    for position, (label, raw_value) in enumerate(iterable):
        label_clean = clean_text(label, field="detail_label") if label is not None else None
        value_clean = clean_text(raw_value, field="detail_value") if raw_value is not None else None
        if label_clean is None or not label_clean["clean_text"]:
            issues.append("LABEL_MISSING")
            continue
        if value_clean is None or not value_clean["clean_text"]:
            issues.append("VALUE_MISSING")
            continue
        rows.append({"label": label_clean["clean_text"], "value": value_clean["clean_text"],
                     "position": position, "source": "preclean", "label_clean": label_clean,
                     "value_clean": value_clean})
    counts = Counter(normalize_key(row["label"]) for row in rows)
    duplicate_labels = sum(max(0, count - 1) for count in counts.values())
    if duplicate_labels:
        issues.append("DUPLICATE_LABEL")
    if not source.strip():
        status = "SOURCE_MISSING"
    elif not rows:
        status = "BLOCKED" if issues else "SUSPICIOUS"
    elif issues:
        status = "NEEDS_REVIEW"
    else:
        status = "CLEAN"
    return {"source_text": source, "rows": rows, "issues": sorted(set(issues)),
            "duplicate_labels": duplicate_labels, "status": status,
            "fully_structured": bool(rows) and not issues,
            "partially_structured": bool(rows) and bool(issues),
            "unstructured": bool(source.strip()) and not rows}


def parse_bullets(value: Any) -> dict[str, Any]:
    source = _text(value)
    raw_items = value if isinstance(value, list) else source.splitlines()
    items = []
    issues: list[str] = []
    seen = set()
    for item in raw_items:
        cleaned = clean_text(item, field="feature_bullet")
        text = cleaned["clean_text"]
        if not text:
            issues.append("EMPTY_BULLET")
            continue
        key = normalize_key(text)
        if key in seen:
            issues.append("DUPLICATE_BULLET")
        seen.add(key)
        if len(text) > 1000:
            issues.append("LONG_BULLET")
        items.append({"source_text": _text(item), "clean_text": text, "clean": cleaned})
    if not source.strip():
        status = "SOURCE_MISSING"
    elif issues:
        status = "NEEDS_REVIEW"
    else:
        status = "CLEAN"
    return {"source_text": source, "items": items, "issues": sorted(set(issues)), "status": status,
            "duplicate_bullets": sum(1 for issue in issues if issue == "DUPLICATE_BULLET"),
            "empty_bullets": sum(1 for issue in issues if issue == "EMPTY_BULLET")}


def language_label(value: str) -> str:
    words = set(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", value.casefold()))
    scores = {
        "Spanish": len(words & SPANISH_WORDS), "English": len(words & ENGLISH_WORDS),
        "German": len(words & GERMAN_WORDS), "French": len(words & FRENCH_WORDS),
        "Italian": len(words & ITALIAN_WORDS),
    }
    ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))
    if not words or ranked[0][1] == 0:
        return "Unknown"
    if len([score for _, score in ranked if score > 0]) > 1:
        return "Mixed"
    return ranked[0][0]


def numeric_profile(value: str) -> dict[str, Any]:
    matches = NUMERIC_UNIT_RE.findall(value)
    units = [match[1].lower() for match in matches]
    issues = []
    if re.search(r"\b\d+(?:[.,]\d+)?\s*mAhmAh\b", value, re.I):
        issues.append("DUPLICATE_UNIT")
    if len(units) != len(set(units)) and len(units) > 1:
        issues.append("MULTIPLE_UNITS")
    values = []
    for number, unit in matches:
        try:
            values.append({"numeric_value": float(number.replace(",", ".")), "unit_raw": unit,
                           "canonical_unit": unit.lower()})
        except ValueError:
            issues.append("INVALID_NUMBER")
    return {"values": values, "issues": sorted(set(issues))}


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[\wÀ-ÿ]{2,}", normalize_key(value)))


def _raw_hash(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def audit_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    service = DictionaryService()
    field_quality: dict[str, Counter] = {field: Counter() for field in AUDIT_FIELDS}
    language_quality: Counter[tuple[str, str]] = Counter()
    issue_counts: Counter[str] = Counter()
    cleanup_counts: Counter[str] = Counter()
    structure_issues: list[dict[str, Any]] = []
    cross_field_issues: list[dict[str, Any]] = []
    unit_issues: list[dict[str, Any]] = []
    identity_counts: Counter[str] = Counter()
    identity_rows: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    translation_input: list[dict[str, Any]] = []
    sku_quality: list[dict[str, Any]] = []
    asins: Counter[str] = Counter()
    missing_asin = 0
    for record in records:
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        if asin:
            asins[asin] += 1
        else:
            missing_asin += 1
        clean_fields: dict[str, dict[str, Any]] = {}
        raw_fields: dict[str, dict[str, Any]] = {}
        detail = parse_details(_source_value(record, "product_details"))
        bullets = parse_bullets(_source_value(record, "feature_bullets"))
        record_issues: list[str] = []
        record_identity: list[dict[str, Any]] = []
        for field in AUDIT_FIELDS:
            raw = _source_value(record, field)
            raw_fields[field] = {"source_hash": _raw_hash(raw), "source_present": bool(_text(raw).strip())}
            if field == "product_details":
                result = {"source_text": detail["source_text"], "clean_text": "\n".join(
                    f"{row['label']}: {row['value']}" for row in detail["rows"]),
                    "clean_status": detail["status"], "actions": [], "issues": detail["issues"],
                    "translate_allowed": detail["status"] in {"CLEAN", "NORMALIZED"}}
            elif field == "feature_bullets":
                result = {"source_text": bullets["source_text"], "clean_text": "\n".join(
                    item["clean_text"] for item in bullets["items"]),
                    "clean_status": bullets["status"], "actions": [], "issues": bullets["issues"],
                    "translate_allowed": bullets["status"] in {"CLEAN", "NORMALIZED"}}
            else:
                result = clean_text(raw, field=field)
            if field == "product_description" and re.search(
                    r"\b(?:ASIN|商品编号|clasificaci[oó]n|valoraci[oó]n|opiniones|reviews?)\b|\bB[0-9A-Z]{8,10}\b",
                    result.get("clean_text", ""), re.I):
                result["issues"].append("POSSIBLE_METADATA_IN_DESCRIPTION")
                result["clean_status"] = "NEEDS_REVIEW"
                result["translate_allowed"] = False
            result["language"] = language_label(result.get("clean_text", ""))
            result["numeric"] = numeric_profile(result.get("clean_text", ""))
            cleanup_counts["removed_ui_artifacts"] += int(result.get("removed_ui_artifacts", 0))
            cleanup_counts["removed_zero_width"] += int(result.get("removed_zero_width", 0))
            cleanup_counts["html_detected"] += int(bool(result.get("html_detected", False)))
            clean_fields[field] = result
            field_quality[field][result["clean_status"]] += 1
            language_quality[(field, result["language"])] += 1
            for issue in result.get("issues", []) + result.get("numeric", {}).get("issues", []):
                issue_counts[issue] += 1
                record_issues.append(issue)
                if issue in result.get("numeric", {}).get("issues", []):
                    unit_issues.append({"asin": asin, "field": field, "source_text": result["source_text"], "issue_code": issue})
        for row in detail["rows"]:
            label = row["label"]
            if is_identity_attribute(label):
                identity_counts[label] += 1
                identity = {"asin": asin, "label": label, "value": row["value"],
                            "value_type": "identity", "translate_allowed": False,
                            "resolution": "source_preserved"}
                identity_rows.append(identity)
                record_identity.append(identity)
        if detail["issues"]:
            for issue in detail["issues"]:
                structure_issues.append({"asin": asin, "field": "product_details", "issue_code": issue,
                                         "source_text": detail["source_text"]})
        if bullets["issues"]:
            for issue in bullets["issues"]:
                structure_issues.append({"asin": asin, "field": "feature_bullets", "issue_code": issue,
                                         "source_text": bullets["source_text"]})
        values_for_overlap = {field: clean_fields[field].get("clean_text", "") for field in
                              ("title_es_raw", "specification_es", "product_description", "product_details", "feature_bullets")}
        for left, right in (("title_es_raw", "specification_es"), ("specification_es", "product_description"),
                            ("product_details", "feature_bullets"), ("feature_bullets", "title_es_raw")):
            l_tokens, r_tokens = _tokens(values_for_overlap[left]), _tokens(values_for_overlap[right])
            if l_tokens and r_tokens:
                overlap = len(l_tokens & r_tokens) / max(1, min(len(l_tokens), len(r_tokens)))
                if values_for_overlap[left] == values_for_overlap[right] or overlap >= 0.85:
                    row = {"asin": asin, "field_a": left, "field_b": right,
                           "issue_code": "CROSS_FIELD_OVERLAP", "overlap_ratio": round(overlap, 6),
                           "source_a": values_for_overlap[left], "source_b": values_for_overlap[right]}
                    cross_field_issues.append(row)
                    issue_counts["CROSS_FIELD_OVERLAP"] += 1
                    record_issues.append("CROSS_FIELD_OVERLAP")
        brand = clean_fields["brand"]["clean_text"]
        category_values = [clean_fields[field]["clean_text"] for field in ("category_l1", "category_l2", "category_l3", "leaf_category")]
        misplaced = False
        if brand and (len(brand) > 200 or "http" in brand.casefold()):
            misplaced = True
        if any("http" in value.casefold() for value in category_values if value):
            misplaced = True
        if clean_fields["title_es_raw"]["clean_text"].strip().isdigit():
            misplaced = True
        if len(clean_fields["specification_es"]["clean_text"]) > 8000:
            misplaced = True
        if misplaced:
            record_issues.append("POSSIBLE_FIELD_MISPLACEMENT")
            issue_counts["POSSIBLE_FIELD_MISPLACEMENT"] += 1
        severe = {"POSSIBLE_FIELD_MISPLACEMENT", "CROSS_FIELD_OVERLAP", "DUPLICATE_UNIT", "MULTIPLE_UNITS"}
        status = "NEEDS_REVIEW" if any(issue in severe for issue in record_issues) else "CLEAN"
        if any(result["clean_status"] == "BLOCKED" for result in clean_fields.values()):
            status = "BLOCKED"
        elif any(result["clean_status"] == "NEEDS_REVIEW" for result in clean_fields.values()):
            status = "NEEDS_REVIEW"
        if not asin:
            status = "BLOCKED"
        if status != "CLEAN":
            for field, result in clean_fields.items():
                if result["clean_status"] not in {"CLEAN", "NORMALIZED", "SOURCE_MISSING"} or result.get("issues"):
                    review_queue.append({"asin": asin, "field": field, "source_text": result["source_text"],
                                         "clean_text": result["clean_text"], "clean_status": result["clean_status"],
                                         "issue_codes": ";".join(result.get("issues", [])),
                                         "severity": "P1" if result["clean_status"] == "BLOCKED" else "P2",
                                         "suggested_action": "人工复核后再进入翻译"})
        translation_input.append({"asin": asin, "clean_schema_version": CLEAN_SCHEMA_VERSION,
                                  "raw_fields": raw_fields, "fields": clean_fields,
                                  "details_structured": detail["rows"], "bullet_items": bullets["items"],
                                  "identity": record_identity,
                                  "record_status": status})
        sku_quality.append({"asin": asin, "status": status, "issue_codes": ";".join(sorted(set(record_issues))),
                            "translate_allowed": status in {"CLEAN", "NORMALIZED"}})
    summary = {
        "input_rows": len(records), "unique_asins": len(asins),
        "duplicate_asins": sum(max(0, count - 1) for count in asins.values()),
        "missing_asin": missing_asin, "clean_schema_version": CLEAN_SCHEMA_VERSION,
        "field_quality": {field: dict(counts) for field, counts in field_quality.items()},
        "issue_counts": dict(issue_counts),
        "status_counts": dict(Counter(row["status"] for row in sku_quality)),
        "structure": {
            "detail_total_skus": len(records),
            "fully_structured": sum(1 for row in translation_input if row["details_structured"] and row["fields"]["product_details"]["clean_status"] == "CLEAN"),
            "partially_structured": sum(1 for row in translation_input if row["fields"]["product_details"]["clean_status"] == "NEEDS_REVIEW"),
            "unstructured": sum(1 for row in translation_input if row["fields"]["product_details"]["clean_status"] in {"BLOCKED", "SUSPICIOUS"}),
            "duplicate_labels": sum(1 for issue in structure_issues if issue["issue_code"] == "DUPLICATE_LABEL"),
        },
        "identity_count": len(identity_rows), "cross_field_issue_count": len(cross_field_issues),
        "review_queue_count": len(review_queue),
        "cleanup": dict(cleanup_counts),
        "language_profile": [{"field": field, "language": language, "count": count}
                             for (field, language), count in sorted(language_quality.items())],
    }
    return {"summary": summary, "translation_input_records": translation_input,
            "field_quality": field_quality, "sku_quality": sku_quality,
            "cross_field_issues": cross_field_issues, "structure_issues": structure_issues,
            "language_profile": summary["language_profile"], "identity_rows": identity_rows,
            "unit_issues": unit_issues, "review_queue": review_queue}


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_reports(result: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = result["summary"]
    paths: dict[str, str] = {}
    def write_json(name: str, data: Any) -> None:
        path = out / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[name] = str(path)
    write_json("translation_input_records.json", {"clean_schema_version": CLEAN_SCHEMA_VERSION,
                                                  "records": result["translation_input_records"]})
    write_json("preclean_audit.json", summary)
    statuses = ["CLEAN", "NORMALIZED", "SOURCE_MISSING", "SUSPICIOUS", "NEEDS_REVIEW", "BLOCKED"]
    quality_rows = [{"field": field, **{status: counts.get(status, 0) for status in statuses}
                     } for field, counts in summary["field_quality"].items()]
    _write_csv(out / "field_quality.csv", quality_rows, ["field", *statuses])
    _write_csv(out / "sku_quality.csv", result["sku_quality"], ["asin", "status", "issue_codes", "translate_allowed"])
    _write_csv(out / "cross_field_issues.csv", result["cross_field_issues"], ["asin", "field_a", "field_b", "issue_code", "overlap_ratio", "source_a", "source_b"])
    _write_csv(out / "structure_issues.csv", result["structure_issues"], ["asin", "field", "issue_code", "source_text"])
    _write_csv(out / "language_profile.csv", result["language_profile"], ["field", "language", "count"])
    identity_summary = []
    for label, count in Counter(row["label"] for row in result["identity_rows"]).most_common():
        identity_summary.append({"label": label, "occurrences": count, "source_preserved": count,
                                 "translate_allowed": False})
    _write_csv(out / "identity_summary.csv", identity_summary, ["label", "occurrences", "source_preserved", "translate_allowed"])
    _write_csv(out / "unit_numeric_issues.csv", result["unit_issues"], ["asin", "field", "source_text", "issue_code"])
    _write_csv(out / "review_queue.csv", result["review_queue"], ["asin", "field", "source_text", "clean_text", "clean_status", "issue_codes", "severity", "suggested_action"])
    for name in ("field_quality.csv", "sku_quality.csv", "cross_field_issues.csv", "structure_issues.csv", "language_profile.csv", "identity_summary.csv", "unit_numeric_issues.csv", "review_queue.csv"):
        paths[name] = str(out / name)
    md = ["# Translation V2 Pre-Clean Audit", "", f"- Input rows: {summary['input_rows']}",
          f"- Unique ASIN: {summary['unique_asins']}", f"- Duplicate ASIN rows: {summary['duplicate_asins']}",
          f"- Missing ASIN rows: {summary['missing_asin']}", f"- Schema: {CLEAN_SCHEMA_VERSION}", "",
          "## SKU status", ""]
    for name, count in summary["status_counts"].items():
        md.append(f"- {name}: {count}")
    md += ["", "## Field quality", "", "| Field | CLEAN | NORMALIZED | SOURCE_MISSING | SUSPICIOUS | NEEDS_REVIEW | BLOCKED |", "|---|---:|---:|---:|---:|---:|---:|"]
    for field, counts in summary["field_quality"].items():
        md.append("| %s | %d | %d | %d | %d | %d | %d |" % (field, counts.get("CLEAN", 0), counts.get("NORMALIZED", 0), counts.get("SOURCE_MISSING", 0), counts.get("SUSPICIOUS", 0), counts.get("NEEDS_REVIEW", 0), counts.get("BLOCKED", 0)))
    md += ["", "## Main issue counts", ""]
    for issue, count in sorted(summary["issue_counts"].items(), key=lambda row: (-row[1], row[0])):
        md.append(f"- {issue}: {count}")
    md += ["", "## Structure", "", json.dumps(summary["structure"], ensure_ascii=False, indent=2),
           "", "## Identity", "", f"- Identity values preserved: {summary['identity_count']}",
           "", "## Translation gate", "", "Only CLEAN and NORMALIZED records/fields have translate_allowed=true.",
           "SUSPICIOUS, NEEDS_REVIEW and BLOCKED remain in review_queue.csv.", ""]
    (out / "preclean_audit.md").write_text("\n".join(md), encoding="utf-8")
    paths["preclean_audit.md"] = str(out / "preclean_audit.md")
    paths["preclean_audit.json"] = str(out / "preclean_audit.json")
    paths["translation_input_records.json"] = str(out / "translation_input_records.json")
    return paths


def run_preclean(products: str | Path) -> dict[str, Any]:
    return audit_records(load_records(products))
