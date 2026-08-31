# -*- coding: utf-8 -*-
"""商品详情页纯解析（离线）：选择器与 extract_details.js 完全一致。

challenge 页绝不空解析（ARCHITECTURE §67）：is_captcha 标记交给上层，本层照常
返回字段（缺省为空字符串），由 collect_details 决定是否保存。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import List, Optional

from bs4 import BeautifulSoup

from ..access.detector import (AccessStopError, CAPTCHA_RE, detect_access_status,
                               require_normal_access)
from ..models import AccessState
from .checkpoints import write_checkpoint

CURRENT_DETAIL_SCHEMA_VERSION = 2


def _clean(t) -> str:
    """对齐 JS clean()：压空白、去首尾。"""
    return re.sub(r"\s+", " ", (t or "")).strip()


def _text(soup, sel) -> str:
    el = soup.select_one(sel)
    return _clean(el.get_text(" ", strip=True)) if el is not None else ""


# ---------- B4 低成本扩展：parent ASIN / 首次上架日期 / 图片 ----------

_ASIN_RE = re.compile(r"[A-Z0-9]{10}", re.I)   # flags 编译进正则，调用时不传
_PARENT_ASIN_SELECTORS = ("input#parentASIN", 'input[name="parentASIN"]')
_PARENT_ASIN_JSON_RE = re.compile(
    r"[\"']parentAsin[\"']\s*:\s*[\"']([A-Z0-9]{10})[\"']", re.I)

# 首次上架日期（西语标签；raw 归一为 parse_es_date 接受的 "D M YYYY"）
_AVAIL_DATE_RE = re.compile(
    r"(?:fecha de primera disponibilidad|primera fecha disponible|fecha de lanzamiento|"
    r"producto en amazon\.es desde)"
    r"\s*:?\s*(\d{1,2})\s+(?:de\s+)?([a-záéíóúñü]+)[,\s]*(?:de\s+)?(\d{4})",
    re.I)
_DATE_SECTIONS = ("#detailBulletsWrapper_feature_div", "#detailBullets_feature_div",
                  "#productDetails_detailBullets_sections1", "#prodDetails")

_IMAGE_SELECTORS = ("#landingImage", "#imgBlkFront", "#imageBlock_feature_div img")
_DETAIL_CATEGORY_SKIP = {
    "los más vendidos", "más vendidos", "los mas vendidos", "mas vendidos",
    "best sellers", "best-sellers", "cualquier departamento",
}


def _detail_category_trail(soup) -> list[str]:
    """Visible product breadcrumb, root → leaf, as raw Spanish evidence."""
    container = soup.select_one("#wayfinding-breadcrumbs_feature_div")
    if container is None:
        return []
    trail = []
    seen = set()
    for a in container.select("a"):
        name = _clean(a.get_text(" ", strip=True))
        key = name.casefold()
        if not name or key in _DETAIL_CATEGORY_SKIP or key in seen:
            continue
        seen.add(key)
        trail.append(name)
    return trail


def _parent_asin(soup) -> str:
    """隐藏域 parentASIN → 10 位 ASIN；值不合法 → 空（缺失→空，不臆造）。"""
    for sel in _PARENT_ASIN_SELECTORS:
        el = soup.select_one(sel)
        if el is None:
            continue
        v = str(el.get("value") or "").strip()
        if _ASIN_RE.fullmatch(v):
            return v.upper()
    # Modern detail pages may expose the confirmed family ASIN in an explicit
    # page-state JSON object instead of the legacy hidden input.
    for match in _PARENT_ASIN_JSON_RE.finditer(str(soup)):
        v = match.group(1).strip()
        if _ASIN_RE.fullmatch(v):
            return v.upper()
    return ""


def _first_available_date_raw(soup) -> str:
    """详情页首次上架日期 → "D M YYYY"（供 parse_es_date 解析）；无 → 空。"""
    for sel in _DATE_SECTIONS:
        txt = re.sub(r"[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff]", "", _text(soup, sel))
        m = _AVAIL_DATE_RE.search(txt)
        if m:
            return "%s %s %s" % (m.group(1), m.group(2).lower(), m.group(3))
    return ""


def _main_image_url(soup) -> str:
    """主图 URL（src 优先，data-old-hires 回退）；无 http 图 → 空。"""
    for sel in _IMAGE_SELECTORS:
        img = soup.select_one(sel)
        if img is None:
            continue
        src = str(img.get("src") or "").strip()
        if src.startswith("http"):
            return src
        hi = str(img.get("data-old-hires") or "").strip()
        if hi.startswith("http"):
            return hi
    return ""


def _selected_variation(soup) -> str:
    """Extract explicitly selected variation values from legacy and modern twisters."""
    values = []

    for sel in ("#variation_name .selection",
                "#twister-plus-name-feature .selection",
                ".twister-plus-buying-options-price-data .selection"):
        el = soup.select_one(sel)
        if el is not None:
            value = _clean(el.get_text(" ", strip=True))
            if value:
                values.append(value)
                break

    # Modern swatch twisters mark selected values with a button class.  Restrict
    # IDs to variation dimensions so quantity/media controls cannot leak in.
    swatch_sel = ('#twister_feature_div span.a-button-selected[id^="color_name_"], '
                  '#twister_feature_div span.a-button-selected[id^="size_name_"], '
                  '#twister_feature_div span.a-button-selected[id^="style_name_"], '
                  '#twister_feature_div span.a-button-selected[id^="pattern_name_"]')
    for swatch in soup.select(swatch_sel):
        title = swatch.select_one('.swatch-title-text-display')
        if title is not None:
            value = _clean(title.get_text(" ", strip=True))
        else:
            image = swatch.select_one('img[alt]')
            value = _clean(image.get('alt')) if image is not None else ''
        if value:
            values.append(value)

    # Modern dropdown twisters expose the chosen option via ``dropdownSelect``
    # (and sometimes a selected attribute), scoped to native variation selects.
    for option in soup.select(
            'select[id^="native_dropdown_selected_"] option.dropdownSelect, '
            'select[id^="native_dropdown_selected_"] option[selected]'):
        value = _clean(option.get_text(" ", strip=True))
        if value:
            values.append(value)

    out = []
    seen = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return ' / '.join(out)


_MONTHLY_RE = re.compile(
    r"([\d.,]+\s*(?:mil|k)?\s*\+)\s+comprados\s+el\s+mes\s+pasado",
    re.I)


def _monthly_bought_raw(soup) -> str:
    """可见的上月购买文案 → 数字下限原文；无证据返回空。"""
    selectors = ("#social-proofing-faceout", ".social-proofing-faceout")
    texts = []
    for sel in selectors:
        for el in soup.select(sel):
            texts.append(_clean(el.get_text(" ", strip=True)))
    body = soup.find("body")
    if body is not None:
        texts.append(_clean(body.get_text(" ", strip=True)))
    for text in texts:
        m = _MONTHLY_RE.search(text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def _struck_price(soup) -> str:
    """只从明确 data-a-strike=true 的划线价格中取原价。"""
    primary = soup.select("#corePrice_feature_div .a-text-price, #corePriceDisplay_desktop_feature_div .a-text-price")
    containers = primary if primary else soup.select("#apex_price .a-text-price")
    for container in containers:
        classes = " ".join(container.get("class") or [])
        parent_classes = " ".join(container.parent.get("class") or []) if container.parent else ""
        if re.search(r"priceperunit|pricePerUnit", classes + " " + parent_classes, re.I):
            continue
        if re.search(r"basisprice", classes + " " + parent_classes, re.I):
            context = _clean(container.parent.parent.get_text(" ", strip=True)) if container.parent and container.parent.parent else ""
            if re.search(r"precio\s+(?:[úu]nico|por\s+unidad)|por\s+(?:kg|g|l|ml)\b", context, re.I):
                continue
        marked = container.get("data-a-strike")
        marked_parent = container.find_parent(attrs={"data-a-strike": "true"})
        offscreen = container.select_one(".a-offscreen")
        if offscreen is not None:
            value = _clean(offscreen.get_text(" ", strip=True))
            if str(marked).lower() == "true" or marked_parent is not None:
                return value
    return ""


def _product_url(asin: str) -> str:
    """商品链接：由 ASIN 确定性派生（非业务推断）；ASIN 非法 → 空。"""
    a = str(asin or "").strip()
    if _ASIN_RE.fullmatch(a):
        return "https://www.amazon.es/dp/%s" % a.upper()
    return ""


# ---------- 无损全量详情：Product Attribute 模型（DATA_MODEL §4-§8） ----------

_OVERVIEW_SEL = "#productOverview_feature_div"              # Resumen del producto（Key/Value 表）
_PROD_DETAILS_SEL = "#prodDetails"                           # Detalles técnicos（th/td 表）
_FEATURE_BULLETS_SEL = "#feature-bullets"                    # Acerca de este producto（卖点）
_PROD_DESC_SEL = "#productDescription_feature_div"           # Descripción del producto
_DETAIL_BULLETS_WRAPPER_SEL = "#detailBulletsWrapper_feature_div"   # 顶部细节子弹（n.º/日期等）
_ADDITIONAL_SELS = ("#detailBullets_feature_div",            # dt/dd 形式附加信息
                    "#productDetails_detailBullets_sections1")  # th/td 形式附加信息


def _attr_row(section: str, label: str, value: str, position: int, source: str) -> dict:
    """Product Attribute 单行（DATA_MODEL §4）：section/label_raw/value_raw/position/source。"""
    return {"section": section, "label_raw": label, "value_raw": value,
            "position": position, "source": source}


def _overview_attributes(soup) -> list:
    """product_overview：Resumen del producto 表，td.a-span3(label)+td.a-span9(value)。"""
    rows = []
    po = soup.select_one(_OVERVIEW_SEL)
    if po is None:
        return rows
    i = 0
    for tr in po.select("table tr"):
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        label = _clean(tds[0].get_text(" ", strip=True))
        value = _clean(tds[1].get_text(" ", strip=True))
        if label and value:
            rows.append(_attr_row("product_overview", label, value, i, "productOverview"))
            i += 1
    return rows


def _is_excluded_detail_table(table) -> bool:
    """旧版布局中 #prodDetails 嵌套的附加信息/反馈表 → 由其他 section 处理，跳过。"""
    tid = table.get("id") or ""
    return tid.startswith("productDetails_detailBullets") or "feedback" in tid


def _prod_details_attributes(soup) -> list:
    """technical_details：#prodDetails 下所有 th.prodDetSectionEntry+td.prodDetAttrValue 行。

    跳过嵌套的 ``productDetails_detailBullets*``（归 additional_information）与
    ``*feedback*``（客户反馈，非商品属性）表，避免同一行被两个 section 重复捕获。
    """
    rows = []
    pd = soup.select_one(_PROD_DETAILS_SEL)
    if pd is None:
        return rows
    i = 0
    for table in pd.select("table"):
        if _is_excluded_detail_table(table):
            continue
        for tr in table.select("tr"):
            th = tr.select_one("th")
            td = tr.select_one("td")
            if th is None or td is None:
                continue  # 表头行（colspan 无值）等非属性行
            label = _clean(th.get_text(" ", strip=True))
            value = _clean(td.get_text(" ", strip=True))
            if label and value:
                rows.append(_attr_row("technical_details", label, value, i, "prodDetails"))
                i += 1
    return rows


def _additional_attributes(soup) -> list:
    """additional_information：dt/dd 或 th/td 形式的附加信息（首个存在的 section）。"""
    rows = []
    for sel, mode in ((_ADDITIONAL_SELS[0], "dl"), (_ADDITIONAL_SELS[1], "table")):
        el = soup.select_one(sel)
        if el is None:
            continue
        i = 0
        if mode == "dl":
            for dt, dd in zip(el.select("dt"), el.select("dd")):
                label = _clean(dt.get_text(" ", strip=True))
                value = _clean(dd.get_text(" ", strip=True))
                if label and value:
                    rows.append(_attr_row("additional_information", label, value, i, "detailBullets"))
                    i += 1
        else:
            for tr in el.select("tr"):
                th = tr.select_one("th")
                td = tr.select_one("td")
                if th is None or td is None:
                    continue
                label = _clean(th.get_text(" ", strip=True))
                value = _clean(td.get_text(" ", strip=True))
                if label and value:
                    rows.append(_attr_row("additional_information", label, value, i, "detailBulletsSections"))
                    i += 1
        if rows:
            break  # 只取第一个存在的附加信息区
    return rows


def _collect_attributes(soup) -> list:
    """全部 Product Attribute 行（product_overview → technical_details → additional_information）。"""
    return (_overview_attributes(soup) + _prod_details_attributes(soup)
            + _additional_attributes(soup))


def _feature_bullets_raw(soup) -> list:
    """feature_bullets_raw：#feature-bullets 下所有非空卖点文本（DATA_MODEL §8）。"""
    # Amazon uses all of these IDs for the same visible block across desktop,
    # quick-view and newer page layouts.  Use the first non-empty block so a
    # hidden/empty duplicate cannot mask the actual bullets.
    for selector in (_FEATURE_BULLETS_SEL, "#featurebullets_feature_div",
                     "#pqv-feature-bullets"):
        fb = soup.select_one(selector)
        if fb is None:
            continue
        bullets = []
        for li in fb.select("li"):
            t = _clean(li.get_text(" ", strip=True))
            if t:
                bullets.append(t)
        if bullets:
            return bullets
    return []


def _product_description_raw(soup) -> str:
    """product_description_raw：#productDescription_feature_div 正文。"""
    return _text(soup, _PROD_DESC_SEL)


def _detail_bullets_raw(soup) -> list:
    """other_visible_details：#detailBulletsWrapper_feature_div 的 li 子弹（n.º 排名/日期等证据）。"""
    el = soup.select_one(_DETAIL_BULLETS_WRAPPER_SEL)
    if el is None:
        return []
    out = []
    for li in el.select("li"):
        t = _clean(li.get_text(" ", strip=True))
        if t:
            out.append(t)
    return out


def parse_detail_page(html: str, asin: str) -> dict:
    """详情页 HTML → 原始详情字段（只读证据层，不做业务推断）。

    B4 低成本扩展：``parent_asin``（隐藏域，值不合法→空）、
    ``date_first_available_raw``（归一为 parse_es_date 接受的 "D M YYYY"）、
    ``product_url``（由 ASIN 确定性派生）、``image_url``（主图 src/高分辨率回退）。
    """
    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    page_text = _clean(body.get_text(" ", strip=True)) if body is not None else ""
    is_captcha = bool(CAPTCHA_RE.search(page_text))

    # 现价（主 BuyBox 价格回退链，与 JS 一致）
    price_el = None
    for price_sel in (
            "#corePrice_feature_div .a-price .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            ".apex-pricetopay-value .a-offscreen",
            ".priceToPay .a-offscreen",
            # Newer Amazon apex markup may leave the offscreen span empty
            # and expose the BuyBox amount only as visible text.
            "#apex_price .apex-pricetopay-value"):
        candidate = soup.select_one(price_sel)
        if candidate is not None and _clean(candidate.get_text(" ", strip=True)):
            price_el = candidate
            break
    current_price_raw = _clean(price_el.get_text(" ", strip=True)) if price_el else ""

    # 划线价（原价）——必须有明确 data-a-strike=true 证据
    original_price_raw = _struck_price(soup)

    # 评分
    rating_el = (soup.select_one("#acrPopover .a-icon-alt")
                 or soup.select_one("#averageCustomerReviews_feature_div .a-icon-alt"))
    rating_raw = _clean(rating_el.get_text(" ", strip=True)) if rating_el else ""

    # 评论数
    review_count_raw = _text(soup, "#acrCustomerReviewText")

    # 库存状态
    avail_el = (soup.select_one("#availability .a-color-success")
                or soup.select_one("#availability .a-color-price")
                or soup.select_one("#availability .a-declarative span")
                or soup.select_one("#availability span")
                or soup.select_one("#outOfStock .a-size-medium")
                or soup.select_one("#outOfStock .a-color-error"))
    availability_raw = _clean(avail_el.get_text(" ", strip=True)) if avail_el else ""

    # BSR 主排名：n.º N en 类目，在 '(' 截断（JS 语义；detail BSR ≠ bestseller_rank）
    detail_el = soup.select_one("#detailBulletsWrapper_feature_div, #prodDetails, #SalesRank")
    detail_bsr_raw = ""
    if detail_el is not None:
        m = re.search(r"n\.?º?\s*([\d.,]+)\s+en\s+([^()\n]{0,50})",
                      _clean(detail_el.get_text(" ", strip=True)), re.I)
        if m:
            detail_bsr_raw = "n.º %s en %s" % (m.group(1), _clean(m.group(2)))

    # 卖家（BuyBox "Vendido por X"）
    merchant_el = (soup.select_one("#merchantInfoFeature_feature_div a")
                   or soup.select_one("#sellerProfileTriggerId"))
    seller_raw = _clean(merchant_el.get_text(" ", strip=True)) if merchant_el else ""
    if not seller_raw:
        bb = soup.select_one('#tabular-buybox .tabular-buybox-text[role="text"] span')
        seller_raw = _clean(bb.get_text(" ", strip=True)) if bb else ""

    # 是否亚马逊自营 / 亚马逊配送
    buybox_el = soup.select_one("#buybox")
    buybox_text = _clean(buybox_el.get_text(" ", strip=True)) if buybox_el else ""
    sold_by_amazon = (seller_raw == "Amazon"
                      or bool(re.search(r"Vendido y enviado por Amazon|Vendido por Amazon", buybox_text)))
    fulfilled_by_amazon = bool(re.search(r"Enviado por Amazon|Vendido y enviado por Amazon", buybox_text))

    # 品牌（Byline，剥双前缀）
    brand_raw = ""
    byline = soup.select_one("#bylineInfo")
    if byline is not None:
        brand_raw = _clean(byline.get_text(" ", strip=True))
        brand_raw = re.sub(r"^Visita la tienda de\s*", "", brand_raw, flags=re.I)
        brand_raw = re.sub(r"^Marca:\s*", "", brand_raw, flags=re.I)
        brand_raw = _clean(brand_raw)
    if not brand_raw:
        # Some pages omit the byline but expose an explicit Marca row in the
        # product-overview table.  This is reliable page evidence, unlike a
        # title-prefix fallback (which is forbidden by the data rules).
        overview = soup.select_one(_OVERVIEW_SEL)
        if overview is not None:
            for row in overview.select("tr"):
                cells = row.select("td")
                if len(cells) < 2:
                    continue
                label = _clean(cells[0].get_text(" ", strip=True)).casefold()
                value = _clean(cells[1].get_text(" ", strip=True))
                if label in {"marca", "brand"} and value:
                    brand_raw = value
                    break

    # 已选规格（变体）：旧版 selection + 现代 dropdown/swatch 选择状态
    selected_variation_raw = _selected_variation(soup)

    return {
        "asin": asin,
        "detail_schema_version": CURRENT_DETAIL_SCHEMA_VERSION,
        "is_captcha": is_captcha,
        "title_es_raw": _text(soup, "#productTitle"),
        "current_price_raw": current_price_raw,
        "original_price_raw": original_price_raw,
        "monthly_bought_raw": _monthly_bought_raw(soup),
        "rating_raw": rating_raw,
        "review_count_raw": review_count_raw,
        "availability_raw": availability_raw,
        "detail_bsr_raw": detail_bsr_raw,
        "seller_raw": seller_raw,
        "brand_raw": brand_raw,
        "selected_variation_raw": selected_variation_raw,
        "sold_by_amazon": sold_by_amazon,
        "fulfilled_by_amazon": fulfilled_by_amazon,
        "parent_asin": _parent_asin(soup),
        "date_first_available_raw": _first_available_date_raw(soup),
        "product_url": _product_url(asin),
        "image_url": _main_image_url(soup),
        "detail_category_trail": _detail_category_trail(soup),
        # 无损全量详情（DATA_MODEL §4-§8）：完整 Key/Value 证据 + 卖点 + 描述
        "attributes": _collect_attributes(soup),
        "feature_bullets_raw": _feature_bullets_raw(soup),
        "product_description_raw": _product_description_raw(soup),
        "detail_bullets_raw": _detail_bullets_raw(soup),
    }


def _page_asin_candidates(soup) -> set[str]:
    """Extract explicit page identity signals when Amazon exposes them."""
    strong = set()
    weak = set()
    for el in soup.select("input#ASIN, input[name='ASIN'], input#productAsin"):
        value = el.get("value") or el.get("data-asin") or ""
        if _ASIN_RE.fullmatch(str(value).strip()):
            strong.add(str(value).strip().upper())
    for el in soup.select("[data-asin]"):
        value = el.get("data-asin") or ""
        if _ASIN_RE.fullmatch(str(value).strip()):
            weak.add(str(value).strip().upper())
    for link in soup.select("link[rel='canonical'], meta[property='og:url']"):
        value = link.get("href") or link.get("content") or ""
        match = re.search(r"/dp/([A-Z0-9]{10})", str(value), re.I)
        if match:
            strong.add(match.group(1).upper())
    return strong or weak


def _classify_saved_page(html: str, asin: str, meta: dict) -> tuple[str, AccessState, Optional[dict]]:
    """Classify saved evidence and return parsed data only for valid pages."""
    access_state = detect_access_status(meta.get("status_code", 200), html)
    if access_state is not AccessState.NORMAL:
        return "CHALLENGE", access_state, None
    if not html.strip():
        return "INVALID_OR_EMPTY", AccessState.UNKNOWN, None
    soup = BeautifulSoup(html, "lxml")
    candidates = _page_asin_candidates(soup)
    final_url = str(meta.get("final_url") or "")
    if final_url and not verify_asin_on_page(final_url, asin):
        return "INVALID_OR_EMPTY", AccessState.UNKNOWN, None
    if candidates and str(asin).upper() not in candidates:
        return "INVALID_OR_EMPTY", AccessState.UNKNOWN, None
    parsed = parse_detail_page(html, asin)
    if not parsed.get("title_es_raw") or parsed.get("is_captcha"):
        return "INVALID_OR_EMPTY", AccessState.UNKNOWN, None
    return "VALID_PRODUCT_PAGE", access_state, parsed


def reparse_saved_details(html_dirs, state, asins=None) -> list[dict]:
    """Offline reparse saved detail HTML; first valid directory wins per ASIN."""
    from pathlib import Path
    roots = [Path(html_dirs)] if isinstance(html_dirs, (str, Path)) else [Path(p) for p in (html_dirs or [])]
    wanted = {str(a).strip().upper() for a in (asins or []) if str(a).strip()}
    out = []
    seen_asins = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.html")):
            try:
                html = path.read_text(encoding="utf-8")
            except OSError:
                continue
            asin = path.stem.upper()
            if not _ASIN_RE.fullmatch(asin):
                match = re.search(r'(?:id|name)=["\']ASIN["\'][^>]*value=["\']([A-Z0-9]{10})', html, re.I)
                if not match:
                    match = re.search(r'(?:data-asin|parentASIN)["\']?\s*[:=]\s*["\']([A-Z0-9]{10})', html, re.I)
                asin = match.group(1).upper() if match else ""
            if not asin or not _ASIN_RE.fullmatch(asin) or (wanted and asin not in wanted):
                continue
            meta = {}
            meta_path = path.with_suffix(".meta.json")
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
            classification, state_value, rec = _classify_saved_page(html, asin, meta)
            if classification != "VALID_PRODUCT_PAGE":
                continue
            if asin in seen_asins:
                continue
            rec.update({"status_code": meta.get("status_code"),
                        "access_state": state_value.value,
                        "resumed_from_html": True})
            out.append(rec)
            seen_asins.add(asin)
    if out:
        state.update(out)
    return out


def audit_saved_detail_cache(html_dirs, asins=None, quarantine_dir=None, state=None,
                             move=False) -> dict:
    """Classify saved HTML without network access.

    Every file is reported as VALID_PRODUCT_PAGE, CHALLENGE, or
    INVALID_OR_EMPTY.  With ``move=False`` (the default) the cache is left
    untouched and challenge/invalid evidence is copied to ``quarantine_dir``.

    ``move=True`` relocates challenge/invalid evidence out of the active cache
    instead of copying it.  This is required to resume collection: a cached
    challenge page halts ``collect_details`` on every later run, so the
    poisoned entries must leave ``html/`` for the affected ASINs to be
    requested again.  The evidence is moved, never deleted, and valid product
    pages are always left in place.
    """
    from pathlib import Path
    roots = [Path(html_dirs)] if isinstance(html_dirs, (str, Path)) else [Path(p) for p in (html_dirs or [])]
    wanted = {str(a).strip().upper() for a in (asins or []) if str(a).strip()}
    from shutil import copy2, move as move_file
    quarantine = Path(quarantine_dir) if quarantine_dir else None
    if move and quarantine is None:
        raise ValueError("move=True 需要 quarantine_dir：证据只移动，绝不删除")
    if quarantine:
        quarantine.mkdir(parents=True, exist_ok=True)
    records = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.html")):
            try:
                html = path.read_text(encoding="utf-8")
            except OSError:
                continue
            asin = path.stem.upper()
            if wanted and asin not in wanted:
                continue
            status_meta = {}
            meta_path = path.with_suffix(".meta.json")
            if meta_path.exists():
                try:
                    status_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    status_meta = {}
            classification, access_state, parsed = _classify_saved_page(html, asin, status_meta)
            records.append({"asin": asin, "path": str(path), "classification": classification,
                            "access_state": access_state.value,
                            "quarantined": bool(quarantine) and classification != "VALID_PRODUCT_PAGE",
                            "removed_from_cache": bool(move) and classification != "VALID_PRODUCT_PAGE"})
            if classification != "VALID_PRODUCT_PAGE" and quarantine:
                transfer = move_file if move else copy2
                transfer(str(path), str(quarantine / path.name))
                if meta_path.exists():
                    transfer(str(meta_path), str(quarantine / meta_path.name))
            if state is not None:
                update = parsed or {"asin": asin}
                update.update({"status_code": status_meta.get("status_code"),
                               "access_state": access_state.value,
                               "cache_classification": classification})
                state.update([update])
    summary = {k: sum(r["classification"] == k for r in records)
               for k in ("VALID_PRODUCT_PAGE", "CHALLENGE", "INVALID_OR_EMPTY")}
    summary["removed_from_cache"] = sum(1 for r in records if r["removed_from_cache"])
    return {"summary": summary, "records": records}


def struck_price_from_html(html: str) -> str:
    """Public wrapper so the closure audit shares the collector's strike semantics.

    The audit must not re-implement a looser ``data-a-strike`` regex: Amazon
    restates the current price under "Precio único" with that exact attribute,
    and treating it as evidence turns correct collector behaviour into a
    PARSER_MISSED finding that blocks a valid export.
    """
    if not html:
        return ""
    return _struck_price(BeautifulSoup(html, "lxml"))


def verify_asin_on_page(url: str, asin: str) -> bool:
    """URL 与记录 ASIN 是否一致（QA_RULES §4）。"""
    if not url or not asin:
        return False
    m = re.search(r"/dp/([A-Z0-9]{10})", str(url), re.I)
    if not m:
        return False
    return m.group(1).upper() == str(asin).strip().upper()


def collect_details(asins: List[str], session, out_dir: str, on_progress=None) -> List[dict]:
    """串行采集详情页：原始 HTML 落盘 html/<asin>.html + 结果 details.json。

    访问纪律（extract_details.js 语义）：goto → wait_for_product_page →
    wait_for_price_text → 页内 1.5s；页间 2.0s 显式延迟。

    访问门禁（ARCHITECTURE §6/§67）：受限页（CHALLENGE/BLOCKED/RATE_LIMITED/
    NETWORK_ERROR/UNKNOWN）HTML 先落盘保留证据，随即抛 AccessStopError——
    challenge 页绝不空解析进结果，details.json 不写出（不产出不可信详情）。

    断点续采（幂等恢复）：已落盘且非受限的 html/<ASIN>.html 直接离线恢复，
    不再发请求——重跑命令天然只补缺失 ASIN。resume 判定只看 CAPTCHA 信号
    （无 HTTP 状态码可依；正常落盘页是此前通过 gate 的 NORMAL 证据）。
    网关超时发生在写 HTML 之前 → 失败 ASIN 无落盘文件 → 重跑必重采。

    失败隔离（瞬时网络故障）：单个 ASIN 的 goto 抛异常（如页面加载超时）
    记入失败并继续，不让一个慢页毁掉整批；访问受限仍由 require_normal_access
    抛 AccessStopError，不吞、不重试、不绕过。
    """
    html_dir = os.path.join(str(out_dir), "html")
    os.makedirs(html_dir, exist_ok=True)

    details: List[dict] = []
    failed: List[str] = []
    checkpoint_dir = os.path.join(str(out_dir), "checkpoints")
    quarantine_root = os.path.join(str(out_dir), "quarantine")
    total = len(asins)

    def progress(asin, status):
        if on_progress is not None:
            on_progress({"completed": len(details) + len(failed), "total": total,
                         "asin": asin, "status": status})

    def quarantine_invalid(asin, path, meta_path):
        target = os.path.join(quarantine_root, asin)
        os.makedirs(target, exist_ok=True)
        for source in (path, meta_path):
            if os.path.exists(source):
                shutil.move(source, os.path.join(target, os.path.basename(source)))
    for asin in asins:
        path = os.path.join(html_dir, asin + ".html")
        meta_path = os.path.splitext(path)[0] + ".meta.json"
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                html = f.read()
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    meta = {}
            cached_status = meta.get("status_code", 200)
            state = detect_access_status(cached_status, html)
            if state.value != "NORMAL":
                raise AccessStopError(
                    "已落盘证据受限（%s），ASIN %s，按策略停止。"
                    "该文件是上一轮留下的历史证据，会阻断本轮全部续采；"
                    "先用 amazon-es audit-detail-cache --html-dir %s "
                    "--quarantine-dir <隔离目录> --move 把它移出活动缓存"
                    "（移动不删除），再重跑本命令。"
                    % (state.value, asin, html_dir))
            require_normal_access(state, "缓存 HTML，ASIN %s" % asin)
            cached_url = meta.get("final_url") or ""
            if cached_url and not verify_asin_on_page(cached_url, asin):
                quarantine_invalid(asin, path, meta_path)
                write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "asin_mismatch",
                                 "error": "缓存详情页 ASIN 不一致", "final_url": cached_url})
                progress(asin, "asin_mismatch")
                continue
            classification, parsed_state, rec = _classify_saved_page(
                html, asin, {"status_code": cached_status, "final_url": cached_url})
            if classification != "VALID_PRODUCT_PAGE":
                quarantine_invalid(asin, path, meta_path)
                write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "invalid",
                                 "classification": classification, "source": "cache"})
                progress(asin, "invalid")
                continue
            rec["status_code"] = meta.get("status_code")
            rec["access_state"] = parsed_state.value
            rec["resumed_from_html"] = True
            details.append(rec)
            write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "success",
                             "source": "cache", "record": rec})
            progress(asin, "success")
            continue
        try:
            status = session.goto("https://www.amazon.es/dp/" + asin)
            session.wait_for_product_page()
            session.wait_for_price_text()
            time.sleep(1.5)
            html = session.page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)  # 先保留证据，再判定访问状态
            state = detect_access_status(status, html)
            try:
                require_normal_access(state, "HTTP %s，ASIN %s，已采 %d 条"
                                      % (status, asin, len(details)))
            except AccessStopError as exc:
                write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "access_stop",
                                 "access_state": state.value, "error": str(exc)})
                progress(asin, "access_stop")
                raise
            final_url = str(getattr(session.page, "url", "") or "")
            if final_url and not verify_asin_on_page(final_url, asin):
                quarantine_invalid(asin, path, meta_path)
                write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "asin_mismatch",
                                 "error": "详情页 ASIN 不一致", "final_url": final_url})
                progress(asin, "asin_mismatch")
                continue
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"status_code": status, "final_url": final_url,
                           "access_state": state.value}, f, ensure_ascii=False, indent=2)
            classification, parsed_state, rec = _classify_saved_page(
                html, asin, {"status_code": status, "final_url": final_url})
            if classification != "VALID_PRODUCT_PAGE":
                quarantine_invalid(asin, path, meta_path)
                write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "invalid",
                                 "classification": classification, "source": "network"})
                progress(asin, "invalid")
                continue
            rec["status_code"] = status
            rec["access_state"] = parsed_state.value
            details.append(rec)
            write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "success",
                             "source": "network", "record": rec})
            progress(asin, "success")
            session.wait_between_requests()
        except AccessStopError:
            raise  # 访问受限：按策略停止，受限页证据已落盘
        except Exception as exc:
            failed.append(asin)  # 瞬时网络故障：失败隔离，不重试不绕过
            write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "failed",
                             "error_type": type(exc).__name__, "error": str(exc)})
            progress(asin, "failed")
            print("详情采集失败 ASIN %s：%s（跳过，重跑将补齐）"
                  % (asin, type(exc).__name__))

    with open(os.path.join(str(out_dir), "details.json"), "w", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    if failed:
        print("详情采集完成：%d 成功 / %d 失败（重跑自动补齐缺失）"
              % (len(details), len(failed)))
    return details
