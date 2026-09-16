# -*- coding: utf-8 -*-
"""Merge browser-scroll bestseller evidence without overwriting raw records.

The source ranking JSON is produced by the normal serial collector after its
bounded lazy-load scroll.  New ASINs remain ranking-only candidates until a
separate detail collection is approved; existing product records receive an
additional ranking context.
"""
from __future__ import annotations

import json
import csv
from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs" / "beauty_lazy_validation" / "rankings.json"
TRACE = ROOT / "outputs" / "scale_4500_repair" / "trace_enriched"
PRODUCTS = TRACE / "products_4500_trace.json"


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    rankings = json.loads(SOURCE.read_text(encoding="utf-8"))
    supplement = [dict(r) for r in rankings
                  if 31 <= int(r.get("bestseller_rank") or 0) <= 50]
    html_paths = sorted((ROOT / "outputs" / "beauty_lazy_validation" / "runs").glob(
        "*/html/ranking_000.html"))
    if html_paths:
        soup = BeautifulSoup(html_paths[-1].read_text(encoding="utf-8", errors="ignore"), "lxml")
        titles = {}
        for card in soup.select("#gridItemRoot"):
            badge = card.select_one("span.zg-bdg-text, span.a-badge-text")
            asin_node = card.select_one("[data-asin]")
            if badge is None or asin_node is None:
                continue
            asin = str(asin_node.get("data-asin") or "").strip().upper()
            texts = [a.get_text(" ", strip=True) for a in card.select('a[href*="/dp/"]')]
            texts = [t for t in texts if t]
            if asin and texts:
                titles[asin] = max(texts, key=len)
        for row in supplement:
            row["title_es_raw"] = titles.get(str(row.get("asin") or "").upper(), "")
    for row in supplement:
        row["evidence_type"] = "browser_scroll_lazy_loaded"
        row["evidence_html"] = "outputs/beauty_lazy_validation/runs/*/html/ranking_000.html"

    out_supplement = TRACE / "ranking_supplement_beauty_31_50.json"
    dump(out_supplement, supplement)
    out_csv = TRACE / "ranking_supplement_beauty_31_50.csv"
    with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "bestseller_rank", "asin", "title_es_raw", "ranking_source_category",
            "ranking_source_category_path", "ranking_source_url", "evidence_type",
        ])
        writer.writeheader()
        for row in supplement:
            writer.writerow({
                "bestseller_rank": row.get("bestseller_rank"),
                "asin": row.get("asin"),
                "title_es_raw": row.get("title_es_raw", ""),
                "ranking_source_category": row.get("ranking_source_category", ""),
                "ranking_source_category_path": row.get("ranking_source_category_path", ""),
                "ranking_source_url": row.get("ranking_source_url", ""),
                "evidence_type": row.get("evidence_type", ""),
            })

    products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
    by_asin = {str(r.get("asin") or "").upper(): r for r in products}
    merged_existing = 0
    for context in supplement:
        asin = str(context.get("asin") or "").upper()
        product = by_asin.get(asin)
        if product is None:
            continue
        contexts = product.get("ranking_contexts")
        if not isinstance(contexts, list):
            contexts = []
        # Do not duplicate an identical source/rank observation.
        key = (context.get("ranking_source_url"), context.get("bestseller_rank"))
        if not any((c.get("ranking_source_url"), c.get("bestseller_rank")) == key
                   for c in contexts if isinstance(c, dict)):
            contexts.append(context)
            product["ranking_contexts"] = contexts
            product["ranking_context_count"] = len(contexts)
            merged_existing += 1

    out_products = TRACE / "products_4500_trace_with_beauty_lazy_context.json"
    dump(out_products, products)
    dump(TRACE / "ranking_supplement_beauty_31_50_audit.json", {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_url": supplement[0].get("ranking_source_url") if supplement else None,
        "rank_range": [31, 50],
        "supplement_records": len(supplement),
        "unique_asins": len({r.get("asin") for r in supplement}),
        "merged_existing_product_records": merged_existing,
        "new_ranking_only_candidates": len(supplement) - merged_existing,
        "evidence_type": "browser_scroll_lazy_loaded",
    })
    print(json.dumps({
        "supplement_records": len(supplement),
        "merged_existing_product_records": merged_existing,
        "new_ranking_only_candidates": len(supplement) - merged_existing,
        "supplement": str(out_supplement),
        "csv": str(out_csv),
        "products": str(out_products),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
