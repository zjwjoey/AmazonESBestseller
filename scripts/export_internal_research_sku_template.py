#!/usr/bin/env python3
"""Export all unique ASINs using the internal research selection template."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from amazon_es_bestseller.pipeline import normalize_product


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "outputs/scale_4500_repair/non_deduplicated_ranking_sku_list_9529.csv"
DETAIL_FILES = [
    ROOT / "outputs/scale_4500_repair/details_4500.json",
    ROOT / "outputs/scale_4500_repair/rank_31_50_batch/details.json",
]
MASTER = ROOT / "outputs/scale_4500_final/master/products_4500_master.json"
OUT = ROOT / "outputs/scale_4500_final/master/sku_list_7365_internal_research"

FIELDS = [
    "序号", "ASIN", "Parent ASIN", "商品名称（西语）", "品牌", "当前售价", "划线原价", "折扣率",
    "评分", "评论数", "月购买量", "一级类目", "二级类目", "三级类目", "细分类目", "畅销榜排名",
    "当前选中规格 / 变体（西语）", "核心规格（西语）", "完整商品详情（西语原文）", "商品卖点（西语原文）",
    "首次上架日期", "卖家", "商品链接", "图片链接", "备注", "来源类目", "来源类目路径",
    "榜单来源类型", "榜单分页", "榜单来源URL", "榜单追溯上下文数", "详情质量状态", "详情错配证据",
]


def text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x).strip() for x in value if str(x).strip())
    return str(value).strip()


def records(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return value.get("records", value.get("details", [])) if isinstance(value, dict) else []


def first_nonempty(*values):
    for value in values:
        if text(value):
            return value
    return ""


def scalar(value):
    if value is None:
        return ""
    return value


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    detail_map = {}
    for path in DETAIL_FILES:
        for row in records(path):
            asin = text(row.get("asin")).upper()
            if asin and asin not in detail_map:
                detail_map[asin] = row

    master_map = {}
    if MASTER.exists():
        for row in records(MASTER):
            asin = text(row.get("asin")).upper()
            if asin:
                master_map[asin] = row

    first = {}
    occurrence_count = Counter()
    contexts = defaultdict(list)
    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            asin = text(raw.get("ASIN") or raw.get("asin")).upper()
            if not asin:
                continue
            occurrence_count[asin] += 1
            first.setdefault(asin, raw)
            context = "|".join([
                text(raw.get("榜单来源URL")),
                text(raw.get("榜单类目路径")) or text(raw.get("榜单类目")),
            ])
            if context and context not in contexts[asin]:
                contexts[asin].append(context)

    output = []
    for order, asin in enumerate(first, 1):
        raw = first[asin]
        detail = detail_map.get(asin, {})
        try:
            normalized = normalize_product(detail) if detail else {}
        except Exception:
            normalized = dict(detail)
        master = master_map.get(asin, {})
        source = {**normalized, **{k: v for k, v in master.items() if text(v)}}
        has_detail = bool(text(detail.get("title_es_raw")) or text(detail.get("attributes")) or text(detail.get("feature_bullets_raw")) or text(detail.get("product_description_raw")))
        mismatch = []
        if not has_detail:
            mismatch.append("RANKING_ONLY")
        if source.get("detail_source") and "ranking_only" in text(source.get("detail_source")):
            mismatch.append("NO_DETAIL_RECORD")
        row = {
            "序号": order,
            "ASIN": asin,
            "Parent ASIN": first_nonempty(source.get("parent_asin")),
            "商品名称（西语）": first_nonempty(source.get("title_es_raw"), raw.get("商品名称（西语）")),
            "品牌": first_nonempty(source.get("brand"), source.get("brand_raw")),
            "当前售价": scalar(source.get("current_price")),
            "划线原价": scalar(source.get("original_price")),
            "折扣率": scalar(source.get("discount_rate")),
            "评分": first_nonempty(source.get("rating"), source.get("rating_raw")),
            "评论数": first_nonempty(source.get("review_count"), source.get("review_count_raw")),
            "月购买量": first_nonempty(source.get("monthly_bought_raw")),
            "一级类目": first_nonempty(source.get("category_l1"), raw.get("一级类目")),
            "二级类目": first_nonempty(source.get("category_l2"), raw.get("二级类目")),
            "三级类目": first_nonempty(source.get("category_l3"), raw.get("三级类目")),
            "细分类目": first_nonempty(source.get("leaf_category"), raw.get("细分类目")),
            "畅销榜排名": first_nonempty(source.get("bestseller_rank"), raw.get("畅销榜排名")),
            "当前选中规格 / 变体（西语）": first_nonempty(source.get("selected_variation"), source.get("selected_variation_raw")),
            "核心规格（西语）": first_nonempty(source.get("specification"), source.get("specification_es")),
            "完整商品详情（西语原文）": first_nonempty(source.get("product_details_es")),
            "商品卖点（西语原文）": first_nonempty(source.get("feature_bullets_es")),
            "首次上架日期": first_nonempty(source.get("date_first_available"), source.get("date_first_available_raw")),
            "卖家": first_nonempty(source.get("seller"), source.get("seller_raw")),
            "商品链接": first_nonempty(source.get("product_url"), f"https://www.amazon.es/dp/{asin}"),
            "图片链接": first_nonempty(source.get("image_url")),
            "备注": "",
            "来源类目": first_nonempty(raw.get("榜单类目"), raw.get("一级类目")),
            "来源类目路径": first_nonempty(raw.get("榜单类目路径")),
            "榜单来源类型": first_nonempty(raw.get("榜单来源类型")),
            "榜单分页": first_nonempty(raw.get("榜单页码")),
            "榜单来源URL": first_nonempty(raw.get("榜单来源URL")),
            "榜单追溯上下文数": len(contexts[asin]),
            "详情质量状态": "AVAILABLE" if has_detail else "RANKING_ONLY",
            "详情错配证据": ";".join(dict.fromkeys(mismatch)),
        }
        output.append(row)

    with OUT.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)
    payload = {
        "template": "internal_research_selection_v1",
        "source_rows": sum(occurrence_count.values()),
        "unique_asins": len(output),
        "detail_available": sum(r["详情质量状态"] == "AVAILABLE" for r in output),
        "ranking_only": sum(r["详情质量状态"] == "RANKING_ONLY" for r in output),
        "fields": FIELDS,
        "records": output,
    }
    OUT.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("source_rows", "unique_asins", "detail_available", "ranking_only")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
