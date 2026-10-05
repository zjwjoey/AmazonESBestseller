"""Derived task manifests and operator-facing category summaries."""
from __future__ import annotations

from collections import defaultdict
import csv
from pathlib import Path
from typing import Mapping

from .checkpoint import write_json_atomic


def merge_records(output: Path, all_rankings: list, all_details: list) -> None:
    ranking_map = {}
    for row in all_rankings:
        key = (row.get("ranking_source_url"), row.get("ranking_page_number"),
               row.get("bestseller_rank"), str(row.get("asin") or "").upper())
        if key[3]:
            ranking_map.setdefault(key, row)
    detail_map = {}
    for row in all_details:
        asin = str(row.get("asin") or "").upper()
        if asin:
            detail_map.setdefault(asin, row)
    write_json_atomic(output / "rankings.json", list(ranking_map.values()))
    write_json_atomic(output / "details.json", list(detail_map.values()))


def write_summary(output: Path, categories: list[Mapping], rankings: list,
                  details: list, selected: Mapping[str, list] | None,
                  statuses: Mapping[str, Mapping]) -> None:
    by_group = defaultdict(list)
    by_detail = defaultdict(set)
    for row in rankings:
        group = str(row.get("research_category") or "")
        if group:
            by_group[group].append(row)
    for row in details:
        asin = str(row.get("asin") or "").upper()
        if asin:
            by_detail[asin].add(asin)
    selected = selected or {}
    output.mkdir(parents=True, exist_ok=True)
    fields = ["采集研究类目", "目标SKU", "原始榜单记录", "类目内唯一ASIN", "详情记录", "最终保留", "使用榜单数量", "状态"]
    with (output / "category_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for category in categories:
            group = category["research_category"]
            rows = by_group[group]
            asins = {str(row.get("asin") or "").upper() for row in rows if row.get("asin")}
            sources = {str(row.get("ranking_source_url") or "") for row in rows if row.get("ranking_source_url")}
            final_count = len(selected.get(group, []))
            target = int(category["target_unique"])
            writer.writerow({"采集研究类目": group, "目标SKU": target,
                             "原始榜单记录": len(rows), "类目内唯一ASIN": len(asins),
                             "详情记录": len(asins & set(by_detail)), "最终保留": final_count,
                             "使用榜单数量": len(sources),
                             "状态": "PASS" if final_count == target else statuses.get(group, {}).get("status", "INCOMPLETE")})


__all__ = ["merge_records", "write_summary"]
