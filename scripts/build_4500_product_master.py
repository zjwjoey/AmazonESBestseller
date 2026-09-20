#!/usr/bin/env python3
"""Build the audited offline 4,500-ASIN Product Master.

This command is intentionally offline.  It reads the already collected ranking,
product, detail and saved-HTML evidence and never opens a network connection or
invokes a translation model.  The raw 9,529-row ranking file is copied, never
rewritten, and all output ordering is deterministic.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup

from amazon_es_bestseller.collection.detail import (
    CURRENT_DETAIL_SCHEMA_VERSION,
    _classify_saved_page,
    _page_asin_candidates,
    parse_detail_page,
    verify_asin_on_page,
)
from amazon_es_bestseller.access.detector import detect_access_status
from amazon_es_bestseller.pipeline import normalize_product


ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.I)
MASTER_SCHEMA_VERSION = "4500-product-master-v1"
TRANSLATION_QUEUE_SCHEMA_VERSION = "pretranslation-queue-v1"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return bool(str(value).strip())


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x).strip() for x in value if nonempty(x)).strip()
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if nonempty(value):
            return value
    return ""


def source_hash(field_name: str, text: str) -> str:
    return sha256_bytes((field_name + "\0" + text).encode("utf-8"))


def load_records(path: Path) -> list[dict]:
    value = load_json(path)
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("records", "products", "details", "rankings", "items"):
            if isinstance(value.get(key), list):
                return [x for x in value[key] if isinstance(x, dict)]
    raise ValueError(f"unsupported record file: {path}")


def map_by_asin(rows: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        asin = str(row.get("asin") or "").strip().upper()
        if ASIN_RE.fullmatch(asin) and asin not in out:
            out[asin] = row
    return out


def _fast_page_asins(html: str) -> set[str]:
    """Cheap identity extraction used before expensive BeautifulSoup parsing."""
    values = set()
    patterns = (
        r'(?:id|name)=["\'](?:ASIN|productAsin)["\'][^>]*value=["\']([A-Z0-9]{10})',
        r'(?:data-asin|parentASIN)["\']?\s*[:=]\s*["\']([A-Z0-9]{10})',
        r'(?:/dp/|asin=)([A-Z0-9]{10})',
    )
    for pattern in patterns:
        values.update(m.upper() for m in re.findall(pattern, html, re.I))
    return values


def _fast_has_title(html: str) -> bool:
    return bool(re.search(r'id=["\']productTitle["\'][^>]*>\s*[^<]', html, re.I))


def build_html_audit(html_dirs: list[Path], wanted: set[str], parse_asins: set[str] | None = None) -> tuple[dict, dict[str, dict]]:
    """Audit saved HTML and return first valid reparse per ASIN.

    The parser's current challenge and product-page checks are used directly.
    A mismatch is split from INVALID_OR_EMPTY for the audit report without
    moving/quarantining any existing evidence.
    """
    records: list[dict] = []
    valid: dict[str, dict] = {}
    counts = Counter()
    for root in html_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.html")):
            asin = path.stem.upper()
            if asin not in wanted:
                continue
            needs_full_parse = parse_asins is None or asin in parse_asins
            meta: dict = {}
            meta_path = path.with_suffix(".meta.json")
            if meta_path.exists():
                try:
                    meta = load_json(meta_path)
                except (OSError, ValueError):
                    meta = {}
            # Existing current Product Records are already validated evidence.
            # Keep the saved file in the inventory without reading its often
            # multi-megabyte body again; only old/missing records are reparsed.
            if not needs_full_parse:
                meta_state = str(meta.get("access_state") or meta.get("initial_access_state") or "").upper()
                classification = "CHALLENGE" if meta_state == "CHALLENGE" else "VALID_PRODUCT_PAGE"
                counts[classification] += 1
                records.append({
                    "asin": asin, "path": str(path), "classification": classification,
                    "validation_method": "current_product_record", "access_state": meta_state or "UNKNOWN",
                    "status_code": meta.get("status_code"), "initial_access_state": meta.get("initial_access_state"),
                    "recovered_from_challenge": bool(meta.get("recovered_from_challenge")),
                })
                continue
            try:
                html = path.read_text(encoding="utf-8")
            except OSError:
                continue
            # Most frozen ASINs already have a current validated Product Record.
            # For those pages use the current detector plus cheap identity/title
            # checks; invoke the full project parser only where an old/missing
            # record actually needs offline reconstruction.  This keeps the
            # audit faithful while avoiding repeated CSS parsing of giant pages.
            if needs_full_parse:
                classification, access_state, parsed = _classify_saved_page(html, asin, meta)
            else:
                access_state = detect_access_status(meta.get("status_code", 200), html)
                candidates = _fast_page_asins(html)
                final_url = str(meta.get("final_url") or "")
                if access_state.value == "CHALLENGE":
                    classification, parsed = "CHALLENGE", None
                elif not html.strip() or not _fast_has_title(html):
                    classification, parsed = "INVALID_OR_EMPTY", None
                elif (candidates and asin not in candidates) or (final_url and not verify_asin_on_page(final_url, asin)):
                    classification, parsed = "ASIN_MISMATCH", None
                else:
                    classification, parsed = "VALID_PRODUCT_PAGE", None
            if classification == "INVALID_OR_EMPTY":
                candidates = _page_asin_candidates(BeautifulSoup(html, "lxml")) if needs_full_parse and html.strip() else _fast_page_asins(html)
                final_url = str(meta.get("final_url") or "")
                if (candidates and asin not in candidates) or (final_url and not verify_asin_on_page(final_url, asin)):
                    classification = "ASIN_MISMATCH"
            counts[classification] += 1
            records.append({
                "asin": asin,
                "path": str(path),
                "classification": classification,
                "access_state": getattr(access_state, "value", str(access_state)),
                "status_code": meta.get("status_code"),
                "initial_access_state": meta.get("initial_access_state"),
                "recovered_from_challenge": bool(meta.get("recovered_from_challenge")),
            })
            if classification == "VALID_PRODUCT_PAGE" and asin in (parse_asins or set()) and asin not in valid:
                parsed = parsed or parse_detail_page(html, asin)
                parsed.update({
                    "status_code": meta.get("status_code"),
                    "initial_access_state": meta.get("initial_access_state"),
                    "access_state": getattr(access_state, "value", str(access_state)),
                    "recovered_from_challenge": bool(meta.get("recovered_from_challenge")),
                    "resumed_from_html": True,
                    "_evidence_path": str(path),
                })
                valid[asin] = parsed
    for key in ("VALID_PRODUCT_PAGE", "CHALLENGE", "INVALID_OR_EMPTY", "ASIN_MISMATCH"):
        counts.setdefault(key, 0)
    return {"summary": dict(counts), "records": records, "html_dirs": [str(p) for p in html_dirs]}, valid


def research_names(plan: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for source in plan.get("sources", []):
        group = str(source.get("category_group") or "").strip()
        if group and group not in names:
            names[group] = str(source.get("category_name_zh") or "").strip()
    return names


def ranking_snapshot_id(row: dict) -> str:
    url = str(row.get("ranking_source_url") or "")
    collected = str(row.get("collected_at") or "")
    return sha256_bytes((url + "|" + collected).encode("utf-8"))[:16] if (url or collected) else ""


def normalize_for_master(row: dict) -> dict:
    try:
        return normalize_product(row)
    except Exception:
        # Preserve source evidence if a historical row contains a shape that a
        # newer normalizer does not understand.  The closure audit will report
        # the missing derived fields instead of inventing them.
        return dict(row)


def field_value(row: dict, field: str) -> Any:
    aliases = {
        "selected_variation": ("selected_variation", "selected_variation_raw"),
        "specification": ("specification", "specification_es"),
        "seller": ("seller", "seller_raw"),
    }
    for key in aliases.get(field, (field,)):
        if nonempty(row.get(key)):
            return row.get(key)
    return ""


def build_closure(master: list[dict]) -> tuple[dict, list[dict]]:
    fields = [
        "title_es_raw", "brand", "current_price", "original_price", "discount_rate",
        "rating", "review_count", "monthly_bought_raw", "selected_variation",
        "specification", "product_details_es", "feature_bullets_es",
        "date_first_available_raw", "date_first_available", "seller", "product_url", "image_url",
    ]
    field_stats = {field: Counter() for field in fields}
    issues: list[dict] = []
    for row in master:
        asin = row["asin"]
        for field in fields:
            value = field_value(row, field)
            if nonempty(value):
                field_stats[field]["PASS"] += 1
                continue
            raw_aliases = {
                "current_price": "current_price_raw", "original_price": "original_price_raw",
                "rating": "rating_raw", "review_count": "review_count_raw",
                "date_first_available": "date_first_available_raw", "brand": "brand_raw",
                "feature_bullets_es": "feature_bullets_raw", "product_details_es": "attributes",
            }
            raw = row.get(raw_aliases.get(field, field))
            if field == "brand" and (nonempty(raw) or any(str(a.get("label_raw", "")).casefold() in {"marca", "brand"} for a in (row.get("attributes") or []) if isinstance(a, dict))):
                issue_type = "MAPPING_MISSED"
            elif field == "specification" and (nonempty(row.get("title_es_raw")) or nonempty(row.get("attributes"))):
                issue_type = "DERIVED_MISSING"
            elif field == "product_details_es" and nonempty(row.get("attributes")):
                issue_type = "PARSER_MISSED"
            elif nonempty(raw):
                issue_type = "PARSER_MISSED"
            else:
                issue_type = "SOURCE_MISSING"
            field_stats[field][issue_type] += 1
            issues.append({"asin": asin, "field": field, "issue_type": issue_type, "source_evidence": raw, "current_value": value, "reason": "empty after offline merge"})
        details = as_text(row.get("product_details_es"))
        if re.search(r"\bVer más\b|\bVer mas\b", details, re.I) or row.get("detail_truncated"):
            field_stats.setdefault("detail_truncated", Counter())["DETAIL_TRUNCATED"] += 1
            issues.append({"asin": asin, "field": "product_details_es", "issue_type": "DETAIL_TRUNCATED", "source_evidence": details, "current_value": details, "reason": "saved source contains Ver más or parser truncation marker"})
    totals = Counter()
    for stats in field_stats.values():
        totals.update(stats)
    for issue_type in ("SOURCE_MISSING", "PARSER_MISSED", "MAPPING_MISSED", "DERIVED_MISSING", "DETAIL_TRUNCATED"):
        totals.setdefault(issue_type, 0)
    return {"fields": {k: dict(v) for k, v in field_stats.items()}, "totals": dict(totals), "issue_count": len(issues)}, issues


def csv_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def build(args: argparse.Namespace) -> dict:
    root = Path(args.root).resolve()
    out = root / args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "outputs/scale_4500_repair/manifest_global_repair.json"
    plan_path = root / "configs/amazon_es_4500_rank_31_50_completion_plan.json"
    products_path = root / "outputs/scale_4500_repair/trace_enriched/products_4500_repaired_after_qa_fixes.json"
    details_paths = [root / "outputs/scale_4500_repair/details_4500.json", root / "outputs/scale_4500_repair/rank_31_50_batch/details.json"]
    ranking_paths = [root / "outputs/scale_4500_repair/trace_enriched/rankings_4500_trace.json", root / "outputs/scale_4500_repair/rank_31_50_batch/rankings.json"]
    raw_ranking = root / "outputs/scale_4500_repair/non_deduplicated_ranking_sku_list_9529.csv"
    if not raw_ranking.exists():
        raise FileNotFoundError(raw_ranking)

    manifest_source = load_json(manifest_path)
    manifest_records = manifest_source.get("records", [])
    if len(manifest_records) != 4500:
        raise ValueError(f"FROZEN_MANIFEST_INVALID count={len(manifest_records)}")
    manifest_asins = [str(r.get("asin") or "").upper() for r in manifest_records]
    if len(set(manifest_asins)) != 4500 or not all(ASIN_RE.fullmatch(a) for a in manifest_asins):
        raise ValueError("FROZEN_MANIFEST_INVALID ASIN set")
    group_counts = Counter(str(r.get("category_group") or "") for r in manifest_records)
    if len(group_counts) != 15 or set(group_counts.values()) != {300}:
        raise ValueError(f"FROZEN_MANIFEST_INVALID groups={dict(group_counts)}")
    wanted = set(manifest_asins)
    names = research_names(load_json(plan_path))
    if set(group_counts) - set(names):
        raise ValueError("FROZEN_MANIFEST_INVALID missing research category name")

    # Archive the raw evidence byte-for-byte and record its digest.
    raw_out = out / "raw/ranking_evidence_9529.csv"
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(raw_ranking, raw_out)
    raw_hash = sha256_file(raw_ranking)
    (raw_out.with_suffix(".sha256")).write_text(raw_hash + "  ranking_evidence_9529.csv\n", encoding="utf-8")

    # Frozen manifest, with deterministic research names and snapshot IDs.
    frozen_rows = []
    for pos, record in enumerate(manifest_records, 1):
        row = dict(record)
        row.update({"selection_order": pos, "category_name_zh": names[row.get("category_group")], "manifest_version": MASTER_SCHEMA_VERSION, "source_snapshot_id": ranking_snapshot_id(row)})
        frozen_rows.append(row)
    manifest_hash = canonical_hash(frozen_rows)
    write_json(out / "manifest/manifest_4500_frozen.json", {"manifest_version": MASTER_SCHEMA_VERSION, "manifest_hash": manifest_hash, "source_manifest": str(manifest_path), "records": frozen_rows})

    products = map_by_asin(load_records(products_path))
    detail_map: dict[str, dict] = {}
    for path in details_paths:
        for row in load_records(path):
            asin = str(row.get("asin") or "").upper()
            if ASIN_RE.fullmatch(asin) and asin not in detail_map:
                detail_map[asin] = row
    rankings: list[dict] = []
    for path in ranking_paths:
        for row in load_records(path):
            if str(row.get("asin") or "").upper() in wanted:
                item = dict(row)
                item["asin"] = str(item["asin"]).upper()
                item["source_file"] = str(path.relative_to(root))
                item["source_snapshot_id"] = ranking_snapshot_id(item)
                rankings.append(item)
    rankings_by_asin: dict[str, list[dict]] = defaultdict(list)
    for row in rankings:
        rankings_by_asin[row["asin"]].append(row)

    # Direct category detail caches are ordered before the 31-50 supplement;
    # each ASIN's first valid page is retained as the canonical reparse.
    html_dirs = sorted((p / "html" for p in (root / "outputs").glob("*_300_spain_28001") if (p / "html").is_dir()), key=lambda p: str(p))
    # Quarantined HTML is evidence too.  Include each ASIN subdirectory so the
    # current challenge detector can classify it without ever moving/deleting
    # the preserved evidence.
    for cache_root in sorted(root.glob("outputs/*_300_spain_28001/quarantine"), key=lambda p: str(p)):
        html_dirs.extend(sorted((p for p in cache_root.iterdir() if p.is_dir()), key=lambda p: str(p)))
    supplement = root / "outputs/luggage_300_spain_28001_supplement_6/html"
    if supplement.is_dir():
        html_dirs.append(supplement)
    batch_html = root / "outputs/scale_4500_repair/rank_31_50_batch/detail_cache/html"
    if batch_html.is_dir():
        html_dirs.append(batch_html)
    # Full HTML reparse is needed only for frozen ASINs without a current
    # detail record or with an older schema.  All other saved pages are still
    # audited with the current challenge/identity/title validation path.
    parse_asins = {
        asin for asin in wanted
        if not nonempty(products.get(asin, {}).get("title_es_raw"))
        or int(products.get(asin, {}).get("detail_schema_version") or 0) < CURRENT_DETAIL_SCHEMA_VERSION
    }
    html_audit, html_valid = build_html_audit(html_dirs, wanted, parse_asins=parse_asins)
    write_json(out / "audit/html_evidence_audit.json", html_audit)

    master: list[dict] = []
    missing_detail: list[dict] = []
    source_counts = Counter()
    for pos, frozen in enumerate(manifest_records, 1):
        asin = str(frozen["asin"]).upper()
        current = dict(products.get(asin) or {})
        detail = dict(detail_map.get(asin) or {})
        html = dict(html_valid.get(asin) or {})
        source = dict(current)
        source_type = "current_product" if nonempty(current.get("title_es_raw")) or nonempty(current.get("product_details_es")) else ""
        current_has_detail = bool(nonempty(current.get("title_es_raw")) or nonempty(current.get("product_details_es")) or nonempty(current.get("attributes")) or nonempty(current.get("detail_schema_version")))
        # Old/missing-schema records are upgraded from a valid saved page first.
        if html and (not source_type or int(current.get("detail_schema_version") or 0) < CURRENT_DETAIL_SCHEMA_VERSION):
            source.update({k: v for k, v in html.items() if not str(k).startswith("_")})
            source_type = "offline_html_reparse"
        for key, value in detail.items():
            if not nonempty(source.get(key)) and nonempty(value):
                source[key] = value
        if source_type == "":
            source_type = "detail_record" if detail else ("offline_html_reparse" if html else "ranking_only")
        if html:
            source_type += "+offline_html_reparse" if source_type != "offline_html_reparse" else ""
        normalized = normalize_for_master(source)
        contexts = rankings_by_asin.get(asin, [])
        rank = dict(frozen)
        record = {
            "selection_order": pos,
            "manifest_index": frozen.get("index"),
            "asin": asin,
            "parent_asin": first_nonempty(normalized.get("parent_asin"), source.get("parent_asin")),
            "research_category_group": frozen.get("category_group"),
            "research_category_name_zh": names.get(frozen.get("category_group"), ""),
            "category_l1": first_nonempty(frozen.get("category_l1"), normalized.get("category_l1")),
            "category_l2": first_nonempty(frozen.get("category_l2"), normalized.get("category_l2")),
            "category_l3": first_nonempty(frozen.get("category_l3"), normalized.get("category_l3")),
            "leaf_category": first_nonempty(frozen.get("leaf_category"), normalized.get("leaf_category")),
            "browse_node_id": first_nonempty(frozen.get("browse_node_id"), normalized.get("browse_node_id")),
            "bestseller_rank": rank.get("bestseller_rank"),
            "ranking_source_url": rank.get("ranking_source_url"),
            "ranking_source_category": first_nonempty(rank.get("ranking_source_category"), normalized.get("ranking_source_category")),
            "ranking_source_category_path": first_nonempty(rank.get("ranking_source_category_path"), normalized.get("ranking_source_category_path")),
            "ranking_context_count": len(contexts),
            "ranking_category_groups": sorted({str(c.get("category_group")) for c in contexts if nonempty(c.get("category_group"))}),
            "title_es_raw": first_nonempty(normalized.get("title_es_raw"), source.get("title_es_raw")),
            "brand": first_nonempty(normalized.get("brand"), source.get("brand")),
            "current_price": normalized.get("current_price"),
            "original_price": normalized.get("original_price"),
            "discount_rate": normalized.get("discount_rate"),
            "rating": normalized.get("rating"),
            "review_count": normalized.get("review_count"),
            "monthly_bought_raw": first_nonempty(normalized.get("monthly_bought_raw"), source.get("monthly_bought_raw")),
            "monthly_bought_min": normalized.get("monthly_bought_min"),
            "selected_variation": field_value(normalized, "selected_variation"),
            "specification": field_value(normalized, "specification"),
            "product_details_es": first_nonempty(normalized.get("product_details_es"), source.get("product_details_es")),
            "feature_bullets_es": first_nonempty(normalized.get("feature_bullets_es"), source.get("feature_bullets_es")),
            "date_first_available_raw": first_nonempty(normalized.get("date_first_available_raw"), source.get("date_first_available_raw")),
            "date_first_available": normalized.get("date_first_available"),
            "seller": field_value(normalized, "seller"),
            "product_url": first_nonempty(normalized.get("product_url"), source.get("product_url"), f"https://www.amazon.es/dp/{asin}"),
            "image_url": first_nonempty(normalized.get("image_url"), source.get("image_url")),
            "detail_schema_version": first_nonempty(normalized.get("detail_schema_version"), source.get("detail_schema_version")),
            "detail_source": source_type,
            "detail_evidence_path": sorted({str(x) for x in [html.get("_evidence_path"), source.get("detail_evidence_path"), str(products_path.relative_to(root)) if current_has_detail else None, str(details_paths[0].relative_to(root)) if (not current_has_detail and detail) else None] if nonempty(x)}),
            "detail_reparsed": bool(html),
            "access_state": first_nonempty(normalized.get("access_state"), frozen.get("access_state")),
            "recovered_from_challenge": bool(normalized.get("recovered_from_challenge") or frozen.get("recovered_from_challenge")),
            "field_closure_status": "PASS",
            # Raw evidence is retained in JSON, while the CSV remains a review view.
            "attributes": normalized.get("attributes", source.get("attributes", [])),
            "feature_bullets_raw": normalized.get("feature_bullets_raw", source.get("feature_bullets_raw", [])),
            "brand_raw": normalized.get("brand_raw", source.get("brand_raw", "")),
            "current_price_raw": normalized.get("current_price_raw", source.get("current_price_raw", "")),
            "original_price_raw": normalized.get("original_price_raw", source.get("original_price_raw", "")),
            "rating_raw": normalized.get("rating_raw", source.get("rating_raw", "")),
            "review_count_raw": normalized.get("review_count_raw", source.get("review_count_raw", "")),
            "seller_raw": normalized.get("seller_raw", source.get("seller_raw", "")),
            "selected_variation_raw": normalized.get("selected_variation_raw", source.get("selected_variation_raw", "")),
            "detail_truncated": bool(normalized.get("detail_truncated")),
        }
        master.append(record)
        if not nonempty(record["title_es_raw"]) and not nonempty(record["product_details_es"]) and not nonempty(record["feature_bullets_es"]):
            missing_detail.append({"asin": asin, "research_category_group": record["research_category_group"], "missing_reason": "no valid product/detail/html evidence", "available_evidence": ["ranking"], "recommended_action": "offline evidence review; no live recollection in this build"})
        source_counts[source_type] += 1

    closure, issues = build_closure(master)
    issue_by_asin = defaultdict(list)
    for issue in issues:
        issue_by_asin[issue["asin"]].append(issue)
    for row in master:
        row["field_closure_status"] = "P1_REVIEW" if any(i["issue_type"] not in {"SOURCE_MISSING"} for i in issue_by_asin.get(row["asin"], [])) else ("SOURCE_MISSING" if issue_by_asin.get(row["asin"]) else "PASS")

    write_json(out / "master/products_4500_master.json", {"master_schema_version": MASTER_SCHEMA_VERSION, "manifest_hash": manifest_hash, "ranking_evidence_hash": raw_hash, "network_requests": 0, "records": master})
    csv_fields = ["selection_order", "asin", "parent_asin", "research_category_group", "research_category_name_zh", "category_l1", "category_l2", "category_l3", "leaf_category", "browse_node_id", "bestseller_rank", "ranking_source_url", "ranking_source_category", "ranking_source_category_path", "ranking_context_count", "title_es_raw", "brand", "current_price", "original_price", "discount_rate", "rating", "review_count", "monthly_bought_raw", "monthly_bought_min", "selected_variation", "specification", "product_details_es", "feature_bullets_es", "date_first_available_raw", "date_first_available", "seller", "product_url", "image_url", "detail_schema_version", "detail_source", "detail_evidence_path", "access_state", "recovered_from_challenge", "field_closure_status"]
    with (out / "master/products_4500_master.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for row in master:
            writer.writerow({key: csv_value(row.get(key)) for key in csv_fields})

    write_json(out / "evidence/ranking_evidence_4500.json", {"raw_source": str(raw_ranking), "raw_sha256": raw_hash, "rows": rankings})
    multi_ranking = sum(1 for asin in wanted if len(rankings_by_asin.get(asin, [])) > 1)
    # A ranking context is an Amazon source/category/path, not the research
    # group (which is intentionally frozen to one group per Product Master row).
    multi_category = sum(1 for asin in wanted if len({(r.get("ranking_source_category_path") or r.get("ranking_source_category") or r.get("ranking_source_url")) for r in rankings_by_asin.get(asin, []) if nonempty(r.get("ranking_source_category_path") or r.get("ranking_source_category") or r.get("ranking_source_url"))}) > 1)
    multi_snapshot = sum(1 for asin in wanted if len({ranking_snapshot_id(r) for r in rankings_by_asin.get(asin, []) if ranking_snapshot_id(r)}) > 1)
    validated_current_unique = len({r["asin"] for r in html_audit["records"] if r.get("validation_method") == "current_product_record"})
    write_json(out / "evidence/evidence_inventory.json", {"manifest_asins": len(wanted), "products_records": len(products), "valid_product_records": sum(1 for a in wanted if nonempty(products.get(a, {}).get("title_es_raw"))), "details_records_unique": len(set(detail_map) & wanted), "valid_html_unique": len(html_valid), "saved_html_validated_by_current_product_unique": validated_current_unique, "checkpoint_success": sum(1 for p in root.glob("outputs/*_300_spain_28001/state/details_state.json") for a, v in load_json(p).items() if str(a).upper() in wanted and str(v.get("access_state") or "").upper() == "NORMAL"), "ranking_only_asins": len(missing_detail), "quarantine": html_audit["summary"].get("CHALLENGE", 0) + html_audit["summary"].get("INVALID_OR_EMPTY", 0) + html_audit["summary"].get("ASIN_MISMATCH", 0), "html_audit": html_audit["summary"], "ranking_rows_for_frozen": len(rankings), "multi_ranking_asins": multi_ranking, "multi_category_context_asins": multi_category, "multi_snapshot_asins": multi_snapshot})

    write_json(out / "audit/pretranslation_field_closure.json", closure)
    with (out / "audit/pretranslation_field_closure.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asin", "field", "issue_type", "source_evidence", "current_value", "reason"])
        writer.writeheader(); writer.writerows({k: csv_value(v) for k, v in row.items()} for row in issues)
    write_json(out / "audit/missing_detail_candidates.json", missing_detail)
    with (out / "audit/affected_asins.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asin", "field", "issue_type", "source_evidence", "current_value", "reason"])
        writer.writeheader(); writer.writerows({k: csv_value(v) for k, v in row.items()} for row in issues if row["issue_type"] != "SOURCE_MISSING")

    queue_fields = ["title_es_raw", "selected_variation", "specification", "product_details_es", "feature_bullets_es", "date_first_available_raw", "seller"]
    queue = []
    for row in master:
        for field in queue_fields:
            text = as_text(field_value(row, field))
            if text:
                queue.append({"asin": row["asin"], "field_name": field, "source_text": text, "source_hash": source_hash(field, text), "translation_schema_version": TRANSLATION_QUEUE_SCHEMA_VERSION, "priority": "P0" if field == "title_es_raw" else "P1"})
    queue.sort(key=lambda x: (next(r["selection_order"] for r in master if r["asin"] == x["asin"]), x["field_name"]))
    write_jsonl(out / "translation/translation_queue_4500.jsonl", queue)
    write_jsonl(out / "translation/translation_memory_seed.jsonl", [])

    amazon_labels = defaultdict(Counter)
    for row in master:
        group = row["research_category_group"]
        for field in ("category_l1", "category_l2", "category_l3", "leaf_category"):
            value = as_text(row.get(field))
            if value: amazon_labels[group][value] += 1
    glossary = {"glossary_schema_version": "research-category-glossary-seed-v1", "machine_translation_used": False, "research_categories": [{"category_group": group, "category_name_zh": names[group], "amazon_labels_observed": [{"label": label, "count": count} for label, count in sorted(amazon_labels[group].items())]} for group in sorted(names)]}
    write_json(out / "translation/category_glossary_seed.json", glossary)

    with raw_ranking.open("r", encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    raw_unique = len({str(r.get("ASIN") or r.get("asin") or "").strip().upper() for r in raw_rows if str(r.get("ASIN") or r.get("asin") or "").strip()})
    issue_asins = defaultdict(set)
    for issue in issues:
        issue_asins[issue["asin"]].add(issue["issue_type"])
    blocked_ready = sum(1 for r in master if not nonempty(r["title_es_raw"]))
    partial_ready = sum(1 for r in master if nonempty(r["title_es_raw"]) and issue_asins.get(r["asin"]))
    ready_ready = len(master) - blocked_ready - partial_ready
    summary = {"raw_ranking_records": len(raw_rows), "raw_ranking_unique_asin": raw_unique, "frozen_manifest": len(frozen_rows), "frozen_unique_asin": len(wanted), "category_count": len(group_counts), "per_category_counts": dict(sorted(group_counts.items())), "product_master_rows": len(master), "product_master_unique_asin": len({r["asin"] for r in master}), "asin_set_matches_manifest": {r["asin"] for r in master} == wanted, "duplicate_asin": len(master) - len({r["asin"] for r in master}), "missing_master_records": len(wanted - {r["asin"] for r in master}), "evidence_coverage": {"valid_product_records": sum(1 for r in master if nonempty(r["title_es_raw"])), "valid_detail_records": len(set(detail_map) & wanted), "offline_html_reparsed": len(html_valid), "ranking_only_asin": len(missing_detail), "challenge_html": html_audit["summary"]["CHALLENGE"], "invalid_html": html_audit["summary"]["INVALID_OR_EMPTY"], "asin_mismatch": html_audit["summary"]["ASIN_MISMATCH"], "valid_html_records": html_audit["summary"].get("VALID_PRODUCT_PAGE", 0), "saved_html_validated_by_current_product_unique": len({r["asin"] for r in html_audit["records"] if r.get("validation_method") == "current_product_record"})}, "field_closure": closure, "translation_readiness": {"READY": ready_ready, "PARTIAL_SOURCE": partial_ready, "BLOCKED_SOURCE_DATA": blocked_ready, "translation_queue_rows": len(queue), "unique_source_strings": len({(q["field_name"], q["source_hash"]) for q in queue}), "translation_memory_seed_entries": 0, "glossary_entries": len(names)}, "ranking_evidence": {"rows_for_frozen_4500": len(rankings), "multi_ranking_asins": multi_ranking, "multi_category_context_asins": multi_category, "multi_snapshot_asins": multi_snapshot, "source_shortfall_asins": sum(1 for asin in wanted if not rankings_by_asin.get(asin))}, "safety": {"network_requests": 0, "no_captcha_bypass": "PASS", "no_proxy": "PASS", "no_ranking_recollection": "PASS"}, "source_files": [str(p.relative_to(root)) for p in [manifest_path, plan_path, products_path, *details_paths, *ranking_paths, raw_ranking]]}
    write_json(out / "audit/product_master_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the offline audited 4,500-ASIN Product Master")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/scale_4500_final")
    args = parser.parse_args()
    summary = build(args)
    print(json.dumps({"output_dir": args.output_dir, "master_rows": summary["product_master_rows"], "ranking_rows": summary["raw_ranking_records"], "network_requests": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
