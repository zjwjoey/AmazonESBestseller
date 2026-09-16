# -*- coding: utf-8 -*-
"""Build the all-category lazy-loaded ranking supplement."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "outputs" / "scale_4500_repair" / "lazy_root_supplement"
TRACE = ROOT / "outputs" / "scale_4500_repair" / "trace_enriched"


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def titles_from_html(path: Path) -> dict[str, str]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    out = {}
    for card in soup.select("#gridItemRoot"):
        node = card.select_one("[data-asin]")
        if node is None:
            continue
        asin = str(node.get("data-asin") or "").strip().upper()
        texts = [a.get_text(" ", strip=True) for a in card.select('a[href*="/dp/"]')]
        texts = [t for t in texts if t]
        if asin and texts:
            out[asin] = max(texts, key=len)
    return out


def main() -> None:
    rankings = json.loads((SRC_DIR / "rankings.json").read_text(encoding="utf-8"))
    run_html = sorted((SRC_DIR / "runs").glob("*/html/ranking_*.html"))
    title_maps = {i: titles_from_html(path) for i, path in enumerate(run_html)}
    config = json.loads((ROOT / "configs" / "amazon_es_4500sku_categories.json").read_text(encoding="utf-8"))
    urls = [str(item.get("url")) for item in config.get("categories", [])]
    url_to_index = {url: i for i, url in enumerate(urls)}

    supplement = []
    for row in rankings:
        rank = int(row.get("bestseller_rank") or 0)
        if not 31 <= rank <= 50:
            continue
        item = dict(row)
        asin = str(item.get("asin") or "").upper()
        item["title_es_raw"] = title_maps.get(url_to_index.get(item.get("ranking_source_url"), -1), {}).get(asin, "")
        item["evidence_type"] = "browser_scroll_lazy_loaded"
        item["evidence_html"] = "outputs/scale_4500_repair/lazy_root_supplement/runs/*/html/ranking_*.html"
        supplement.append(item)

    out_json = TRACE / "ranking_supplement_all_categories_31_50.json"
    dump(out_json, supplement)
    out_csv = TRACE / "ranking_supplement_all_categories_31_50.csv"
    fields = ["ranking_source_category", "bestseller_rank", "asin", "title_es_raw",
              "ranking_source_category_path", "ranking_source_url", "evidence_type"]
    with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in supplement)

    products = json.loads((TRACE / "products_4500_trace.json").read_text(encoding="utf-8"))
    by_asin = {str(r.get("asin") or "").upper(): r for r in products}
    merged = 0
    for context in supplement:
        product = by_asin.get(str(context.get("asin") or "").upper())
        if product is None:
            continue
        contexts = product.get("ranking_contexts")
        if not isinstance(contexts, list):
            contexts = []
        key = (context.get("ranking_source_url"), context.get("bestseller_rank"))
        if any((c.get("ranking_source_url"), c.get("bestseller_rank")) == key
               for c in contexts if isinstance(c, dict)):
            continue
        contexts.append(context)
        product["ranking_contexts"] = contexts
        product["ranking_context_count"] = len(contexts)
        merged += 1

    out_products = TRACE / "products_4500_trace_with_lazy_root_context.json"
    dump(out_products, products)
    category_counts = {}
    for row in supplement:
        category_counts[row.get("ranking_source_url")] = category_counts.get(row.get("ranking_source_url"), 0) + 1
    dump(TRACE / "ranking_supplement_all_categories_31_50_audit.json", {
        "source": "outputs/scale_4500_repair/lazy_root_supplement/rankings.json",
        "rank_range": [31, 50],
        "source_urls_with_31_50": len(category_counts),
        "supplement_records": len(supplement),
        "unique_asins": len({r.get("asin") for r in supplement}),
        "merged_existing_product_contexts": merged,
        "ranking_only_candidates": len(supplement) - merged,
        "category_counts": category_counts,
        "luggage_source_status": "SOURCE_MISSING_NO_BESTSELLERS",
    })
    print(json.dumps({"supplement_records": len(supplement), "source_urls_with_31_50": len(category_counts),
                      "merged_existing_product_contexts": merged,
                      "ranking_only_candidates": len(supplement) - merged,
                      "json": str(out_json), "csv": str(out_csv), "products": str(out_products)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
