# -*- coding: utf-8 -*-
"""Offline dictionary profiling and dictionary-only dry-run.

The module accepts the frozen internal-research CSV, a normal list of product
records, or the ``sku_list_7365_internal_research.json`` wrapper.  It never
constructs a provider and never calls a translation API.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ..normalization.specification import translate_spec_es_to_zh
from .dictionary_service import DictionaryService, is_identity_attribute, normalize_key, resolve_exact


CSV_FIELDS = {
    "ASIN": "asin", "商品名称（西语）": "title_es_raw", "品牌": "brand",
    "一级类目": "category_l1", "二级类目": "category_l2",
    "三级类目": "category_l3", "细分类目": "leaf_category",
    "当前选中规格 / 变体（西语）": "selected_variant_es",
    "核心规格（西语）": "specification_es",
    "完整商品详情（西语原文）": "product_details_es",
    "商品卖点（西语原文）": "feature_bullets_es",
}

VARIATION_SOURCE_ALIASES = ("selected_variation_raw", "selected_variant_es", "variation_es")

SCALAR_FIELDS = (
    "title_es_raw", "brand", "category_l1", "category_l2", "category_l3",
    "leaf_category", "selected_variation_raw", "specification_es",
    "description_es",
)
DETAIL_FIELDS = ("product_details_es", "attributes")
BULLET_FIELDS = ("feature_bullets_es", "feature_bullets_raw")
TARGETS = {
    "title_es_raw": "title_zh", "brand": "brand_zh",
    "category_l1": "category_l1_zh", "category_l2": "category_l2_zh",
    "category_l3": "category_l3_zh", "leaf_category": "leaf_category_zh",
    "selected_variation_raw": "selected_variation_zh", "specification_es": "specification_zh",
    "description_es": "description_zh", "product_details_es": "product_details_zh",
    "feature_bullets_es": "feature_bullets_zh",
}


def _is_category_field(field: str) -> bool:
    return field.startswith("category_") or field == "leaf_category"


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load and normalize the supported offline dataset shapes."""
    path = Path(path)
    if path.suffix.casefold() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [_normalize_record(row) for row in csv.DictReader(handle)]
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, Mapping) and isinstance(data.get("records"), list):
        data = data["records"]
    if not isinstance(data, list):
        raise ValueError(f"offline dataset must be a list or records wrapper: {path}")
    return [_normalize_record(row) for row in data if isinstance(row, Mapping)]


def _normalize_record(row: Mapping[str, Any]) -> dict[str, Any]:
    if "asin" in row:
        out = dict(row)
    else:
        out = {target: row.get(source, "") for source, target in CSV_FIELDS.items()}
        out.setdefault("description_es", row.get("商品描述（西语原文）", ""))
    out["asin"] = str(out.get("asin") or out.get("ASIN") or "").strip().upper()
    if not out.get("selected_variation_raw"):
        for alias in VARIATION_SOURCE_ALIASES:
            if out.get(alias):
                out["selected_variation_raw"] = out[alias]
                break
    return out


def _source_value(record: Mapping[str, Any], field: str) -> Any:
    if field == "selected_variation_raw":
        for alias in VARIATION_SOURCE_ALIASES:
            value = record.get(alias)
            if value not in (None, ""):
                return value
        return ""
    return record.get(field)


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x).strip() for x in value if str(x).strip())
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "").strip()


def detail_items(record: Mapping[str, Any]) -> list[tuple[str, str]]:
    raw = record.get("product_details_es") or record.get("attributes") or ""
    if isinstance(raw, Mapping):
        return [(str(k).strip(), str(v).strip()) for k, v in raw.items()
                if str(k).strip() and str(v).strip()]
    if isinstance(raw, list):
        rows = []
        for item in raw:
            if isinstance(item, Mapping):
                label = item.get("label_raw") or item.get("label") or item.get("name")
                value = item.get("value_raw") or item.get("value")
                if label is not None and value is not None and str(value).strip():
                    rows.append((str(label).strip(), str(value).strip()))
            elif ":" in str(item):
                label, value = str(item).split(":", 1)
                if label.strip() and value.strip():
                    rows.append((label.strip(), value.strip()))
        return rows
    rows = []
    for line in str(raw).splitlines():
        if ":" not in line and "：" not in line:
            continue
        label, value = re.split(r"[:：]", line, maxsplit=1)
        if label.strip() and value.strip():
            rows.append((label.strip(), value.strip()))
    return rows


def bullet_items(record: Mapping[str, Any]) -> list[str]:
    raw = record.get("feature_bullets_es") or record.get("feature_bullets_raw") or ""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def _length_stats(values: list[str]) -> dict[str, Any]:
    nums = [len(x) for x in values]
    if not nums:
        return {"nonempty": 0, "empty": 0, "unique": 0, "duplicate_rate": 0.0,
                "average_length": 0.0, "p50_length": 0, "p95_length": 0, "max_length": 0}
    ordered = sorted(nums)
    def percentile(p: float) -> int:
        return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * p)))]
    unique = len(set(values))
    return {"nonempty": len(values), "empty": 0, "unique": unique,
            "duplicate_rate": round(1 - unique / len(values), 6),
            "average_length": round(statistics.mean(nums), 2),
            "p50_length": percentile(.50), "p95_length": percentile(.95),
            "max_length": max(nums)}


def profile_records(records: list[dict[str, Any]], *, top_n: int = 100) -> dict[str, Any]:
    """Build field profiles and candidate values from immutable source rows."""
    field_values: dict[str, list[str]] = defaultdict(list)
    value_occurrences: dict[tuple[str, str], dict[str, Any]] = {}
    field_names = list(SCALAR_FIELDS) + [
        "product_details_es", "feature_bullets_es",
        "product_details_label", "product_details_value", "feature_bullet",
    ]
    for record in records:
        asin = record.get("asin", "")
        for field in SCALAR_FIELDS:
            value = _text(_source_value(record, field))
            if value:
                field_values[field].append(value)
                value_occurrences.setdefault((field, value), {"frequency": 0, "asins": [], "contexts": []})
                hit = value_occurrences[(field, value)]
                hit["frequency"] += 1
                if asin and len(hit["asins"]) < 5 and asin not in hit["asins"]:
                    hit["asins"].append(asin)
        # Keep aggregate raw fields in the profile while collecting parsed
        # detail/bullet candidates separately.  Long aggregate values are
        # source evidence, not dictionary candidates.
        for field, raw in (
            ("product_details_es", record.get("product_details_es") or record.get("attributes")),
            ("feature_bullets_es", record.get("feature_bullets_es") or record.get("feature_bullets_raw")),
        ):
            value = _text(raw)
            if value:
                field_values[field].append(value)
        for label, value in detail_items(record):
            for field, text in (("product_details_label", label), ("product_details_value", value)):
                field_values[field].append(text)
                hit = value_occurrences.setdefault((field, text), {"frequency": 0, "asins": [], "contexts": []})
                hit["frequency"] += 1
                if asin and len(hit["asins"]) < 5 and asin not in hit["asins"]:
                    hit["asins"].append(asin)
                if len(hit["contexts"]) < 3:
                    hit["contexts"].append(f"{label}: {value}" if field.endswith("value") else label)
        for bullet in bullet_items(record):
            field_values["feature_bullet"].append(bullet)
            hit = value_occurrences.setdefault(("feature_bullet", bullet), {"frequency": 0, "asins": [], "contexts": []})
            hit["frequency"] += 1
            if asin and len(hit["asins"]) < 5 and asin not in hit["asins"]:
                hit["asins"].append(asin)
    profiles = {}
    for field in field_names:
        values = field_values.get(field, [])
        stats = _length_stats(values)
        stats["empty"] = len(records) - len(values) if field in SCALAR_FIELDS or field in {
            "product_details_es", "feature_bullets_es"
        } else 0
        profiles[field] = stats
    top_values = {}
    for field in field_names:
        rows = []
        for (candidate_field, value), meta in value_occurrences.items():
            if candidate_field != field:
                continue
            rows.append({"source_text": value, "frequency": meta["frequency"],
                         "sample_asins": list(meta["asins"]),
                         "sample_context": list(meta["contexts"])})
        top_values[field] = sorted(rows, key=lambda x: (-x["frequency"], x["source_text"]))[:top_n]
    return {"record_count": len(records), "unique_asins": len({r.get('asin') for r in records if r.get('asin')}),
            "profiles": profiles, "top_values": top_values,
            "occurrences": value_occurrences, "top_n": top_n}


def _classify_candidate(service: DictionaryService, *, field: str, value: str) -> str:
    if _is_category_field(field):
        return "category"
    if field == "product_details_label":
        return "attribute_label"
    if service.lookup_normalized(value, "booleans") is not None:
        return "boolean"
    if service.lookup_normalized(value, "materials") is not None:
        return "material"
    if service.lookup_normalized(value, "colors") is not None:
        return "color"
    if service.lookup_normalized(value, "packaging") is not None:
        return "packaging"
    if service.is_protected(value):
        return "protected_token"
    if re.search(r"(?:\d+(?:[.,]\d+)?\s*)(?:g|kg|ml|l|cm|mm|m|v|w|psi|bar|ghz|mhz|mah|ah)\b", value, re.I):
        return "unit"
    return "unknown"


def _resolve_detail_value(service: DictionaryService, label: str, value: str) -> dict[str, str]:
    normalized_label = normalize_key(label)
    if is_identity_attribute(label):
        return {"source_text": value, "resolved_text": value, "status": "resolved",
                "resolution_source": "source_preserved"}
    exact = resolve_exact(service, value, kind="value", field=normalized_label)
    if exact["status"] == "resolved":
        return exact
    # Numeric + unit normalization is a rule, not a free-form translation.
    if re.search(r"\d+(?:[.,]\d+)?\s*(?:g|kg|ml|l|cm|mm|m|v|w|psi|bar|ghz|mhz|mah|ah)\b", value, re.I):
        normalized = re.sub(r"(?<=\d),(?=\d)", ".", value)
        normalized = re.sub(r"(?i)\b(gramos?|g)\b", "g", normalized)
        normalized = re.sub(r"(?i)\b(kilogramos?|kg)\b", "kg", normalized)
        normalized = re.sub(r"(?i)\b(mililitros?|ml)\b", "ml", normalized)
        normalized = re.sub(r"(?i)\b(litros?|l)\b", "L", normalized)
        normalized = re.sub(r"(?i)\b(cent[ií]metros?|cm)\b", "cm", normalized)
        normalized = re.sub(r"(?i)\b(mil[ií]metros?|mm)\b", "mm", normalized)
        normalized = re.sub(r"(?i)\b(voltios?|v)\b", "V", normalized)
        normalized = re.sub(r"(?i)\b(vatios?|w)\b", "W", normalized)
        return {"source_text": value, "resolved_text": normalized, "status": "resolved",
                "resolution_source": "rule"}
    return {"source_text": value, "resolved_text": value, "status": "unresolved",
            "resolution_source": "unresolved"}


def _resolve_field(service: DictionaryService, record: Mapping[str, Any], field: str) -> dict[str, Any]:
    source = _text(_source_value(record, field))
    target = TARGETS.get(field, field)
    if not source:
        return {"source_text": "", "resolved_text": "", "status": "source_missing",
                "resolution_source": "source_missing", "target_field": target, "items": []}
    if _is_category_field(field):
        row = resolve_exact(service, source, kind="category")
        return {**row, "target_field": target, "items": []}
    if field == "brand":
        return {"source_text": source, "resolved_text": source, "status": "resolved",
                "resolution_source": "source_preserved", "target_field": target, "items": []}
    if field == "selected_variation_raw":
        row = resolve_exact(service, source, kind="packaging", field=field)
        if row["status"] == "unresolved" and service.is_protected(source):
            row = {"source_text": source, "resolved_text": source, "status": "resolved",
                   "resolution_source": "protected"}
        return {**row, "target_field": target, "items": []}
    if field == "specification_es":
        translated = translate_spec_es_to_zh(source)
        if translated and translated != source:
            return {"source_text": source, "resolved_text": translated, "status": "resolved",
                    "resolution_source": "rule", "target_field": target, "items": []}
        return {"source_text": source, "resolved_text": source, "status": "unresolved",
                "resolution_source": "unresolved", "target_field": target, "items": []}
    if field in DETAIL_FIELDS:
        items = []
        for label, value in detail_items(record):
            label_row = resolve_exact(service, label, kind="attribute_label")
            value_row = _resolve_detail_value(service, label, value)
            item_status = "resolved" if label_row["status"] == "resolved" and value_row["status"] == "resolved" else "unresolved"
            items.append({"label": label, "label_zh": label_row["resolved_text"],
                          "value": value, "value_zh": value_row["resolved_text"],
                          "status": item_status,
                          "label_status": label_row["status"],
                          "label_resolution_source": label_row["resolution_source"],
                          "value_status": value_row["status"],
                          "value_resolution_source": value_row["resolution_source"],
                          "resolution_source": "+".join(sorted({label_row["resolution_source"], value_row["resolution_source"]}))})
        rendered = "\n".join(f"{x['label_zh']}：{x['value_zh']}" for x in items)
        resolved = bool(items) and all(x["status"] == "resolved" for x in items)
        return {"source_text": source, "resolved_text": rendered or source,
                "status": "resolved" if resolved else "unresolved",
                "resolution_source": "dictionary+rule" if resolved else "unresolved",
                "target_field": target, "items": items}
    if field in BULLET_FIELDS:
        items = [{"source_text": x, "resolved_text": x, "status": "unresolved",
                  "resolution_source": "unresolved"} for x in bullet_items(record)]
        return {"source_text": source, "resolved_text": source, "status": "unresolved",
                "resolution_source": "unresolved", "target_field": target, "items": items}
    return {"source_text": source, "resolved_text": source, "status": "unresolved",
            "resolution_source": "unresolved", "target_field": target, "items": []}


def _resolution_bucket(origin: str) -> str:
    if "source_preserved" in origin:
        return "source_preserved"
    if "protected" in origin:
        return "protected"
    if "rule" in origin:
        return "rule"
    if "dictionary" in origin:
        return "dictionary"
    return "unresolved"


def run_dictionary_only(records: list[dict[str, Any]], *, service: Optional[DictionaryService] = None,
                        top_n: int = 100) -> dict[str, Any]:
    service = service or DictionaryService()
    profile = profile_records(records, top_n=top_n)
    fields = list(TARGETS)
    auxiliary_fields = ("product_details_label", "product_details_value", "feature_bullet")
    field_counts = {field: Counter() for field in (*fields, *auxiliary_fields)}
    identity_counts: Counter[str] = Counter()
    identity_preserved: Counter[str] = Counter()
    outputs = {}
    asin_counts: Counter[str] = Counter()
    missing_asins = 0
    for record in records:
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        if asin:
            asin_counts[asin] += 1
        else:
            missing_asins += 1
    for record in records:
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        if not asin:
            continue
        out_fields = {}
        for field in fields:
            result = _resolve_field(service, record, field)
            out_fields[result["target_field"]] = result
            units = result["items"] or ([result] if result["status"] != "source_missing" else [])
            if not units:
                field_counts[field]["source_missing"] += 1
                if field == "product_details_es":
                    field_counts["product_details_label"]["source_missing"] += 1
                    field_counts["product_details_value"]["source_missing"] += 1
                elif field == "feature_bullets_es":
                    field_counts["feature_bullet"]["source_missing"] += 1
            else:
                for unit in units:
                    source = unit.get("value") if "value" in unit else unit.get("source_text", "")
                    origin = unit.get("resolution_source", "unresolved")
                    bucket = _resolution_bucket(origin)
                    field_counts[field][bucket] += 1
                    if field == "product_details_es":
                        label = str(unit.get("label") or "").strip()
                        if label:
                            field_counts["product_details_label"][_resolution_bucket(
                                unit.get("label_resolution_source", "unresolved"))] += 1
                            field_counts["product_details_value"][_resolution_bucket(
                                unit.get("value_resolution_source", "unresolved"))] += 1
                            if is_identity_attribute(label):
                                identity_counts[label] += 1
                                if unit.get("value_resolution_source") == "source_preserved":
                                    identity_preserved[label] += 1
                    elif field == "feature_bullets_es":
                        field_counts["feature_bullet"][bucket] += 1
        outputs[asin] = {"asin": asin, "fields": out_fields, "resolution_status": "dictionary_only"}
    primary_counts = [field_counts[field] for field in fields]
    observed_units = sum(sum(c.values()) for c in primary_counts)
    source_missing_units = sum(c["source_missing"] for c in primary_counts)
    total_units = observed_units - source_missing_units
    category_stats = {}
    for field in ("category_l1", "category_l2", "category_l3", "leaf_category"):
        values = [(value, meta["frequency"])
                  for (candidate_field, value), meta in profile["occurrences"].items()
                  if candidate_field == field]
        hit = sum(freq for value, freq in values if service.lookup_category(value) is not None)
        category_stats[field] = {"unique_values": len(values), "dictionary_hit": hit,
                                 "unresolved": sum(freq for _, freq in values) - hit,
                                 "coverage_rate": round(hit / sum(freq for _, freq in values), 6)
                                 if values else 0.0}
    label_values = [(value, meta["frequency"])
                    for (candidate_field, value), meta in profile["occurrences"].items()
                    if candidate_field == "product_details_label"]
    unresolved_labels = sorted(
        ({"label": value, "frequency": freq} for value, freq in label_values
         if service.lookup_attribute_label(value) is None),
        key=lambda row: (-row["frequency"], row["label"]))
    attribute_label_stats = {
        "unique_labels": len(label_values),
        "resolved_labels": len(label_values) - len(unresolved_labels),
        "unresolved_labels": len(unresolved_labels),
        "top_unresolved": unresolved_labels[:30],
    }
    summary = {"input_rows": len(records), "total_skus": len(outputs),
               "unique_asins": len(asin_counts),
               "duplicate_asins": sum(max(0, count - 1) for count in asin_counts.values()),
               "missing_asin": missing_asins,
               "dictionary_entries": service.dictionary_counts(),
               "fields": {f: dict(c) for f, c in field_counts.items()},
               "category_stats": category_stats,
               "attribute_label_stats": attribute_label_stats,
               "identity_attributes": [
                   {"label": label, "occurrences": identity_counts[label],
                    "source_preserved": identity_preserved[label],
                    "unresolved": identity_counts[label] - identity_preserved[label]}
                   for label in sorted(identity_counts,
                                       key=lambda value: (-identity_counts[value], value))
               ],
               "observed_units": observed_units,
               "source_missing_units": source_missing_units,
               "total_units": total_units,
               "resolved_by_dictionary": sum(c["dictionary"] for c in primary_counts),
               "resolved_by_rules": sum(c["rule"] for c in primary_counts),
               "source_preserved": sum(c["source_preserved"] for c in primary_counts),
               "protected": sum(c["protected"] for c in primary_counts),
               "remaining_for_qwen": sum(c["unresolved"] for c in primary_counts),
               "qwen_api_calls": 0, "deepseek_api_calls": 0,
               "openai_api_calls": 0, "other_translation_api_calls": 0}
    summary["estimated_units_avoided"] = sum(
        c.get(key, 0)
        for c in primary_counts
        for key in ("dictionary", "rule", "source_preserved", "protected")
    )
    for field, counts in summary["fields"].items():
        nonmissing = sum(value for key, value in counts.items() if key != "source_missing")
        resolved = sum(counts.get(key, 0) for key in ("dictionary", "rule", "source_preserved", "protected"))
        counts["coverage_rate"] = round(resolved / nonmissing, 6) if nonmissing else 0.0
    return {"schema_version": "translation-v2-dictionary-only.3",
            "dictionary_directory": str(service.directory), "summary": summary,
            "profile": profile, "records": outputs}


def write_reports(result: dict[str, Any], out_dir: str | Path, *, service: Optional[DictionaryService] = None) -> dict[str, str]:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    profile = result["profile"]
    # ``occurrences`` is keyed by (field, value) internally for fast
    # aggregation; serialize it as rows because JSON object keys cannot be
    # tuples.
    serial_profile = dict(profile)
    serial_profile["occurrences"] = [
        {"field": field, "source_text": value, **meta}
        for (field, value), meta in profile["occurrences"].items()
    ]
    serial_result = dict(result)
    serial_result["profile"] = serial_profile
    _write_json(serial_result, out / "dictionary_only_results.json")
    _write_json({"record_count": profile["record_count"], "unique_asins": profile["unique_asins"],
                 "profiles": profile["profiles"], "top_values": profile["top_values"]},
                out / "field_profile.json")
    with (out / "field_profile.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["field", "nonempty", "empty", "unique", "duplicate_rate", "average_length", "p50_length", "p95_length", "max_length"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for field, data in profile["profiles"].items():
            writer.writerow({"field": field, **data})
    service = service or DictionaryService(result.get("dictionary_directory") or None)
    candidates = []
    unresolved = []
    for (field, value), meta in profile["occurrences"].items():
        candidate_type = _classify_candidate(service, field=field, value=value)
        row = {"source_text": value, "normalized_source": normalize_key(value),
               "field_type": field, "frequency": meta["frequency"],
               "sample_asins": ",".join(meta["asins"]), "sample_context": " | ".join(meta["contexts"]),
               "candidate_type": candidate_type}
        candidates.append(row)
        if candidate_type == "unknown":
            unresolved.append(row)
    candidates.sort(key=lambda x: (-x["frequency"], x["field_type"], x["source_text"]))
    unresolved.sort(key=lambda x: (-x["frequency"], x["field_type"], x["source_text"]))
    _write_csv(candidates, out / "dictionary_candidates.csv")
    _write_csv(unresolved, out / "unresolved_high_frequency.csv")
    category_review = [x for x in candidates if x["candidate_type"] == "category" and not service.lookup_category(x["source_text"])]
    _write_csv(category_review, out / "category_review_candidates.csv")
    coverage = result["summary"] | {"profile_record_count": profile["record_count"], "profile_unique_asins": profile["unique_asins"]}
    _write_json(coverage, out / "dictionary_coverage.json")
    lines = ["# Translation V2 dictionary-only coverage", "", f"- Input rows: {coverage['input_rows']}",
             f"- SKU outputs: {coverage['total_skus']}", f"- Unique ASIN: {coverage['unique_asins']}",
             f"- Duplicate ASIN rows: {coverage['duplicate_asins']}",
             f"- Missing ASIN rows: {coverage['missing_asin']}",
             "- Mode: dictionary-only", "- Qwen API calls: 0", "- DeepSeek API calls: 0",
             "- OpenAI API calls: 0", "- Other translation API calls: 0", "",
             "## Dictionary entries", ""]
    for name, count in coverage["dictionary_entries"].items(): lines.append(f"- {name}: {count}")
    lines += ["", "## Resolution coverage", "", "| Field | Dictionary | Rule | Source preserved | Protected | Unresolved | Coverage |", "|---|---:|---:|---:|---:|---:|---:|"]
    for field, counts in coverage["fields"].items():
        lines.append("| %s | %d | %d | %d | %d | %d | %.1f%% |" % (field, counts.get("dictionary", 0), counts.get("rule", 0), counts.get("source_preserved", 0), counts.get("protected", 0), counts.get("unresolved", 0), 100 * counts.get("coverage_rate", 0)))
    lines += ["", "## Category coverage", "", "| Field | Unique values | Dictionary hit | Unresolved | Coverage |", "|---|---:|---:|---:|---:|"]
    for field, stats in coverage["category_stats"].items():
        lines.append("| %s | %d | %d | %d | %.1f%% |" % (
            field, stats["unique_values"], stats["dictionary_hit"],
            stats["unresolved"], 100 * stats["coverage_rate"]))
    lines += ["", "## Identity attributes", "", "| Label | Occurrences | Source preserved | Unresolved |", "|---|---:|---:|---:|"]
    for row in coverage["identity_attributes"]:
        lines.append("| %s | %d | %d | %d |" % (
            row["label"], row["occurrences"], row["source_preserved"], row["unresolved"]))
    label_stats = coverage["attribute_label_stats"]
    lines += ["", "## Attribute label coverage", "",
              f"- Unique labels: {label_stats['unique_labels']}",
              f"- Resolved labels: {label_stats['resolved_labels']}",
              f"- Unresolved labels: {label_stats['unresolved_labels']}",
              "", "### TOP 30 unresolved attribute labels", ""]
    for row in label_stats["top_unresolved"]:
        lines.append(f"- {row['frequency']} × `{row['label']}`")
    lines += ["", "## Top unresolved values", ""]
    for row in unresolved[:30]: lines.append(f"- {row['frequency']} × `{row['source_text']}` ({row['field_type']})")
    (out / "dictionary_coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {name: str(out / name) for name in ["dictionary_only_results.json", "field_profile.json", "field_profile.csv", "dictionary_candidates.csv", "unresolved_high_frequency.csv", "category_review_candidates.csv", "dictionary_coverage.json", "dictionary_coverage.md"]}


def _write_json(value: Any, path: Path) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = ["source_text", "normalized_source", "field_type", "frequency", "sample_asins", "sample_context", "candidate_type"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
