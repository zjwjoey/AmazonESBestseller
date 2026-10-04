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
import uuid
from datetime import datetime
from typing import Callable, List, Mapping, Optional
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..access.detector import AccessStopError, detect_access_status, require_normal_access
from ..normalization.category import category_levels
from ..monitoring.ranking_identity.adapters import extract_server_candidates, select_ranking_cards

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
_MONTHLY_RE = re.compile(
    r"([\d.,]+\s*(?:mil|k)?\s*\+)\s+comprados\s+el\s+mes\s+pasado", re.I)


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


def _ranking_page_number(source_url: str) -> int:
    """Return Amazon's explicit ``pg`` value, defaulting to page 1.

    The value is source metadata only.  It must never be used to fabricate a
    rank when Amazon omits a visible badge.
    """
    try:
        value = parse_qs(urlsplit(str(source_url or "")).query).get("pg", ["1"])[0]
        page = int(value)
        return page if page >= 1 else 1
    except (TypeError, ValueError):
        return 1


def _ranking_source_type(source_url: str, browse_node: Optional[str]) -> str:
    """Classify the ranking URL without guessing a category name."""
    if browse_node:
        return "subcategory"
    path = urlsplit(str(source_url or "")).path.rstrip("/")
    if re.search(r"/gp/bestsellers/[^/]+(?:/ref=[^/]+)?$", path):
        return "top_level"
    return "unknown"


def _monthly_bought_raw(item) -> str:
    text = item.get_text(" ", strip=True)
    match = _MONTHLY_RE.search(text)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _ranking_link_asin(raw_url: str) -> Optional[str]:
    """Extract the ASIN from the raw product href independently of card data."""
    # Keep the historical fixture-compatible prefix capture: real ASINs are
    # ten characters, while a few saved offline synthetic hrefs append a
    # suffix.  The complete href remains untouched in raw evidence.
    match = re.search(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})",
                      str(raw_url or ""), re.I)
    return match.group(1).upper() if match else None


def _normalize_ranking_product_url(raw_url: str, link_asin: Optional[str]) -> str:
    """Return a safe canonical product URL without changing raw evidence."""
    if not raw_url:
        return ""
    if link_asin:
        return "https://www.amazon.es/dp/%s" % link_asin
    absolute = urljoin("https://www.amazon.es", str(raw_url))
    return absolute.split("?", 1)[0].split("#", 1)[0]


class RankingCollectionResult(list):
    """Backward-compatible list carrying explicit run/page evidence."""

    def __init__(self, records, run_dir, page_statuses):
        super().__init__(records)
        self.run_dir = str(run_dir)
        self.page_statuses = list(page_statuses)

    @property
    def records(self):
        return list(self)


def parse_bestsellers_page(html: str, source_url: str, collected_at: str) -> list[dict]:
    """畅销榜页 HTML → 排行榜记录列表（每 ASIN × 页面一行）。

    页面级榜单上下文（browse_node_id / category_l1..l3 / leaf_category）
    解析一次并盖章到每条记录；同一页记录共享同一节点类目上下文。
    """
    soup = BeautifulSoup(html, "lxml")
    category_trail = _extract_category_trail(soup)
    l1, l2, l3, leaf = category_levels(category_trail)
    browse_node = _browse_node_id(source_url, soup)
    source_category = category_trail[-1] if category_trail else None
    source_category_path = " > ".join(category_trail) if category_trail else None
    page_number = _ranking_page_number(source_url)
    source_type = _ranking_source_type(source_url, browse_node)
    records = []
    cards = select_ranking_cards(soup, source_url)
    candidates = extract_server_candidates(html, source_url=source_url,
                                           page_number=page_number)
    for candidate in candidates:
        i = candidate.get("card_index", len(records))
        raw_product_url = str(candidate.get("raw_href") or "")
        link_asin = candidate.get("href_asin") or None
        card_asin = str(candidate.get("card_asin") or "")
        asin = str(candidate.get("asin") or "")
        # Preserve the historical ranking-parser compatibility for synthetic
        # saved hrefs that append a suffix after the ten-character ASIN.  The
        # new identity snapshot remains strict and will not treat such a URL
        # as a confirmed product href.
        if not link_asin and raw_product_url:
            link_asin = _ranking_link_asin(raw_product_url)
        if not asin and link_asin:
            asin = link_asin
        if not asin:
            continue
        link_status = (
            "NO_PRODUCT_URL" if not raw_product_url else
            "NO_ASIN_IN_URL" if not link_asin else
            "MATCH" if link_asin == asin else "MISMATCH")
        rank = candidate.get("rank")
        rank_raw = candidate.get("rank_raw")
        record = {
            "index": i,
            "asin": asin,
            "ranking_asin": asin,
            "ranking_asin_source": (
                "CARD_DATA_ASIN" if card_asin else "PRODUCT_URL_ASIN"
                if candidate.get("asin_source") == "PRODUCT_HREF_ASIN"
                else candidate.get("asin_source") or "PRODUCT_URL_ASIN"),
            "category_l1": l1,
            "category_l2": l2,
            "category_l3": l3,
            "leaf_category": leaf,
            "browse_node_id": browse_node,
            "bestseller_rank": rank,
            "bestseller_rank_raw": rank_raw,
            "ranking_source_url": source_url,
            "ranking_source_type": source_type,
            "ranking_source_category": source_category,
            "ranking_source_category_path": source_category_path,
            "ranking_page_number": page_number,
            "collected_at": collected_at,
            "ranking_product_url_raw": raw_product_url or "",
            "ranking_product_url_normalized": _normalize_ranking_product_url(
                raw_product_url or "", link_asin),
            "ranking_link_asin": link_asin,
            "ranking_link_identity_status": (
                "LINK_ASIN_MISMATCH" if link_status == "MISMATCH" else link_status),
        }
        record["ranking_rank"] = rank
        record["ranking_rank_raw"] = rank_raw
        if isinstance(i, int) and 0 <= i < len(cards):
            monthly = _monthly_bought_raw(cards[i])
            if monthly:
                record["monthly_bought_raw"] = monthly
        records.append(record)
    return records


def collect_rankings(urls: List[str], session, out_dir: str, pages_per_url: int = 1,
                    should_stop: Optional[Callable[[], bool]] = None,
                    run_dir: Optional[str] = None) -> List[dict]:
    """串行采集榜单页：原始 HTML 落盘 runs/YYYYMMDD_HHMMSS/html/ + rankings.json。

    需要 BrowserSession（playwright 仅在 __enter__ 时导入）；联网仅发生在
    调用本函数时。页间显式延迟，无重试、无 stealth。

    访问门禁（ARCHITECTURE §6）：受限页 HTML 先落盘保留证据，随即抛
    AccessStopError，rankings.json 不写出（不产出不完整榜单数据）。
    """
    # Real BrowserSession instances enforce the delivery-location invariant;
    # offline fake sessions intentionally omit this method.
    from ..access.location import ensure_spain_delivery
    ensure_spain_delivery(session)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = run_dir or os.path.join(str(out_dir), "runs", stamp + "_" + uuid.uuid4().hex[:8])
    html_dir = os.path.join(run_dir, "html")
    pages_dir = os.path.join(run_dir, "pages")
    os.makedirs(html_dir, exist_ok=True)
    os.makedirs(pages_dir, exist_ok=True)

    if int(pages_per_url) < 1:
        raise ValueError("pages_per_url must be >= 1")
    records: List[dict] = []
    collected_at = datetime.now().isoformat(timespec="seconds")
    page_index = 0
    page_statuses: List[dict] = []

    def persist_page(status_row: dict, page_records: list[dict]) -> None:
        page_statuses.append(status_row)
        with open(os.path.join(pages_dir, "page_%03d.json" % (len(page_statuses) - 1),),
                  "w", encoding="utf-8") as handle:
            json.dump({"status": status_row, "records": page_records},
                      handle, ensure_ascii=False, indent=2)
        temporary = os.path.join(run_dir, "page_statuses.json.tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(page_statuses, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, os.path.join(run_dir, "page_statuses.json"))

    for url in urls:
        for page_no in range(1, int(pages_per_url) + 1):
            page_url = url if page_no == 1 else (url + ("&" if "?" in url else "?") + "pg=%d" % page_no)
            status_row = {"source_url": str(url), "page_number": page_no,
                          "page_url": page_url, "access_state": "UNKNOWN",
                          "http_status": None, "parse_status": "NOT_STARTED",
                          "parsed_record_count": 0, "error": ""}
            page_records: list[dict] = []
            if should_stop is not None and should_stop():
                status_row.update(access_state="BLOCKED", parse_status="ACCESS_BLOCKED",
                                  error="其他工作槽触发访问限制")
                persist_page(status_row, page_records)
                raise AccessStopError("其他工作槽触发访问限制，停止新的榜单请求")
            try:
                status = session.goto(page_url)
                status_row["http_status"] = status
                session.wait_between_requests()
                # Capture the initial shell first.  Root bestseller pages may only
                # contain ranks 1--30 until the browser scrolls; trigger lazy
                # loading on a normal page before taking the authoritative HTML
                # snapshot.  Fake/offline sessions do not implement the helper.
                initial_html = session.page.content()
                initial_state = detect_access_status(status, initial_html)
                html = initial_html
                if initial_state.value == "NORMAL":
                    load_lazy = getattr(session, "load_lazy_ranking_content", None)
                    if callable(load_lazy):
                        load_lazy()
                        html = session.page.content()
                html_path = os.path.join(html_dir, "ranking_%03d.html" % page_index)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)  # 先保留证据，再判定访问状态
                page_index += 1
                state = detect_access_status(status, html)
                from ..access.challenge import maybe_wait_for_challenge
                original_html = html
                state, html, recovered = maybe_wait_for_challenge(session, state, html, status)
                if original_html != html and state.value == "NORMAL":
                    with open(html_path + ".challenge", "w", encoding="utf-8") as f:
                        f.write(original_html)
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(html)
                status_row.update(access_state=state.value,
                                  initial_access_state=initial_state.value,
                                  recovered_from_challenge=bool(recovered))
                require_normal_access(state, "HTTP %s，榜单页 %s，已采 %d 页"
                                      % (status, page_url, page_index - 1))
                page_records = parse_bestsellers_page(html, page_url, collected_at)
                for r in page_records:
                    r["status_code"] = status
                    r["initial_access_state"] = initial_state.value
                    r["access_state"] = state.value
                    r["recovered_from_challenge"] = recovered
                    records.append(r)
                status_row.update(parse_status="PARSE_OK" if page_records else "PARSE_EMPTY",
                                  parsed_record_count=len(page_records))
            except AccessStopError as exc:
                status_row.setdefault("access_state", "UNKNOWN")
                status_row["parse_status"] = "ACCESS_BLOCKED"
                status_row["error"] = str(exc)
                persist_page(status_row, page_records)
                raise
            except Exception as exc:
                status_row["parse_status"] = "PARSER_ERROR" if "parse" in str(exc).lower() else "NETWORK_ERROR"
                status_row["error"] = str(exc)
                persist_page(status_row, page_records)
                raise
            persist_page(status_row, page_records)
    with open(os.path.join(run_dir, "rankings.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    # Identity extraction is deliberately performed in the same offline step
    # after HTML evidence is persisted.  The collector remains backward
    # compatible: legacy rankings.json is unchanged, while downstream detail
    # planning can consume this immutable identity artifact directly.
    try:
        from ..monitoring.ranking_identity.extract import extract_identity_from_evidence
        identity_result = extract_identity_from_evidence(html_dir)
        with open(os.path.join(run_dir, "identity.json"), "w", encoding="utf-8") as f:
            json.dump(identity_result["records"], f, ensure_ascii=False, indent=2)
        with open(os.path.join(run_dir, "identity_audit.json"), "w", encoding="utf-8") as f:
            json.dump(identity_result["audit"], f, ensure_ascii=False, indent=2)
    except Exception as exc:
        with open(os.path.join(run_dir, "identity_extraction_error.json"), "w", encoding="utf-8") as f:
            json.dump({"error": str(exc), "identity_parser_version": "ranking_identity_v1"},
                      f, ensure_ascii=False, indent=2)
    return RankingCollectionResult(records, run_dir, page_statuses)
