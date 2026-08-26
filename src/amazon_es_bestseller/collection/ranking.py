# -*- coding: utf-8 -*-
"""畅销榜页面纯解析（离线）。

bestseller_rank 只取显式徽章 ``span.a-badge-text``（QA_RULES §11）：
无徽章 → None，DOM 顺序单独存 ``index``，绝不把第 N 行当 Amazon 第 N 名。
同一 ASIN 出现在多个榜单页时保留多条记录（§7），不去重。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import List, Mapping, Optional

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status


def parse_bestsellers_page(html: str, source_url: str, collected_at: str) -> list[dict]:
    """畅销榜页 HTML → 排行榜记录列表（每 ASIN × 页面一行）。"""
    soup = BeautifulSoup(html, "lxml")
    records = []
    for i, item in enumerate(soup.select("#gridItemRoot")):
        a = item.select_one('a[href*="/dp/"]')
        if a is None:
            continue
        m = re.search(r"/dp/([A-Z0-9]{10})", a.get("href", ""), re.I)
        if not m:
            continue
        badge = item.select_one("span.a-badge-text")
        rank = None
        if badge is not None:
            bm = re.match(r"#\s*(\d+)", badge.get_text(" ", strip=True))
            if bm:
                rank = int(bm.group(1))
        records.append({
            "index": i,
            "asin": m.group(1).upper(),
            "bestseller_rank": rank,
            "ranking_source_url": source_url,
            "collected_at": collected_at,
        })
    return records


def collect_rankings(urls: List[str], session, out_dir: str) -> List[dict]:
    """串行采集榜单页：原始 HTML 落盘 runs/YYYYMMDD_HHMMSS/html/ + rankings.json。

    需要 BrowserSession（playwright 仅在 __enter__ 时导入）；联网仅发生在
    调用本函数时。页间显式延迟，无重试、无 stealth。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(str(out_dir), "runs", stamp)
    html_dir = os.path.join(run_dir, "html")
    os.makedirs(html_dir, exist_ok=True)

    records: List[dict] = []
    collected_at = datetime.now().isoformat(timespec="seconds")
    for i, url in enumerate(urls):
        status = session.goto(url)
        session.wait_between_requests()
        html = session.page.content()
        with open(os.path.join(html_dir, "ranking_%02d.html" % i), "w", encoding="utf-8") as f:
            f.write(html)
        for r in parse_bestsellers_page(html, url, collected_at):
            r["status_code"] = status
            r["access_state"] = detect_access_status(status, html).value
            records.append(r)
    with open(os.path.join(run_dir, "rankings.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return records
