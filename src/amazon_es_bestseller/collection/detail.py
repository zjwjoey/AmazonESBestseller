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
import uuid
from datetime import datetime
from typing import Callable, List, Optional

from bs4 import BeautifulSoup

from ..access.detector import (AccessStopError, CAPTCHA_RE, detect_access_status,
                               require_normal_access)
from ..identity import resolve_identity, asin_from_url
from ..models import AccessState
from .checkpoints import write_checkpoint

CURRENT_DETAIL_SCHEMA_VERSION = 2
CURRENT_DETAIL_PARSER_VERSION = "collection.detail_v2"


def _clean(t) -> str:
    """对齐 JS clean()：压空白、去首尾。"""
    return re.sub(r"\s+", " ", (t or "")).strip()


def _text(soup, sel) -> str:
    el = soup.select_one(sel)
    return _clean(el.get_text(" ", strip=True)) if el is not None else ""


def _hidden(node, *, allow_expanders: bool = False) -> bool:
    for element in [node, *node.parents]:
        if getattr(element, "name", "") in {"script", "style", "template"}:
            return True
        if not hasattr(element, "get"):
            continue
        expandable = allow_expanders and "a-expander-content" in (element.get("class") or [])
        if element.has_attr("hidden") or (not expandable and re.search(
                r"display\s*:\s*none|visibility\s*:\s*hidden", element.get("style", ""), re.I)):
            return True
        if {"aok-hidden", "a-hidden"} & set(element.get("class") or []):
            return True
    return False


def _evidence_text(node, *, allow_expanders: bool = False) -> str:
    """Saved visible text, excluding scripts and explicitly hidden copies."""
    parts = []
    for text in node.strings:
        if _hidden(text.parent, allow_expanders=allow_expanders):
            continue
        # Keep the saved full attribute rather than concatenating the visible
        # truncated preview and its full offscreen copy.
        cut = text.find_parent(class_="a-truncate-cut")
        if cut is not None and cut.parent.select_one(".a-truncate-full") is not None:
            continue
        parts.append(str(text))
    return _clean(" ".join(parts))


def _secondary_product(node) -> bool:
    for element in [node, *node.parents]:
        if not hasattr(element, "get"):
            continue
        context = str(element.get("id") or "") + " " + " ".join(element.get("class") or [])
        if re.search(r"sponsor|recommend|related|similar|desktop-dp-sims|p13n|a-carousel", context, re.I):
            return True
    return False


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


_DETAIL_BREADCRUMB_SELECTORS = (
    ("wayfinding-breadcrumbs", "#wayfinding-breadcrumbs_feature_div"),
    # Amazon's newer desktop card has the same product breadcrumb, but some
    # saved pages contain only this surface. It is a scoped fallback: never
    # search arbitrary page-wide navigation or recommendation cards.
    ("desktop-breadcrumbs", "#desktop-breadcrumbs_feature_div"),
)
def _category_links(container) -> tuple[list[str], list[dict]]:
    trail = []
    links = []
    seen = set()
    for a in container.select("a"):
        name = _clean(a.get_text(" ", strip=True))
        key = name.casefold()
        if not name or key in _DETAIL_CATEGORY_SKIP or key in seen:
            continue
        seen.add(key)
        trail.append(name)
        links.append({"label_raw": name, "href_raw": str(a.get("href") or "").strip()})
    return trail, links


def _detail_category_evidence(soup) -> list[dict]:
    """Return distinct, scoped product breadcrumb candidates with provenance."""
    candidates = []
    seen_paths = set()
    for source, selector in _DETAIL_BREADCRUMB_SELECTORS:
        container = soup.select_one(selector)
        if container is None or _hidden(container) or _secondary_product(container):
            continue
        trail, links = _category_links(container)
        key = tuple(value.casefold() for value in trail)
        if not trail or key in seen_paths:
            # Preserve duplicate selector evidence only when it adds distinct
            # labels; duplicate layouts should not imply a deeper path.
            continue
        seen_paths.add(key)
        candidates.append({"source": source, "path": trail, "links": links})
    return candidates


def _detail_category_trail(soup) -> list[str]:
    """Best scoped visible product breadcrumb, root → leaf, raw Spanish."""
    candidates = _detail_category_evidence(soup)
    return list(candidates[0]["path"]) if candidates else []


def _bsr_category_evidence(soup) -> list[dict]:
    """Preserve explicit BSR category links without treating them as a path."""
    out, seen = [], set()
    for selector in ("#SalesRank", "#detailBulletsWrapper_feature_div", "#prodDetails"):
        for container in soup.select(selector):
            if _hidden(container) or _secondary_product(container):
                continue
            for anchor in container.select("a[href]"):
                href = str(anchor.get("href") or "").strip()
                if not re.search(r"/gp/bestsellers/|(?:\?|&)node=", href, re.I):
                    continue
                label = _clean(anchor.get_text(" ", strip=True))
                if not label:
                    continue
                # Only retain links attached to an explicit rank statement;
                # unrelated help/store links in the same product block are
                # not category evidence.
                parent_text = ""
                node = anchor
                found_rank_context = False
                while node is not None:
                    parent_text = _clean(node.get_text(" ", strip=True))
                    if re.search(r"(?:n\.?º|número|puesto)\s*[\d.,]+\s+en\s+", parent_text, re.I):
                        found_rank_context = True
                        break
                    if node is container:
                        break
                    node = node.parent
                if not found_rank_context:
                    continue
                match = re.search(r"(?:[?&]node=|/)(\d{5,})(?:[/?&#]|$)", href, re.I)
                row = {"label_raw": label, "href_raw": href,
                       "browse_node_id": match.group(1) if match else None,
                       "source_container": selector}
                key = (label.casefold(), href)
                if key not in seen:
                    seen.add(key)
                    out.append(row)
    return out


def _parent_asin(soup, html: str | None = None) -> str:
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
    for match in _PARENT_ASIN_JSON_RE.finditer(html if html is not None else str(soup)):
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
    for el in soup.select("#social-proofing-faceout, .social-proofing-faceout, "
                          "#socialProofingAsinFaceout_feature_div"):
        if _hidden(el) or _secondary_product(el):
            continue
        text = _evidence_text(el)
        m = _MONTHLY_RE.search(text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def _current_product_price_candidate(node) -> bool:
    """A variation's price is evidence for that option, not the main BuyBox."""
    if _hidden(node) or _secondary_product(node):
        return False
    for element in [node, *node.parents]:
        if not hasattr(element, 'get'):
            continue
        identifier = str(element.get('id') or '').casefold()
        classes = set(element.get('class') or [])
        if (identifier.startswith('variation_') or 'twister' in identifier
                or {'apex_on_twister_price', 'dimension-slot-info'} & classes):
            return False
    return True


def _current_price(soup) -> str:
    # Restrict fallbacks to explicit current-product price containers.
    for node in soup.select("#corePrice_feature_div .a-price, "
                            "#corePriceDisplay_desktop_feature_div .a-price, "
                            ".apex-pricetopay-value, .priceToPay"):
        if not _current_product_price_candidate(node):
            continue
        if any(hasattr(a, "get") and (
                "a-text-price" in (a.get("class") or [])
                or str(a.get("data-a-strike")).lower() == "true"
                or re.search(r"priceperunit|basisprice", " ".join(a.get("class") or []), re.I))
                for a in [node, *node.parents]):
            continue
        offscreen = node.select_one(".a-offscreen")
        value = _evidence_text(offscreen) if offscreen is not None else ""
        if not value:
            value = _evidence_text(node)
        if value and not re.search(r"/\s*(kg|g|l|ml)\b", value, re.I):
            from ..normalization.price import parse_price
            if parse_price(value) is not None:
                return value
    return ""


def _struck_price(soup) -> str:
    """只从明确 data-a-strike=true 的划线价格中取原价。"""
    containers = soup.select("#corePrice_feature_div .a-text-price, "
                             "#corePriceDisplay_desktop_feature_div .a-text-price, "
                             "#apex_price .a-text-price")
    for container in containers:
        if not _current_product_price_candidate(container):
            continue
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
            value = _evidence_text(offscreen)
            if str(marked).lower() == "true" or marked_parent is not None:
                from ..normalization.price import parse_price
                if parse_price(value) is not None:
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


def _attribute_label_key(value: str) -> str:
    # Directional controls belong in raw evidence, not in label matching.
    return _clean(re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", value)).casefold()


def _overview_attributes(soup) -> list:
    """product_overview：Resumen del producto 表，td.a-span3(label)+td.a-span9(value)。"""
    rows = []
    po = soup.select_one(_OVERVIEW_SEL)
    if po is None:
        return rows
    i = 0
    for tr in po.select("tr"):
        tds = tr.find_all(["th", "td"], recursive=False)
        if len(tds) < 2 or _hidden(tr, allow_expanders=True):
            continue
        label = _evidence_text(tds[0], allow_expanders=True)
        value = _evidence_text(tds[1], allow_expanders=True)
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
    i = 0
    for table in soup.select("#prodDetails table, table[id^='productDetails_techSpec']"):
        if _is_excluded_detail_table(table):
            continue
        for tr in table.select("tr"):
            th = tr.select_one("th")
            td = tr.select_one("td")
            if th is None or td is None or _hidden(tr, allow_expanders=True):
                continue  # 表头行（colspan 无值）等非属性行
            label = _evidence_text(th, allow_expanders=True)
            value = _evidence_text(td, allow_expanders=True)
            if label and value:
                rows.append(_attr_row("technical_details", label, value, i, "prodDetails"))
                i += 1
    return rows


def _additional_attributes(soup) -> list:
    """Read all additional-information blocks; no first-block truncation."""
    rows = []
    for dt in soup.select("#detailBullets_feature_div dt"):
        dd = dt.find_next_sibling()
        if (dd is not None and dd.name == "dd" and not _hidden(dt, allow_expanders=True)
                and not _hidden(dd, allow_expanders=True)):
            label, value = _evidence_text(dt, allow_expanders=True), _evidence_text(dd, allow_expanders=True)
            if label and value:
                rows.append(_attr_row("additional_information", label, value, len(rows), "detailBullets"))
    for tr in soup.select("table[id^='productDetails_detailBullets'] tr"):
        th, td = tr.select_one("th"), tr.select_one("td")
        if th is not None and td is not None and not _hidden(tr, allow_expanders=True):
            label, value = _evidence_text(th, allow_expanders=True), _evidence_text(td, allow_expanders=True)
            if label and value:
                rows.append(_attr_row("additional_information", label, value, len(rows), "detailBulletsSections"))
    return rows


def _collect_attributes(soup) -> list:
    """全部 Product Attribute 行（product_overview → technical_details → additional_information）。"""
    rows = (_overview_attributes(soup) + _prod_details_attributes(soup)
            + _additional_attributes(soup))
    for text in _detail_bullets_raw(soup):
        label, separator, value = text.partition(":")
        if separator and label.strip() and value.strip():
            rows.append(_attr_row("detail_bullets", _clean(label), _clean(value),
                                  len(rows), "detailBulletsWrapper"))
    return rows


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
            t = _evidence_text(li, allow_expanders=True)
            if t:
                bullets.append(t)
        if bullets:
            return bullets
    return []


def _product_description_raw(soup) -> str:
    """product_description_raw：#productDescription_feature_div 正文。"""
    for selector in ("#productDescription", _PROD_DESC_SEL):
        for node in soup.select(selector):
            value = _evidence_text(node, allow_expanders=True)
            if value:
                return value
    return ""


def _aplus_content_raw(soup) -> list[dict]:
    """Keep scoped A+ prose separate from facts and comparison-product tables."""
    rows = []
    for region in soup.select("#aplus_feature_div"):
        if _hidden(region) or _secondary_product(region):
            continue
        for block in region.select("h1, h2, h3, h4, p, li"):
            if block.find_parent(["p", "li"]) is not None:
                continue
            excluded = False
            for ancestor in [block, *block.parents]:
                if ancestor is region:
                    break
                context = str(ancestor.get("id") or "") + " " + " ".join(ancestor.get("class") or [])
                if (ancestor.get("role") == "dialog" or _secondary_product(ancestor)
                        or re.search(r"comparison|compare|modal|video", context, re.I)):
                    excluded = True
                    break
            if excluded:
                continue
            value = _evidence_text(block, allow_expanders=True)
            if value:
                rows.append({"source": "aplus_feature_div", "position": len(rows),
                             "tag": block.name, "text_raw": value})
    return rows


def _detail_bullets_raw(soup) -> list:
    """other_visible_details：#detailBulletsWrapper_feature_div 的 li 子弹（n.º 排名/日期等证据）。"""
    el = soup.select_one(_DETAIL_BULLETS_WRAPPER_SEL)
    if el is None:
        return []
    out = []
    for li in el.select("li"):
        t = _evidence_text(li, allow_expanders=True)
        if t:
            out.append(t)
    return out


def _seller_visible(node) -> bool:
    return not _hidden(node) and not _secondary_product(node)


def _seller_from_dom(soup) -> tuple[str, dict]:
    """Read seller-owned controls, never the first shipping/BuyBox span."""
    active_roots = [root for root in soup.select('#merchantInfoFeature_feature_div')
                    if root.get('data-csa-c-is-in-initial-active-row') == 'true' and _seller_visible(root)]

    def eligible(node):
        # The telemetry flag is also false on ordinary single-offer pages.
        # Use it only to distinguish alternatives when an explicit active
        # merchant block exists; never treat every false flag as hidden.
        return _seller_visible(node) and (not active_roots or any(
            parent is root for parent in [node, *node.parents] for root in active_roots))

    for selector, source in (
        ('#merchantInfoFeature_feature_div #sellerProfileTriggerId', 'merchantInfoFeature_feature_div'),
        ('#merchantInfoFeature_feature_div a[href*="seller="]', 'merchantInfoFeature_feature_div'),
        ('#merchantInfoFeature_feature_div a', 'merchantInfoFeature_feature_div'),
        ('#sellerProfileTriggerId', 'sellerProfileTriggerId'),
    ):
        values = {_evidence_text(node) for node in soup.select(selector)
                  if eligible(node)} - {''}
        if len(values) == 1:
            value = values.pop()
            return value, {'source': source, 'selector': selector, 'value_raw': value}
        if len(values) > 1:
            return '', {}  # conflicting offers are not a license to pick one
    for root in soup.select('#merchantInfoFeature_feature_div'):
        if not eligible(root):
            continue
        # Amazon-owned offers can use plain text instead of a seller link.
        for node in root.select('.offer-display-feature-text-message'):
            value = _evidence_text(node)
            if value and eligible(node) and not re.search(r'vendido|remitente|vendedor', value, re.I):
                return value, {'source': 'merchantInfoFeature_feature_div', 'value_raw': value}
    return '', {}


def _seller_evidence(soup, html: str) -> tuple[str, dict]:
    value, evidence = _seller_from_dom(soup)
    if value or not html:
        return value, evidence
    # The saved TurboCheckout script in B0D9L982GG causes BeautifulSoup's
    # streaming builder to swallow the following BuyBox. Recover only actual
    # DOM elements from lxml, not HTML-looking strings inside JavaScript.
    from lxml import html as lhtml, etree
    try:
        tree = lhtml.fromstring(html)
    except (ValueError, etree.ParserError):
        return '', {}
    fragments = []
    for node in tree.xpath('//*[@id="merchantInfoFeature_feature_div" or @id="sellerProfileTriggerId"]'):
        unsafe = False
        for ancestor in [node, *node.iterancestors()]:
            context = str(ancestor.get('id') or '') + ' ' + str(ancestor.get('class') or '')
            if (ancestor.tag in {'script', 'style', 'template'} or ancestor.get('hidden') is not None
                    or re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', ancestor.get('style', ''), re.I)
                    or re.search(r'sponsor|recommend|related|similar|desktop-dp-sims|p13n|a-carousel|aok-hidden|a-hidden', context, re.I)):
                unsafe = True
                break
        if not unsafe:
            fragments.append(etree.tostring(node, encoding='unicode', method='html'))
    value, evidence = _seller_from_dom(BeautifulSoup(''.join(fragments), 'lxml'))
    if evidence:
        evidence['dom_recovery'] = 'lxml_scoped_buybox'
    return value, evidence


def _current_model_comparison_attributes(soup, asin: str) -> tuple[list, list]:
    """Preserve only a uniquely ASIN-linked, explicitly current device column.

    Comparative prices/ratings remain raw attributes, never canonical prices.
    Unlabelled icons are retained as markup evidence, not guessed booleans.
    """
    attrs, visual = [], []
    for table in soup.select('#mars-comp-chart_feature_div table.ucc-v2-widget__table'):
        if _hidden(table) or _secondary_product(table):
            continue
        rows = [row for row in table.select('tr') if row.find_parent('table') is table]
        if not rows:
            continue
        headers = rows[0].find_all(['th', 'td'], recursive=False)
        if any(cell.get('rowspan') or cell.get('colspan') for cell in headers):
            continue
        active = [i for i, cell in enumerate(headers) if cell.select_one('[aria-current="page"]') is not None]
        if len(active) != 1:
            continue
        index = active[0]
        links = {asin_from_url(a.get('href')) for a in headers[index].select('a[href]')}
        links.discard('')
        if index == 0 or links != {str(asin).upper()}:
            continue
        for row in rows[1:]:
            cells = row.find_all(['th', 'td'], recursive=False)
            if len(cells) != len(headers) or any(cell.get('rowspan') or cell.get('colspan') for cell in cells):
                continue
            label = _evidence_text(cells[0])
            cell = cells[index]
            if not label or _hidden(row) or _hidden(cell):
                continue
            value = _evidence_text(cell)
            if value:
                attrs.append({**_attr_row('current_model_comparison', label, value, len(attrs),
                                         'mars-comp-chart:current-asin-link'), 'asin': str(asin).upper()})
            elif cell.select_one('svg, img, i') is not None:
                visual.append({'label_raw': label, 'asin': str(asin).upper(),
                               'source': 'mars-comp-chart:current-asin-link', 'markup_raw': str(cell)})
    return attrs, visual


def parse_detail_page(html: str, asin: str) -> dict:
    """详情页 HTML → 原始详情字段（只读证据层，不做业务推断）。

    B4 低成本扩展：``parent_asin``（隐藏域，值不合法→空）、
    ``date_first_available_raw``（归一为 parse_es_date 接受的 "D M YYYY"）、
    ``product_url``（由 ASIN 确定性派生）、``image_url``（主图 src/高分辨率回退）。
    """
    return _parse_detail_page_soup(BeautifulSoup(html or "", "lxml"), html or "", asin)


def _parse_detail_page_soup(soup, html: str, asin: str) -> dict:
    """Internal parser entry used by V2 to share one DOM for one HTML page."""
    body = soup.find("body")
    page_text = _clean(body.get_text(" ", strip=True)) if body is not None else ""
    is_captcha = bool(CAPTCHA_RE.search(page_text))

    # 现价（主 BuyBox 价格回退链，与 JS 一致）
    current_price_raw = _current_price(soup)

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
    seller_raw, seller_evidence = _seller_evidence(soup, html)

    # 是否亚马逊自营 / 亚马逊配送
    buybox_el = soup.select_one("#buybox")
    buybox_text = _clean(buybox_el.get_text(" ", strip=True)) if buybox_el else ""
    sold_by_amazon = (seller_raw == "Amazon"
                      or bool(re.search(r"Vendido y enviado por Amazon|Vendido por Amazon", buybox_text)))
    fulfilled_by_amazon = bool(re.search(r"Enviado por Amazon|Vendido y enviado por Amazon", buybox_text))

    # 品牌（Byline，剥双前缀）
    brand_raw = ""
    brand_evidence = []
    byline = soup.select_one("#bylineInfo")
    if byline is not None and not _hidden(byline) and not _secondary_product(byline):
        brand_raw = _evidence_text(byline)
        brand_raw = re.sub(r"^Visita la tienda de\s*", "", brand_raw, flags=re.I)
        brand_raw = re.sub(r"^Marca:\s*", "", brand_raw, flags=re.I)
        brand_raw = _clean(brand_raw)
        if brand_raw:
            brand_evidence.append({'source': 'bylineInfo', 'value_raw': _evidence_text(byline)})
    attributes = _collect_attributes(soup)
    comparison_attributes, comparison_visuals = _current_model_comparison_attributes(soup, asin)
    attributes.extend(comparison_attributes)
    if not brand_raw:
        # Some pages omit the byline but expose an explicit Marca row in the
        # product-overview table.  This is reliable page evidence, unlike a
        # title-prefix fallback (which is forbidden by the data rules).
        for row in attributes:
            if _attribute_label_key(row["label_raw"]) in {"marca", "brand"}:
                brand_raw = row["value_raw"]
                brand_evidence.append({'source': row['source'], 'label_raw': row['label_raw'],
                                       'value_raw': row['value_raw']})
                break
    manufacturer_raw = next((row["value_raw"] for row in attributes
        if _attribute_label_key(row["label_raw"]) in {"fabricante", "manufacturer"}), "")

    # 已选规格（变体）：旧版 selection + 现代 dropdown/swatch 选择状态
    selected_variation_raw = _selected_variation(soup)

    bsr_category_raw = ""
    if detail_bsr_raw:
        bsr_match = re.search(r"\s+en\s+(.+)$", detail_bsr_raw, re.I)
        if bsr_match:
            bsr_category_raw = _clean(bsr_match.group(1))
    bsr_links = _bsr_category_evidence(soup)
    bsr_id_matches = []
    for row in bsr_links:
        label = re.sub(r"^ver el top\s*\d+\s+en\s+", "", row["label_raw"], flags=re.I).strip()
        if bsr_category_raw and label.casefold() == bsr_category_raw.casefold() and row.get("browse_node_id"):
            bsr_id_matches.append(row["browse_node_id"])

    category_evidence = _detail_category_evidence(soup)
    category_trail = list(category_evidence[0]["path"]) if category_evidence else []
    return {
        "asin": asin,
        "detail_schema_version": CURRENT_DETAIL_SCHEMA_VERSION,
        "detail_parser_version": "collection.detail_v1",
        "detail_parser_contract_version": 1,
        "is_captcha": is_captcha,
        "title_es_raw": _text(soup, "#productTitle"),
        "current_price_raw": current_price_raw,
        "original_price_raw": original_price_raw,
        "monthly_bought_raw": _monthly_bought_raw(soup),
        "rating_raw": rating_raw,
        "review_count_raw": review_count_raw,
        "availability_raw": availability_raw,
        "detail_bsr_raw": detail_bsr_raw,
        "bsr_category_id": (bsr_id_matches[0] if len(set(bsr_id_matches)) == 1 else None),
        "bsr_category_path": ([bsr_category_raw] if bsr_category_raw else []),
        "bsr_category_evidence": bsr_links,
        "seller_raw": seller_raw,
        "seller_evidence": seller_evidence,
        "brand_raw": brand_raw,
        "brand_evidence": brand_evidence,
        "manufacturer_raw": manufacturer_raw,
        "selected_variation_raw": selected_variation_raw,
        "sold_by_amazon": sold_by_amazon,
        "fulfilled_by_amazon": fulfilled_by_amazon,
        "parent_asin": _parent_asin(soup, html),
        "date_first_available_raw": _first_available_date_raw(soup),
        "product_url": _product_url(asin),
        "image_url": _main_image_url(soup),
        "detail_category_trail": category_trail,
        "detail_category_evidence": category_evidence,
        # 无损全量详情（DATA_MODEL §4-§8）：完整 Key/Value 证据 + 卖点 + 描述
        "attributes": attributes,
        "current_model_comparison_visual_evidence": comparison_visuals,
        "feature_bullets_raw": _feature_bullets_raw(soup),
        "product_description_raw": _product_description_raw(soup),
        "aplus_content_raw": _aplus_content_raw(soup),
        "detail_bullets_raw": _detail_bullets_raw(soup),
    }


def _page_asin_candidates(soup) -> set[str]:
    """Return only page-owned identity signals; page-wide cards are weak."""
    strong = set()
    for el in soup.select("input#ASIN, input[name='ASIN'], input#productAsin"):
        value = el.get("value") or el.get("data-asin") or ""
        if _ASIN_RE.fullmatch(str(value).strip()):
            strong.add(str(value).strip().upper())
    for link in soup.select("link[rel='canonical'], meta[property='og:url']"):
        value = link.get("href") or link.get("content") or ""
        match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?#]|$)",
                          str(value), re.I)
        if match:
            strong.add(match.group(1).upper())
    # Some modern detail pages expose the current ASIN on the main product
    # shell rather than the legacy hidden input. Keep this scope explicit.
    for el in soup.select("#dp-container[data-asin], #ppd[data-asin], #centerCol[data-asin]"):
        value = str(el.get("data-asin") or "").strip()
        if _ASIN_RE.fullmatch(value):
            strong.add(value.upper())
    return strong


def _weak_page_asin_observations(soup, strong=None) -> set[str]:
    """Keep recommendation/card ASINs for diagnostics without identity force."""
    values = set()
    for el in soup.select("[data-asin]"):
        value = str(el.get("data-asin") or "").strip()
        if _ASIN_RE.fullmatch(value):
            values.add(value.upper())
    for link in soup.select("a[href]"):
        match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?#]|$)",
                          str(link.get("href") or ""), re.I)
        if match:
            values.add(match.group(1).upper())
    return values - set(strong or ())


def _page_asin_evidence(html: str) -> list[str]:
    """Return explicit ASINs found in a saved page for mismatch diagnostics."""
    try:
        soup = BeautifulSoup(html, "lxml")
        strong = _page_asin_candidates(soup)
        return sorted(strong | _weak_page_asin_observations(soup, strong))
    except Exception:
        return []


def _parse_detail_for_version(html: str, asin: str, *, parser_version: str = "v1",
                              requested_url: str = "", final_url: str = "") -> dict:
    """Parse detail evidence through the explicitly selected parser contract.

    V1 remains the default for backwards compatibility.  Importing V2 lazily
    avoids the parser module's intentional dependency on ``parse_detail_page``.
    """
    if str(parser_version or "v1").casefold() in {"v2", "collection.detail_v2"}:
        from .detail_v2 import parse_detail_evidence_v2
        return parse_detail_evidence_v2(
            html, asin, requested_url=requested_url, final_url=final_url)
    return parse_detail_page(html, asin)


def _load_detail_metadata(path) -> dict:
    """Absent legacy metadata is allowed; damaged metadata is never discarded."""
    from pathlib import Path
    path = Path(path)
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("cache metadata must be an object")
        status = meta.get("status_code")
        if isinstance(status, str) and status.isdecimal():
            status = int(status)
            meta["status_code"] = status
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError("invalid cached HTTP status")
        if "recovered_from_challenge" in meta and not isinstance(meta["recovered_from_challenge"], bool):
            raise ValueError("invalid cached recovery flag")
        return meta
    except (OSError, ValueError) as exc:
        return {"_metadata_error": "%s: %s" % (type(exc).__name__, exc)}


def _classify_saved_page(html: str, asin: str, meta: dict, *,
                         parser_version: str = "v1") -> tuple[str, AccessState, Optional[dict]]:
    """Classify saved evidence and return parsed data only for valid pages."""
    # HTML evidence is always checked first.  This protects the historical
    # 200 + validateCaptcha cache case and prevents recovery metadata from
    # making a challenge page look valid.
    html_state = detect_access_status(200, html)
    if html_state is AccessState.CHALLENGE:
        return "CHALLENGE", html_state, None
    if meta.get("_metadata_error"):
        return "INVALID_OR_EMPTY", AccessState.UNKNOWN, None

    recovered = bool(meta.get("recovered_from_challenge"))
    effective_state = str(meta.get("access_state") or "").upper()
    if recovered and effective_state == AccessState.NORMAL.value:
        # The initial 403/429 remains evidence, while the recovered document
        # is judged by its final HTML and the explicit effective state.
        access_state = AccessState.NORMAL
    else:
        access_state = detect_access_status(meta.get("status_code", 200), html)
    if access_state is not AccessState.NORMAL:
        return "CHALLENGE", access_state, None
    if not html.strip():
        return "INVALID_OR_EMPTY", AccessState.UNKNOWN, None
    soup = BeautifulSoup(html, "lxml")
    candidates = _page_asin_candidates(soup)
    parsed = _parse_detail_for_version(
        html, asin, parser_version=parser_version,
        requested_url=str(meta.get("requested_url") or ""),
        final_url=str(meta.get("final_url") or ""))
    if not parsed.get("title_es_raw") or parsed.get("is_captcha"):
        return "INVALID_OR_EMPTY", AccessState.UNKNOWN, None
    return "VALID_PRODUCT_PAGE", access_state, parsed


def _resolve_page_identity(asin: str, html: str, final_url: str, record: dict | None) -> dict:
    """Use the shared resolver for live and cached detail evidence."""
    soup = BeautifulSoup(html, "lxml")
    page_asins = _page_asin_candidates(soup)
    weak_page_asins = _weak_page_asin_observations(soup, page_asins)
    parsed = next(iter(page_asins)) if len(page_asins) == 1 else ""
    record = record or {}
    identity = resolve_identity(
        ranking_asin=asin, requested_asin=asin, final_url_asin=final_url,
        embedded_asins=page_asins, parsed_detail_asin=parsed,
        weak_embedded_asins=weak_page_asins,
        parent_asin=record.get("parent_asin") or record.get("parent_asin_raw"),
        variation_family_asins=record.get("variation_family_asins") or record.get("variation_asins") or [],
    )
    # A matching destination URL cannot settle contradictory strong page
    # identities (e.g. ASIN input vs canonical URL). Only an explicit family
    # containing every observed identity can explain that disagreement.
    if len(page_asins) > 1 and not ({asin, *page_asins}
                                  <= set(identity["variation_family_asins"])):
        identity.update({"identity_status": "IDENTITY_UNCONFIRMED",
                         "identity_status_code": "IDENTITY_UNCONFIRMED",
                         "resolved_asin": ""})
    return identity


def reparse_saved_details(html_dirs, state, asins=None, *, parser_version: str = "v1") -> list[dict]:
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
            except (OSError, UnicodeError):
                continue
            asin = path.stem.upper()
            if not _ASIN_RE.fullmatch(asin):
                match = re.search(r'(?:id|name)=["\']ASIN["\'][^>]*value=["\']([A-Z0-9]{10})', html, re.I)
                if not match:
                    match = re.search(r'(?:data-asin|parentASIN)["\']?\s*[:=]\s*["\']([A-Z0-9]{10})', html, re.I)
                asin = match.group(1).upper() if match else ""
            if not asin or not _ASIN_RE.fullmatch(asin) or (wanted and asin not in wanted):
                continue
            meta_path = path.with_suffix(".meta.json")
            meta = _load_detail_metadata(meta_path)
            classification, state_value, rec = _classify_saved_page(
                html, asin, meta, parser_version=parser_version)
            if classification != "VALID_PRODUCT_PAGE":
                continue
            if asin in seen_asins:
                continue
            identity = _resolve_page_identity(asin, html, meta.get("final_url") or "", rec)
            if identity["identity_status"] in {"IDENTITY_MISMATCH", "IDENTITY_UNCONFIRMED"}:
                # The filename is only the requested identity, not proof of
                # what the saved HTML contains. Do not let a stale or
                # mislabelled page enter DetailState during offline reparse.
                continue
            rec.update({"status_code": meta.get("status_code"),
                        "initial_access_state": meta.get("initial_access_state"),
                        "access_state": state_value.value,
                        "recovered_from_challenge": bool(meta.get("recovered_from_challenge")),
                        "resumed_from_html": True,
                        "requested_asin": asin,
                        "resolved_asin": identity.get("resolved_asin") or asin,
                        "identity_status": ("MATCH" if identity["identity_status"] == "IDENTITY_MATCH"
                                             else identity["identity_status"]),
                        "identity_status_code": identity["identity_status"],
                        "identity_evidence": identity["identity_evidence"]})
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
            asin = path.stem.upper()
            if wanted and asin not in wanted:
                continue
            read_error = ""
            try:
                html = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                html = ""
                read_error = "%s: %s" % (type(exc).__name__, exc)
            meta_path = path.with_suffix(".meta.json")
            status_meta = _load_detail_metadata(meta_path)
            error = read_error or status_meta.get("_metadata_error", "")
            if read_error:
                classification, access_state, parsed = "INVALID_OR_EMPTY", AccessState.UNKNOWN, None
            else:
                classification, access_state, parsed = _classify_saved_page(html, asin, status_meta)
            identity = _resolve_page_identity(
                asin, html, status_meta.get("final_url") or "", parsed)
            if (classification == "VALID_PRODUCT_PAGE"
                    and identity["identity_status"] in {"IDENTITY_MISMATCH", "IDENTITY_UNCONFIRMED"}):
                classification = "INVALID_OR_EMPTY"
                parsed = None
            records.append({"asin": asin, "path": str(path), "classification": classification,
                            "initial_access_state": status_meta.get("initial_access_state"),
                            "access_state": access_state.value,
                            "recovered_from_challenge": bool(status_meta.get("recovered_from_challenge")),
                            "identity_status": identity["identity_status"],
                            "identity_evidence": identity["identity_evidence"],
                            "error": error,
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
                               "initial_access_state": status_meta.get("initial_access_state"),
                               "access_state": access_state.value,
                               "recovered_from_challenge": bool(status_meta.get("recovered_from_challenge")),
                               "cache_classification": classification,
                               "identity_status": identity["identity_status"],
                               "identity_status_code": identity["identity_status"],
                               "identity_evidence": identity["identity_evidence"]})
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


def collect_details(asins: List[str], session, out_dir: str, on_progress=None,
                    should_stop: Optional[Callable[[], bool]] = None,
                    request_urls: Optional[dict] = None,
                    execution_context: Optional[dict] = None,
                    write_summary: bool = True,
                    details_output_path: Optional[str] = None,
                    parser_version: str = "v1", checkpoint_root=None) -> List[dict]:
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
    # Keep direct collector use safe as well as the CLI path.  The adapter is a
    # no-op for offline fake sessions used by the test suite.
    from ..access.location import ensure_spain_delivery
    ensure_spain_delivery(session)
    html_dir = os.path.join(str(out_dir), "html")
    os.makedirs(html_dir, exist_ok=True)

    details: List[dict] = []
    failed: List[str] = []
    request_urls = request_urls or {}
    execution_context = execution_context or {}
    checkpoint_dir = str(checkpoint_root or os.path.join(str(out_dir), "checkpoints"))
    quarantine_root = os.path.join(str(out_dir), "quarantine")
    total = len(asins)
    completed_asins = set()

    def progress(asin, status):
        if status != "challenge_cached":
            completed_asins.add(asin)
        if on_progress is not None:
            on_progress({"completed": len(completed_asins), "total": total,
                         "asin": asin, "status": status})

    def quarantine_invalid(asin, path, meta_path):
        target = os.path.join(quarantine_root, asin)
        os.makedirs(target, exist_ok=True)
        for source in (path, meta_path):
            if os.path.exists(source):
                shutil.move(source, os.path.join(target, os.path.basename(source)))

    def checkpoint(asin, payload):
        """Persist the legacy checkpoint plus optional planner evidence."""
        enriched = dict(payload)
        record = enriched.get("record") if isinstance(enriched.get("record"), dict) else {}
        for key in ("final_url", "resolved_asin", "parent_asin", "identity_status",
                    "detail_status", "detail_schema_version", "status_code",
                    "http_status", "initial_access_state", "final_access_state",
                    "access_state", "collected_at"):
            if record.get(key) is not None:
                enriched.setdefault(key, record[key])
        context = execution_context.get(asin) or {}
        for key in ("snapshot_id", "ranking_asin", "ranking_product_url_raw",
                    "action", "attempt"):
            if context.get(key) is not None:
                enriched.setdefault(key, context[key])
        enriched.setdefault("requested_asin", asin)
        enriched.setdefault("attempt_count", int(context.get("attempt") or 1))
        collected_at = datetime.now().isoformat(timespec="seconds")
        enriched.setdefault("timestamp", collected_at)
        enriched.setdefault("collected_at", collected_at)
        if str(enriched.get("status") or "").lower() == "failed":
            enriched.setdefault("last_attempt_at", collected_at)
            if str(enriched.get("detail_status") or "").upper() in {
                    "TIMEOUT", "NETWORK_ERROR", "TRANSIENT_NETWORK_FAILURE"}:
                enriched.setdefault("next_retry_at", collected_at)
        if enriched.get("status_code") is not None:
            enriched.setdefault("http_status", enriched["status_code"])
        if enriched.get("access_state") is not None:
            enriched.setdefault("final_access_state", enriched["access_state"])
        if context.get("preferred_request_url"):
            enriched.setdefault("requested_url", context["preferred_request_url"])
        if enriched.get("final_url"):
            match = re.search(r"/dp/([A-Z0-9]{10})(?:[/?#]|$)",
                              str(enriched["final_url"]), re.I)
            if match:
                enriched.setdefault("resolved_asin", match.group(1).upper())
        write_checkpoint(checkpoint_dir, asin, enriched)

    for asin in asins:
        if should_stop is not None and should_stop():
            raise AccessStopError("其他工作槽触发访问限制，停止新的详情请求")
        path = os.path.join(html_dir, asin + ".html")
        meta_path = os.path.splitext(path)[0] + ".meta.json"
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    html = f.read()
            except (OSError, UnicodeError) as exc:
                failed.append(asin)
                checkpoint(asin, {"asin": asin, "status": "failed",
                    "detail_status": "CACHE_READ_ERROR", "error": str(exc), "source": "cache"})
                progress(asin, "failed")
                continue
            meta = _load_detail_metadata(meta_path)
            cached_status = meta.get("status_code", 200)
            cached_url = meta.get("final_url") or ""
            cache_meta = {"status_code": cached_status, "final_url": cached_url,
                          "initial_access_state": meta.get("initial_access_state"),
                          "access_state": meta.get("access_state"),
                          "recovered_from_challenge": meta.get("recovered_from_challenge"),
                          "requested_url": meta.get("requested_url"),
                          "_metadata_error": meta.get("_metadata_error")}
            try:
                classification, parsed_state, rec = _classify_saved_page(
                    html, asin, cache_meta, parser_version=parser_version)
            except OSError:
                # Read/parse output cannot be committed if storage is failing.
                # A system persistence fault must stop admission, not become an
                # ordinary per-product failure.
                raise
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                failed.append(asin)
                checkpoint(asin, {"asin": asin, "status": "failed",
                    "detail_status": "PARSER_ERROR", "error_type": type(exc).__name__.upper(),
                    "error": str(exc), "source": "cache"})
                progress(asin, "failed")
                continue
            force_refetch = execution_context.get(asin, {}).get("action") == "REFETCH_INVALID_CACHE"
            if classification == "CHALLENGE":
                if not getattr(session, "manual_assist", False):
                    raise AccessStopError(
                        "已落盘证据受限（%s），ASIN %s，按策略停止。"
                        "该文件是上一轮留下的历史证据，会阻断本轮全部续采；"
                        "先用 amazon-es audit-detail-cache --html-dir %s "
                        "--quarantine-dir <隔离目录> --move 把它移出活动缓存"
                        "（移动不删除），再重跑本命令。"
                        % (parsed_state.value, asin, html_dir))
                # In explicit manual-assist mode, retain the old challenge
                # evidence in quarantine and request the page again.  The
                # visible browser will then pause on a live challenge until a
                # human clears it; no cached challenge is treated as success.
                quarantine_invalid(asin, path, meta_path)
                checkpoint(asin, {
                    "asin": asin, "status": "challenge_cached",
                    "access_state": parsed_state.value,
                    "source": "cache_quarantined_for_manual_assist",
                })
                progress(asin, "challenge_cached")
            elif meta.get("_metadata_error"):
                failed.append(asin)
                checkpoint(asin, {"asin": asin, "status": "failed",
                    "detail_status": "CACHE_READ_ERROR", "error": meta["_metadata_error"],
                    "source": "cache"})
                progress(asin, "failed")
                continue
            elif force_refetch:
                # Preserve every previous capture before honoring an explicit
                # refetch. A historical challenge still takes the unchanged
                # gate above and never reaches this branch.
                history = os.path.join(str(out_dir), "cache_history", asin, uuid.uuid4().hex)
                os.makedirs(history, exist_ok=False)
                for source in (path, meta_path):
                    if os.path.exists(source):
                        shutil.copy2(source, os.path.join(history, os.path.basename(source)))
            else:
                if classification != "VALID_PRODUCT_PAGE":
                    quarantine_invalid(asin, path, meta_path)
                    checkpoint(asin, {"asin": asin, "status": "invalid",
                                     "classification": classification, "source": "cache",
                                     "observed_page_asins": _page_asin_evidence(html)})
                    progress(asin, "invalid")
                    continue
                cached_identity = _resolve_page_identity(asin, html, cached_url, rec)
                if cached_identity["identity_status"] in {"IDENTITY_MISMATCH", "IDENTITY_UNCONFIRMED"}:
                    quarantine_invalid(asin, path, meta_path)
                    checkpoint(asin, {"asin": asin, "status": "asin_mismatch",
                                      "error": "缓存详情页身份证据不一致或不足",
                                      "final_url": cached_url,
                                      "resolved_asin": cached_identity.get("resolved_asin"),
                                      "identity_status": cached_identity.get("identity_status"),
                                      "identity_evidence": cached_identity.get("identity_evidence"),
                                      "observed_page_asins": _page_asin_evidence(html)})
                    progress(asin, "asin_mismatch")
                    continue
                rec["status_code"] = meta.get("status_code")
                rec["initial_access_state"] = meta.get("initial_access_state")
                rec["access_state"] = parsed_state.value
                rec["final_access_state"] = parsed_state.value
                rec["http_status"] = meta.get("status_code")
                rec["recovered_from_challenge"] = bool(meta.get("recovered_from_challenge"))
                rec["resumed_from_html"] = True
                rec["requested_asin"] = asin
                rec["resolved_asin"] = cached_identity.get("resolved_asin") or asin
                rec["identity_status"] = ("MATCH" if cached_identity["identity_status"] == "IDENTITY_MATCH"
                                           else cached_identity["identity_status"])
                rec["identity_status_code"] = cached_identity["identity_status"]
                rec["identity_evidence"] = cached_identity["identity_evidence"]
                rec["detail_status"] = "SUCCESS"
                rec["requested_url"] = (execution_context.get(asin, {}).get("preferred_request_url")
                                         or cached_url or ("https://www.amazon.es/dp/" + asin))
                details.append(rec)
                checkpoint(asin, {"asin": asin, "status": "success",
                                 "source": "cache", "record": rec})
                progress(asin, "success")
                continue
        request_started = False
        access_stopped = False
        stage = "request"
        try:
            requested_url = str(request_urls.get(asin) or
                                ("https://www.amazon.es/dp/" + asin))
            request_started = True
            status = session.goto(requested_url)
            stage = "document_wait"
            session.wait_for_product_page()
            session.wait_for_price_text()
            time.sleep(1.5)
            stage = "document_capture"
            html = session.page.content()
            stage = "raw_persist"
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)  # 先保留证据，再判定访问状态
            stage = "access_gate"
            initial_state = detect_access_status(status, html)
            state = initial_state
            from ..access.challenge import maybe_wait_for_challenge
            original_html = html
            state, html, recovered = maybe_wait_for_challenge(session, state, html, status)
            if original_html != html and state.value == "NORMAL":
                stage = "challenge_evidence_persist"
                with open(path + ".challenge", "w", encoding="utf-8") as f:
                    f.write(original_html)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
            # Save HTTP evidence even when the gate stops here. Otherwise a
            # 403 with product-like HTML could be replayed as a legacy 200.
            final_url = str(getattr(session.page, "url", "") or "")
            stage = "metadata_persist"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"status_code": status, "final_url": final_url,
                           "requested_url": requested_url, "requested_asin": asin,
                           "initial_access_state": initial_state.value,
                           "access_state": state.value,
                           "recovered_from_challenge": recovered}, f,
                          ensure_ascii=False, indent=2)
            try:
                stage = "access_checkpoint"
                require_normal_access(state, "HTTP %s，ASIN %s，已采 %d 条"
                                      % (status, asin, len(details)))
            except AccessStopError as exc:
                checkpoint(asin, {"asin": asin, "status": "access_stop",
                                 "http_status": status,
                                 "initial_access_state": initial_state.value,
                                 "final_access_state": state.value,
                                 "access_state": state.value, "error": str(exc)})
                progress(asin, "access_stop")
                raise
            stage = "parse"
            classification, parsed_state, rec = _classify_saved_page(
                html, asin, {"status_code": status, "final_url": final_url,
                             "initial_access_state": initial_state.value,
                             "access_state": state.value,
                             "recovered_from_challenge": recovered,
                             "requested_url": requested_url},
                parser_version=parser_version)
            if classification != "VALID_PRODUCT_PAGE":
                quarantine_invalid(asin, path, meta_path)
                checkpoint(asin, {"asin": asin, "status": "invalid",
                                 "http_status": status,
                                 "initial_access_state": initial_state.value,
                                 "final_access_state": parsed_state.value,
                                 "classification": classification, "source": "network",
                                 "observed_page_asins": _page_asin_evidence(html)})
                progress(asin, "invalid")
                continue
            stage = "identity"
            identity = _resolve_page_identity(asin, html, final_url, rec)
            if identity["identity_status"] in {"IDENTITY_MISMATCH", "IDENTITY_UNCONFIRMED"}:
                quarantine_invalid(asin, path, meta_path)
                checkpoint(asin, {"asin": asin, "status": "asin_mismatch",
                                  "http_status": status,
                                  "initial_access_state": initial_state.value,
                                  "final_access_state": state.value,
                                  "error": "详情页身份证据不一致或不足", "final_url": final_url,
                                  "resolved_asin": identity.get("resolved_asin"),
                                  "identity_status": identity.get("identity_status"),
                                  "identity_evidence": identity.get("identity_evidence"),
                                  "observed_page_asins": _page_asin_evidence(html)})
                progress(asin, "asin_mismatch")
                continue
            rec["status_code"] = status
            rec["http_status"] = status
            rec["initial_access_state"] = initial_state.value
            rec["access_state"] = parsed_state.value
            rec["final_access_state"] = parsed_state.value
            rec["recovered_from_challenge"] = recovered
            details.append(rec)
            rec["requested_asin"] = asin
            rec["requested_url"] = requested_url
            rec["final_url"] = final_url
            rec["resolved_asin"] = identity.get("resolved_asin") or asin
            rec["identity_status"] = ("MATCH" if identity["identity_status"] == "IDENTITY_MATCH"
                                       else identity["identity_status"])
            rec["identity_status_code"] = identity["identity_status"]
            rec["identity_evidence"] = identity["identity_evidence"]
            rec["detail_status"] = "SUCCESS"
            stage = "success_checkpoint"
            checkpoint(asin, {"asin": asin, "status": "success",
                             "source": "network", "record": rec})
            progress(asin, "success")
        except AccessStopError:
            access_stopped = True
            raise  # 访问受限：按策略停止，受限页证据已落盘
        except Exception as exc:
            message = str(exc).lower()
            error_type = type(exc).__name__.upper()
            if isinstance(exc, OSError) and stage not in {
                    "request", "document_wait", "document_capture"}:
                # Evidence, metadata, and checkpoint persistence failures are
                # system-level. TimeoutError is also an OSError on Python, so
                # only capture-stage OSErrors are eligible for network typing.
                raise
            if stage == "parse" and isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)):
                detail_status = "PARSER_ERROR"
            elif stage in {"request", "document_wait", "document_capture"} and (
                    isinstance(exc, TimeoutError) or "timeout" in message):
                detail_status = "TIMEOUT"
            elif stage in {"request", "document_wait", "document_capture"} and (
                    isinstance(exc, ConnectionError)
                    or any(token in message for token in (
                        "network", "connection", "navigation", "net::err_"))):
                detail_status = "TRANSIENT_NETWORK_FAILURE"
            else:
                # Unknown control-flow, browser, identity, and system failures
                # have not been proven safe to isolate as a single bad SKU.
                raise
            failed.append(asin)
            checkpoint(asin, {"asin": asin, "status": "failed",
                             "detail_status": detail_status,
                             "error_type": error_type, "error": str(exc)})
            progress(asin, "failed")
            print("详情采集失败 ASIN %s：%s（跳过，重跑将补齐）"
                  % (asin, type(exc).__name__))
        finally:
            if request_started and not access_stopped:
                session.wait_between_requests()

    if write_summary or details_output_path:
        target = details_output_path or os.path.join(str(out_dir), "details.json")
        temporary = target + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(details, f, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    if failed:
        print("详情采集完成：%d 成功 / %d 失败（重跑自动补齐缺失）"
              % (len(details), len(failed)))
    return details
