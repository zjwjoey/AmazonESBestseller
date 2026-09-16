# -*- coding: utf-8 -*-
"""Offline ranking trace backfill and gap audit.

This command only reads saved ranking/product evidence.  It never requests
Amazon and never fabricates a missing rank.  Derived files are written next to
the supplied inputs so the frozen export can remain unchanged until reviewed.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from amazon_es_bestseller.collection.ranking import (
    _ranking_page_number,
    _ranking_source_type,
)


TRACE_FIELDS = (
    "ranking_source_type",
    "ranking_source_category",
    "ranking_source_category_path",
    "ranking_page_number",
)


def _load_rows(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("records", value.get("items", []))
    return value if isinstance(value, list) else []


def _source_category(record: dict) -> str | None:
    for key in ("leaf_category", "category_l3", "category_l2", "category_l1"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return None


def _source_category_path(record: dict) -> str | None:
    values: list[str] = []
    for key in ("category_l1", "category_l2", "category_l3", "leaf_category"):
        value = str(record.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    return " > ".join(values) if values else None


def add_trace_fields(record: dict) -> dict:
    """Return a copy with only source-derived trace metadata added."""
    out = dict(record)
    url = str(out.get("ranking_source_url") or "").strip()
    browse_node = str(out.get("browse_node_id") or "").strip() or None
    out["ranking_source_type"] = _ranking_source_type(url, browse_node)
    out["ranking_source_category"] = _source_category(out)
    out["ranking_source_category_path"] = _source_category_path(out)
    out["ranking_page_number"] = _ranking_page_number(url)
    return out


def _ranges(values: set[int]) -> list[list[int]]:
    if not values:
        return []
    missing: list[int] = []
    for value in range(min(values), max(values) + 1):
        if value not in values:
            missing.append(value)
    ranges: list[list[int]] = []
    for value in missing:
        if not ranges or value != ranges[-1][1] + 1:
            ranges.append([value, value])
        else:
            ranges[-1][1] = value
    return ranges


def _mismatch_asins(paths: list[Path], requested: str) -> list[str]:
    found: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        patterns = (
            r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"'][^\"']*/dp/([A-Z0-9]{10})",
            r"<meta[^>]+property=[\"']og:url[\"'][^>]+content=[\"'][^\"']*/dp/([A-Z0-9]{10})",
            r"<input[^>]+(?:id|name)=[\"']ASIN[\"'][^>]+value=[\"']([A-Z0-9]{10})",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I):
                asin = match.group(1).upper()
                if asin != requested:
                    found.add(asin)
    return sorted(found)


def build_products(products: list[dict], rankings: list[dict]) -> list[dict]:
    by_asin: dict[str, list[dict]] = defaultdict(list)
    for row in rankings:
        asin = str(row.get("asin") or "").strip().upper()
        if asin:
            context = add_trace_fields(row)
            if context not in by_asin[asin]:
                by_asin[asin].append(context)
    out: list[dict] = []
    for product in products:
        row = dict(product)
        asin = str(row.get("asin") or "").strip().upper()
        contexts = by_asin.get(asin, [])
        if contexts:
            first = contexts[0]
            for key in TRACE_FIELDS:
                row[key] = first.get(key)
            row["ranking_contexts"] = contexts
            row["ranking_context_count"] = len(contexts)
        if asin == "B0CZS7F7T1":
            mismatch_paths = [
                Path("outputs/sports_300_spain_28001/quarantine/B0CZS7F7T1/B0CZS7F7T1.html"),
                Path("outputs/luggage_300_spain_28001/quarantine/B0CZS7F7T1/B0CZS7F7T1.html"),
            ]
            observed = _mismatch_asins(mismatch_paths, asin)
            row["detail_quality_status"] = "INVALID_ASIN_MISMATCH"
            row["detail_quality_error"] = (
                "详情页 canonical/title 与请求 ASIN 不一致"
            )
            row["detail_expected_asin"] = asin
            row["detail_observed_asins"] = observed
            row["detail_evidence_paths"] = [str(p) for p in mismatch_paths if p.exists()]
        out.append(row)
    return out


def audit(rankings: list[dict], products: list[dict]) -> dict:
    traced = [add_trace_fields(r) for r in rankings]
    by_url: dict[str, list[dict]] = defaultdict(list)
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in traced:
        by_url[str(row.get("ranking_source_url") or "")].append(row)
        by_group[str(row.get("category_group") or "")].append(row)
    sources = []
    for url, rows in sorted(by_url.items()):
        ranks = {int(r["bestseller_rank"]) for r in rows
                 if isinstance(r.get("bestseller_rank"), int)}
        pages = {int(r.get("ranking_page_number") or 1) for r in rows}
        missing = _ranges(ranks)
        gap_reason = None
        if 2 in pages and ranks and min(ranks) >= 51:
            gap_reason = "SOURCE_PAGE_OMITS_31_50"
        sources.append({
            "ranking_source_url": url,
            "ranking_page_numbers": sorted(pages),
            "visible_records": len(rows),
            "visible_rank_min": min(ranks) if ranks else None,
            "visible_rank_max": max(ranks) if ranks else None,
            "missing_ranges_between_visible_ranks": missing,
            "rank_31_50_records": sum(31 <= r <= 50 for r in ranks),
            "gap_reason": gap_reason,
        })
    groups = []
    for group, rows in sorted(by_group.items()):
        ranks = [r.get("bestseller_rank") for r in rows]
        groups.append({
            "category_group": group,
            "records": len(rows),
            "unique_asins": len({r.get("asin") for r in rows}),
            "rank_1_30": sum(isinstance(r, int) and 1 <= r <= 30 for r in ranks),
            "rank_31_50": sum(isinstance(r, int) and 31 <= r <= 50 for r in ranks),
            "rank_51_80": sum(isinstance(r, int) and 51 <= r <= 80 for r in ranks),
        })
    return {
        "source": "saved rankings_4500.json",
        "ranking_records": len(rankings),
        "ranking_unique_asins": len({str(r.get("asin") or "").upper() for r in rankings}),
        "product_records": len(products),
        "source_url_count": len(sources),
        "rank_31_50_total": sum(
            isinstance(r.get("bestseller_rank"), int) and 31 <= r["bestseller_rank"] <= 50
            for r in rankings
        ),
        "page_2_source_gap_count": sum(s["gap_reason"] == "SOURCE_PAGE_OMITS_31_50" for s in sources),
        "groups": groups,
        "sources": sources,
        "asin_fix": {
            "asin": "B0CZS7F7T1",
            "action": "retain_ranking_only_and_block_mismatched_detail",
            "observed_page_asins": _mismatch_asins([
                Path("outputs/sports_300_spain_28001/quarantine/B0CZS7F7T1/B0CZS7F7T1.html"),
                Path("outputs/luggage_300_spain_28001/quarantine/B0CZS7F7T1/B0CZS7F7T1.html"),
            ], "B0CZS7F7T1"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", required=True, type=Path)
    parser.add_argument("--products", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    rankings = _load_rows(args.rankings)
    products = _load_rows(args.products)
    traced_rankings = [add_trace_fields(r) for r in rankings]
    traced_products = build_products(products, rankings)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "rankings_4500_trace.json").write_text(
        json.dumps(traced_rankings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out_dir / "products_4500_trace.json").write_text(
        json.dumps(traced_products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = audit(rankings, products)
    (args.out_dir / "ranking_trace_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "ranking_records", "ranking_unique_asins", "rank_31_50_total",
        "page_2_source_gap_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
