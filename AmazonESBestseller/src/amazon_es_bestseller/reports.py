import csv
import json
from dataclasses import asdict, fields
from pathlib import Path

from .models import ProductSummary, RankingRecord


def _write_dicts(rows: list[dict], path: Path, fieldnames: list[str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = fieldnames or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)
    return path


def write_ranking_csv(records: list[RankingRecord], path: Path) -> Path:
    return _write_dicts(
        [asdict(record) for record in records],
        path,
        [field.name for field in fields(RankingRecord)],
    )


def write_products_csv(products: list[ProductSummary], path: Path) -> Path:
    return _write_dicts(
        [asdict(product) for product in products],
        path,
        [field.name for field in fields(ProductSummary)],
    )


def build_field_availability(records: list[RankingRecord]) -> list[dict]:
    total = len(records)
    rows = []
    for field in fields(RankingRecord):
        name = field.name
        non_null = sum(getattr(record, name) is not None for record in records)
        rows.append(
            {
                "field": name,
                "records": total,
                "non_null": non_null,
                "null": total - non_null,
                "availability_rate": round(non_null / total, 4) if total else 0.0,
                "source": "ranking_records",
            }
        )
    return rows


def write_field_availability_csv(records: list[RankingRecord], path: Path) -> Path:
    return _write_dicts(build_field_availability(records), path)


def duplicate_summary(records: list[RankingRecord]) -> dict[str, float | int]:
    unique_asins = len({record.asin for record in records if record.asin})
    duplicate_records = max(len(records) - unique_asins, 0)
    return {
        "ranking_records": len(records),
        "unique_asins": unique_asins,
        "duplicate_records": duplicate_records,
        "duplicate_rate": round(duplicate_records / len(records), 4) if records else 0.0,
    }


REPORT_HEADINGS = [
    "测试环境", "测试日期", "测试页面", "页面访问结果", "是否出现访问限制",
    "Best Sellers 页面结构", "商品卡结构", "可稳定提取字段", "不稳定字段", "无法获得字段",
    "ASIN提取成功率", "Rank提取成功率", "价格提取成功率", "Rating提取成功率",
    "Review Count成功率", "Monthly Bought出现率", "家居类目树", "二级类目数量",
    "三级类目观察结果", "每个类目榜单商品数量", "是否存在分页", "是否存在懒加载",
    "榜单最大深度", "Ranking Record数量", "Unique ASIN数量", "重复率",
    "商品详情页额外字段", "页面 vs Creators API字段分工建议", "访问稳定性", "是否建议进入正式开发",
]


def write_category_tree(nodes, csv_path: Path, json_path: Path) -> None:
    rows = [
        {
            "level_1": "Hogar y cocina",
            "level_2": node.category_name_es,
            "level_3": None,
            "category_name_es": node.category_name_es,
            "browse_node_id": node.browse_node_id,
            "category_url": node.category_url,
            "depth": node.depth,
        }
        for node in nodes
    ]
    _write_dicts(rows, csv_path, ["level_1", "level_2", "level_3", "category_name_es", "browse_node_id", "category_url", "depth"])
    tree = {
        "name": "Hogar y cocina",
        "browse_node_id": None,
        "children": [
            {
                "name": node.category_name_es,
                "browse_node_id": node.browse_node_id,
                "url": node.category_url,
                "children": [],
            }
            for node in nodes
        ],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(path_or_dir: Path, summary: dict) -> Path:
    path = path_or_dir / "report.md" if path_or_dir.is_dir() else path_or_dir
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Amazon.es Best Sellers 页面侦察报告", ""]
    values = {
        "测试环境": summary.get("environment", "Python + Playwright"),
        "测试日期": summary.get("tested_at", ""),
        "测试页面": summary.get("tested_pages", ""),
        "页面访问结果": summary.get("page_access_result", ""),
        "是否出现访问限制": summary.get("access_restriction", "未发现"),
        "Best Sellers 页面结构": summary.get("page_structure", "以保存 HTML 为依据离线分析"),
        "商品卡结构": summary.get("card_structure", ""),
        "可稳定提取字段": summary.get("stable_fields", ""),
        "不稳定字段": summary.get("unstable_fields", ""),
        "无法获得字段": summary.get("unavailable_fields", ""),
        "ASIN提取成功率": summary.get("asin_success_rate", ""),
        "Rank提取成功率": summary.get("rank_success_rate", ""),
        "价格提取成功率": summary.get("price_success_rate", ""),
        "Rating提取成功率": summary.get("rating_success_rate", ""),
        "Review Count成功率": summary.get("review_success_rate", ""),
        "Monthly Bought出现率": summary.get("monthly_bought_rate", ""),
        "家居类目树": summary.get("category_tree", ""),
        "二级类目数量": summary.get("category_count", 0),
        "三级类目观察结果": summary.get("level3_observation", ""),
        "每个类目榜单商品数量": summary.get("category_product_counts", ""),
        "是否存在分页": summary.get("pagination", "未构造额外分页请求"),
        "是否存在懒加载": summary.get("lazy_loading", "未绕过或触发懒加载"),
        "榜单最大深度": summary.get("max_rank_depth", ""),
        "Ranking Record数量": summary.get("ranking_records", 0),
        "Unique ASIN数量": summary.get("unique_asins", 0),
        "重复率": summary.get("duplicate_rate", 0),
        "商品详情页额外字段": summary.get("detail_fields", ""),
        "页面 vs Creators API字段分工建议": summary.get("api_split", ""),
        "访问稳定性": summary.get("access_stability", ""),
        "是否建议进入正式开发": summary.get("decision", "NO-GO"),
    }
    for index, heading in enumerate(REPORT_HEADINGS, start=1):
        lines.extend([f"## {index}. {heading}", "", str(values[heading]), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
