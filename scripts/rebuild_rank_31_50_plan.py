# -*- coding: utf-8 -*-
"""Rebuild the 31--50 extraction plan from the verified 4,500-SKU evidence."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re


ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / "outputs" / "scale_4500_repair" / "trace_enriched"
RANKINGS = TRACE / "rankings_4500_trace.json"
ROOT_SUPPLEMENT = TRACE / "ranking_supplement_all_categories_31_50.json"
LOCAL_OUTPUTS = ROOT / "outputs"
CONFIG = ROOT / "configs" / "amazon_es_4500sku_categories.json"
OUT_CONFIG = ROOT / "configs" / "amazon_es_4500_rank_31_50_completion_plan.json"
OUT_MD = ROOT / "outputs" / "scale_4500_repair" / "rank_31_50_completion_plan.md"
OUT_PENDING = ROOT / "outputs" / "scale_4500_repair" / "rank_31_50_pending_urls.txt"

CATEGORY_ZH = {
    "car": "汽车与摩托车用品", "baby": "母婴用品", "beauty": "美妆",
    "electronics": "电子产品", "garden": "花园及户外家居",
    "grocery": "食品与饮料", "health": "健康与个人护理",
    "hogar": "家居与厨房", "lighting": "照明", "luggage": "箱包及旅行用品",
    "office": "办公及文具", "pets": "宠物用品", "sports": "运动与户外",
    "diy": "家装与工具", "toys": "玩具与游戏",
}


def page1_url(raw: str) -> str:
    """Canonicalize equivalent Amazon ranking URLs for plan identity."""
    parts = urlsplit(str(raw or "").strip())
    # Browser/ref tracking suffixes are not a different ranking source.
    path = re.sub(r"/ref=[^/]+/?$", "/", parts.path or "/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() != "pg"]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       urlencode(query), ""))


URL_GROUP = {
    "automotive": "car", "baby": "baby", "beauty": "beauty",
    "electronics": "electronics", "lawn-garden": "garden",
    "grocery": "grocery", "hpc": "health", "kitchen": "hogar",
    "lighting": "lighting", "luggage": "luggage", "office": "office",
    "pet-supplies": "pets", "sports": "sports", "tools": "diy",
    "toys": "toys",
}


def infer_group(url: str) -> str | None:
    parts = urlsplit(url)
    bits = [b for b in parts.path.split("/") if b]
    return URL_GROUP.get(bits[bits.index("bestsellers") + 1]) if "bestsellers" in bits and len(bits) > bits.index("bestsellers") + 1 else None


def iter_local_ranking_files():
    """Include every saved 300-candidate ranking evidence file, not only trace."""
    for path in LOCAL_OUTPUTS.rglob("*.json"):
        name = path.name.lower()
        if not name.startswith("rankings") or "_300_" not in str(path).lower():
            continue
        yield path


def main() -> None:
    rankings = json.loads(RANKINGS.read_text(encoding="utf-8"))
    supplement = json.loads(ROOT_SUPPLEMENT.read_text(encoding="utf-8"))
    category_config = json.loads(CONFIG.read_text(encoding="utf-8"))["categories"]
    category_meta = {row["category_group"]: row for row in category_config}

    completed_by_url = defaultdict(list)
    for row in supplement:
        completed_by_url[page1_url(row.get("ranking_source_url"))].append(row)

    grouped = defaultdict(list)
    for row in rankings:
        grouped[page1_url(row.get("ranking_source_url"))].append(row)
    # The trace is not the complete historical extraction manifest. Merge all
    # locally saved 300-SKU ranking evidence and keep the first useful record
    # as provenance for each canonical source URL.
    for path in iter_local_ranking_files():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = value.get("records", []) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("ranking_source_url"):
                grouped[page1_url(row.get("ranking_source_url"))].append(row)
    # Every approved top-level category is an intentional source, even when
    # its old raw files were not retained locally.
    for meta in category_config:
        grouped.setdefault(page1_url(meta["url"]), [{
            "ranking_source_url": meta["url"],
            "category_group": meta["category_group"],
            "ranking_source_type": "top_level",
        }])

    category_order = {row["category_group"]: i for i, row in enumerate(category_config, 1)}
    sources = []
    for url, rows in grouped.items():
        sample = next((r for r in rows if isinstance(r, dict)), {})
        group = sample.get("category_group") or infer_group(url) or "unknown"
        page1_rows = [r for r in rows if int(r.get("ranking_page_number") or 1) == 1]
        page2_rows = [r for r in rows if int(r.get("ranking_page_number") or 1) == 2]
        captured = completed_by_url.get(url, [])
        is_luggage_root = url.rstrip("/") == "https://www.amazon.es/gp/bestsellers/luggage"
        if len(captured) == 20:
            status = "completed"
            status_reason = "已通过滚动采集并保存31–50原始HTML证据"
        elif is_luggage_root:
            status = "source_missing"
            status_reason = "Amazon页面明确显示该类目暂无畅销商品"
        else:
            status = "pending"
            status_reason = "旧采集未触发31–50懒加载"
        ranks = [int(r["bestseller_rank"]) for r in rows
                 if r.get("bestseller_rank") is not None]
        meta = category_meta.get(group, {})
        sources.append({
            "sequence": 0,
            "category_sequence": category_order.get(group),
            "category_group": group,
            "category_name_zh": CATEGORY_ZH.get(group, group),
            "category_name_es": meta.get("category_name"),
            "source_url": url,
            "ranking_source_type": sample.get("ranking_source_type"),
            "ranking_source_category": sample.get("ranking_source_category"),
            "ranking_source_category_path": sample.get("ranking_source_category_path"),
            "browse_node_id": sample.get("browse_node_id"),
            "historical_selected_records": len(rows),
            "historical_page1_records": len(page1_rows),
            "historical_page2_records": len(page2_rows),
            "historical_rank_min": min(ranks) if ranks else None,
            "historical_rank_max": max(ranks) if ranks else None,
            "target_rank_start": 31,
            "target_rank_end": 50,
            "target_record_count": 20,
            "status": status,
            "status_reason": status_reason,
            "output_dir": "outputs/scale_4500_repair/rank_31_50_completion/%s" % group,
        })

    sources.sort(key=lambda r: (
        r.get("category_sequence") or 999,
        0 if r.get("ranking_source_type") == "top_level" else 1,
        str(r.get("ranking_source_category_path") or ""),
        r["source_url"],
    ))
    per_category_counter = defaultdict(int)
    for sequence, row in enumerate(sources, 1):
        row["sequence"] = sequence
        per_category_counter[row["category_group"]] += 1
        row["source_sequence_in_category"] = per_category_counter[row["category_group"]]

    counts = defaultdict(int)
    for row in sources:
        counts[row["status"]] += 1
    plan = {
        "task_id": "amazon_es_4500_rank_31_50_completion",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "补齐原4,500 SKU实际榜单来源页滚动后显示的31–50名",
        "execution_mode": "serial",
        "category_count": len({r["category_group"] for r in sources}),
        "source_page_count": len(sources),
        "target_rank_range": [31, 50],
        "target_records_per_source": 20,
        "maximum_ranking_records": len(sources) * 20,
        "cooldown_between_categories_seconds": 1800,
        "normal_per_request_pacing": True,
        "dedupe_identity": "ASIN",
        "stop_on_access_signals": [
            "CHALLENGE", "HTTP_403", "HTTP_429", "ROBOT_CHECK",
            "CAPTCHA", "ACCESS_DENIED",
        ],
        "resume_policy": "优先使用已保存HTML和检查点，不重复请求已完成来源页",
        "summary": {
            "completed_sources": counts["completed"],
            "pending_sources": counts["pending"],
            "source_missing_sources": counts["source_missing"],
            "pending_maximum_ranking_records": counts["pending"] * 20,
        },
        "sources": sources,
    }
    OUT_CONFIG.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_PENDING.write_text("\n".join(
        row["source_url"] for row in sources if row["status"] == "pending"
    ) + "\n", encoding="utf-8")

    lines = [
        "# Amazon.es 31–50 补抓计划",
        "",
        "- 实际来源页：%d" % len(sources),
        "- 已完成：%d" % counts["completed"],
        "- 待提取：%d" % counts["pending"],
        "- 来源为空：%d" % counts["source_missing"],
        "- 待提取榜单记录上限：%d" % (counts["pending"] * 20),
        "- 执行方式：串行；类目间冷却 1,800 秒；保留正常请求间隔和访问门禁",
        "",
        "|序号|类目|来源页数|已完成|待提取|来源为空|待提取记录上限|",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for group, _ in sorted(category_order.items(), key=lambda item: item[1]):
        subset = [r for r in sources if r["category_group"] == group]
        complete = sum(r["status"] == "completed" for r in subset)
        pending = sum(r["status"] == "pending" for r in subset)
        missing = sum(r["status"] == "source_missing" for r in subset)
        lines.append("|%d|%s|%d|%d|%d|%d|%d|" % (
            category_order[group], CATEGORY_ZH.get(group, group),
            len(subset), complete, pending, missing, pending * 20,
        ))
    lines.extend(["", "## 逐来源提取清单", "",
                  "|总序号|类目|状态|第一页来源 URL|旧第一页记录|旧第二页记录|目标|",
                  "|---:|---|---|---|---:|---:|---|"])
    for row in sources:
        lines.append("|%d|%s|%s|%s|%d|%d|31–50（20条）|" % (
            row["sequence"], CATEGORY_ZH.get(row["category_group"], row["category_group"]),
            row["status"], row["source_url"], row["historical_page1_records"],
            row["historical_page2_records"],
        ))
    lines.extend([
        "", "## 程序改动审查清单", "",
        "|文件|改动|审查重点|", "|---|---|---|",
        "|src/amazon_es_bestseller/access/browser.py|新增有限次串行下滑并等待卡片集合稳定|不绕过挑战；滚动只触发懒加载|",
        "|src/amazon_es_bestseller/collection/ranking.py|正常页先检测，再滚动后保存最终HTML；继续只按可见徽章解析排名|31–50不能由DOM序号或Detail BSR推断|",
        "|src/amazon_es_bestseller/cli.py|新增batch-collect，按类目分批、断点续跑、类目间倒计时|进程保持运行；默认冷却1800秒|",
        "|src/amazon_es_bestseller/cli.py|合并已有商品/详情/31–50种子；失败详情挂起重试；来源短缺不标完成；冷却截止时间持久化|断点续跑不会丢证据或跳过失败ASIN|",
        "|configs/amazon_es_4500_rank_31_50_completion_plan.json|合并追溯到的本地来源页计划（%d页）|%d待提取、%d已完成、%d来源为空、目标31–50|" % (len(sources), counts["pending"], counts["completed"], counts["source_missing"]),
        "|scripts/run_rank_31_50_completion.ps1|一键启动入口|设置PYTHONPATH、邮编、人工接管和1800秒冷却|",
        "|tests/test_batch_collect.py|批处理命令和倒计时离线回归测试|不访问Amazon|",
        "", "程序不会在类目间退出；只有完成全部待提取类目或遇到挑战/403/429等访问门禁时才结束。",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "source_page_count": len(sources),
        "completed": counts["completed"],
        "pending": counts["pending"],
        "source_missing": counts["source_missing"],
        "pending_maximum_ranking_records": counts["pending"] * 20,
        "config": str(OUT_CONFIG),
        "summary": str(OUT_MD),
        "pending_urls": str(OUT_PENDING),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
