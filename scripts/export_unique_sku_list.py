#!/usr/bin/env python3
"""Export the complete unique-ASIN list from preserved ranking evidence."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "outputs/scale_4500_repair/non_deduplicated_ranking_sku_list_9529.csv"
DETAIL_FILES = [
    ROOT / "outputs/scale_4500_repair/details_4500.json",
    ROOT / "outputs/scale_4500_repair/rank_31_50_batch/details.json",
]
OUT = ROOT / "outputs/scale_4500_final/master"


def load_records(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return value.get("records", value.get("details", [])) if isinstance(value, dict) else []


def text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x).strip() for x in value if str(x).strip())
    return str(value).strip()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    details = {}
    for path in DETAIL_FILES:
        for row in load_records(path):
            asin = text(row.get("asin")).upper()
            if asin and asin not in details:
                details[asin] = row

    first = {}
    occurrences = Counter()
    urls = defaultdict(list)
    contexts = defaultdict(list)
    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            asin = text(raw.get("ASIN") or raw.get("asin")).upper()
            if not asin:
                continue
            occurrences[asin] += 1
            if asin not in first:
                first[asin] = raw
            for key in ("榜单来源URL", "榜单来源URL "):
                url = text(raw.get(key))
                if url and url not in urls[asin]:
                    urls[asin].append(url)
            context = " > ".join(x for x in (text(raw.get("榜单类目")), text(raw.get("榜单类目路径"))) if x)
            if context and context not in contexts[asin]:
                contexts[asin].append(context)

    records = []
    for order, asin in enumerate(first, 1):
        rank = first[asin]
        detail = details.get(asin, {})
        records.append({
            "selection_order": order,
            "asin": asin,
            "occurrence_count": occurrences[asin],
            "ranking_context_count": len(contexts[asin]),
            "ranking_source_urls": urls[asin],
            "ranking_contexts": contexts[asin],
            "ranking_rank_first_seen": text(rank.get("畅销榜排名")),
            "ranking_category_first_seen": text(rank.get("榜单类目")),
            "ranking_category_path_first_seen": text(rank.get("榜单类目路径")),
            "category_l1_first_seen": text(rank.get("一级类目")),
            "category_l2_first_seen": text(rank.get("二级类目")),
            "category_l3_first_seen": text(rank.get("三级类目")),
            "leaf_category_first_seen": text(rank.get("细分类目")),
            "browse_node_id_first_seen": text(rank.get("浏览节点ID")),
            "collected_at_first_seen": text(rank.get("采集时间")),
            "title_es_raw": text(detail.get("title_es_raw")),
            "brand": text(detail.get("brand") or detail.get("brand_raw")),
            "current_price": text(detail.get("current_price")),
            "rating": text(detail.get("rating") or detail.get("rating_raw")),
            "review_count": text(detail.get("review_count") or detail.get("review_count_raw")),
            "product_url": text(detail.get("product_url")) or f"https://www.amazon.es/dp/{asin}",
            "image_url": text(detail.get("image_url")),
            "detail_status": "AVAILABLE" if detail else "RANKING_ONLY",
        })

    payload = {
        "source_file": str(RAW.relative_to(ROOT)),
        "source_rows": sum(occurrences.values()),
        "unique_asins": len(records),
        "dedupe_rule": "ASIN, first occurrence order preserved",
        "records": records,
    }
    (OUT / "sku_list_7365_unique.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = [
        "selection_order", "asin", "occurrence_count", "ranking_context_count",
        "ranking_source_urls", "ranking_contexts", "ranking_rank_first_seen",
        "ranking_category_first_seen", "ranking_category_path_first_seen",
        "category_l1_first_seen", "category_l2_first_seen", "category_l3_first_seen",
        "leaf_category_first_seen", "browse_node_id_first_seen", "collected_at_first_seen",
        "title_es_raw", "brand", "current_price", "rating", "review_count",
        "product_url", "image_url", "detail_status",
    ]
    with (OUT / "sku_list_7365_unique.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in records:
            out = dict(row)
            out["ranking_source_urls"] = "\n".join(row["ranking_source_urls"])
            out["ranking_contexts"] = "\n".join(row["ranking_contexts"])
            writer.writerow({key: out.get(key, "") for key in fields})
    print(json.dumps({"source_rows": sum(occurrences.values()), "unique_asins": len(records), "detail_available": sum(r["detail_status"] == "AVAILABLE" for r in records), "ranking_only": sum(r["detail_status"] == "RANKING_ONLY" for r in records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
