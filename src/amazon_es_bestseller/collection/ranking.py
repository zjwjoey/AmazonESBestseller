# -*- coding: utf-8 -*-
"""畅销榜页面纯解析（离线）。

bestseller_rank 只取显式徽章（旧 ``span.a-badge-text`` / 现代 ``span.zg-bdg-text``，
QA_RULES §11）：无徽章 → None，DOM 顺序单独存 ``index``，绝不把第 N 行当
Amazon 第 N 名。同一 ASIN 出现在多个榜单页时保留多条记录（§7），不去重。

类目为一等字段（B1，QA_RULES §6/§13）：browse_node_id 取自榜单 URL 的
``/zgbs/<NODE>``（URL 缺失时回退到面包屑最深类目链接）；category_l1..l3 /
leaf_category 取自页面面包屑的节点类目路径（主源 = 榜单节点，绝不从标题
或详情 BSR 臆造）。无面包屑 → 类目全 None（缺失即 null）。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import List, Mapping, Optional

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status, require_normal_access
from ..normalization.category import category_levels

#: 榜单 URL 节点号：旧式 /zgbs/<NODE> 或现代 /gp/bestsellers/<slug>/<NODE>/
_ZGBS_NODE_RE = re.compile(r"/zgbs/(\d+)")
_NODE_URL_RE = re.compile(r"/gp/bestsellers/[^/\"']+/(\d+)")

#: 面包屑容器候选（旧结构回退，优先级从高到低）
_BREADCRUMB_SELECTORS = ("#zg_browseRoot", "#browseNodeCrumbs", "ol.zg_hrsr")

#: 面包屑根链接文本（不是类目层级，剔除）
_BREADCRUMB_SKIP = {
    "los más vendidos", "más vendidos", "los mas vendidos", "mas vendidos",
    "best sellers", "best-sellers", "cualquier departamento",
}

#: 现代类目层级链：unv_ 链接（父级类目）+ h1 当前类目标题
_UNV_SEL = 'a[href*="zg_bs_unv_"]'
_H1_CURRENT_RE = re.compile(r"Los más vendidos en\s+(.+)$", re.I)


def _category_trail_modern(soup) -> list:
    """现代页面类目路径：unv 父级链 + h1 当前类目（无证据 → []，回退旧结构）。"""
    trail: list = []
    last = None
    for a in soup.select(_UNV_SEL):
        name = a.get_text(" ", strip=True)
        if not name or name.lower() in _BREADCRUMB_SKIP:
            continue
        if name.lower() == last:
            continue
        last = name.lower()
        trail.append(name)
    # 当前类目：类目标题 "Los más vendidos en X"（页头导航 h1 是 "…de Amazon"，跳过）
    for h1 in soup.select("h1"):
        m = _H1_CURRENT_RE.search(h1.get_text(" ", strip=True))
        if m:
            cur = m.group(1).strip()
            if cur and cur.lower() not in {t.lower() for t in trail}:
                trail.append(cur)
            break
    return trail


def _extract_category_trail(soup) -> list[str]:
    """类目路径：现代 unv+h1 优先，旧 ``/zgbs/`` 面包屑回退（无证据 → []）。

    剔除根链接（"Los más vendidos" / "Cualquier departamento"）与连续重复。
    """
    modern = _category_trail_modern(soup)
    if modern:
        return modern
    container = None
    for sel in _BREADCRUMB_SELECTORS:
        container = soup.select_one(sel)
        if container is not None:
            break
    if container is None:
        return []
    trail: list[str] = []
    last = None
    for a in container.select('a[href*="/zgbs/"]'):
        name = a.get_text(" ", strip=True)
        if not name or name.lower() in _BREADCRUMB_SKIP:
            continue
        if name.lower() == last:
            continue  # 连续重复（当前页/父级），去重
        last = name.lower()
        trail.append(name)
    return trail


def _browse_node_id(source_url, soup) -> Optional[str]:
    """榜单节点号：URL 旧式/现代节点号优先；否则取面包屑最深类目链接节点。"""
    if source_url:
        m = _ZGBS_NODE_RE.search(str(source_url))
        if m:
            return m.group(1)
        m = _NODE_URL_RE.search(str(source_url))
        if m:
            return m.group(1)
    container = None
    for sel in _BREADCRUMB_SELECTORS:
        container = soup.select_one(sel)
        if container is not None:
            break
    if container is None:
        return None
    node = None
    for a in container.select('a[href*="/zgbs/"]'):
        m = _ZGBS_NODE_RE.search(a.get("href", ""))
        if m:
            node = m.group(1)  # 最后一个（最深）类目链接的节点
    return node


def parse_bestsellers_page(html: str, source_url: str, collected_at: str) -> list[dict]:
    """畅销榜页 HTML → 排行榜记录列表（每 ASIN × 页面一行）。

    页面级榜单上下文（browse_node_id / category_l1..l3 / leaf_category）
    解析一次并盖章到每条记录；同一页记录共享同一节点类目上下文。
    """
    soup = BeautifulSoup(html, "lxml")
    l1, l2, l3, leaf = category_levels(_extract_category_trail(soup))
    browse_node = _browse_node_id(source_url, soup)
    records = []
    for i, item in enumerate(soup.select("#gridItemRoot")):
        a = item.select_one('a[href*="/dp/"]')
        if a is None:
            continue
        m = re.search(r"/dp/([A-Z0-9]{10})", a.get("href", ""), re.I)
        if not m:
            continue
        badge = item.select_one("span.a-badge-text, span.zg-bdg-text")
        rank = None
        if badge is not None:
            bm = re.match(r"#\s*(\d+)", badge.get_text(" ", strip=True))
            if bm:
                rank = int(bm.group(1))
        records.append({
            "index": i,
            "asin": m.group(1).upper(),
            "category_l1": l1,
            "category_l2": l2,
            "category_l3": l3,
            "leaf_category": leaf,
            "browse_node_id": browse_node,
            "bestseller_rank": rank,
            "ranking_source_url": source_url,
            "collected_at": collected_at,
        })
    return records


def collect_rankings(urls: List[str], session, out_dir: str) -> List[dict]:
    """串行采集榜单页：原始 HTML 落盘 runs/YYYYMMDD_HHMMSS/html/ + rankings.json。

    需要 BrowserSession（playwright 仅在 __enter__ 时导入）；联网仅发生在
    调用本函数时。页间显式延迟，无重试、无 stealth。

    访问门禁（ARCHITECTURE §6）：受限页 HTML 先落盘保留证据，随即抛
    AccessStopError，rankings.json 不写出（不产出不完整榜单数据）。
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
            f.write(html)  # 先保留证据，再判定访问状态
        state = detect_access_status(status, html)
        require_normal_access(state, "HTTP %s，榜单页 %s，已采 %d 页"
                              % (status, url, len(records)))
        for r in parse_bestsellers_page(html, url, collected_at):
            r["status_code"] = status
            r["access_state"] = state.value
            records.append(r)
    with open(os.path.join(run_dir, "rankings.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return records
