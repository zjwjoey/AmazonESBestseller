# -*- coding: utf-8 -*-
"""商品详情页纯解析（离线）：选择器与 extract_details.js 完全一致。

challenge 页绝不空解析（ARCHITECTURE §67）：is_captcha 标记交给上层，本层照常
返回字段（缺省为空字符串），由 collect_details 决定是否保存。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional

from bs4 import BeautifulSoup

from ..access.detector import CAPTCHA_RE, detect_access_status, require_normal_access


def _clean(t) -> str:
    """对齐 JS clean()：压空白、去首尾。"""
    return re.sub(r"\s+", " ", (t or "")).strip()


def _text(soup, sel) -> str:
    el = soup.select_one(sel)
    return _clean(el.get_text(" ", strip=True)) if el is not None else ""


# ---------- B4 低成本扩展：parent ASIN / 首次上架日期 / 图片 ----------

_ASIN_RE = re.compile(r"[A-Z0-9]{10}", re.I)   # flags 编译进正则，调用时不传
_PARENT_ASIN_SELECTORS = ("input#parentASIN", 'input[name="parentASIN"]')

# 首次上架日期（西语标签；raw 归一为 parse_es_date 接受的 "D M YYYY"）
_AVAIL_DATE_RE = re.compile(
    r"(?:fecha de primera disponibilidad|primera fecha disponible|fecha de lanzamiento)"
    r"\s*:?\s*(\d{1,2})\s+(?:de\s+)?([a-záéíóúñü]+)[,\s]*(?:de\s+)?(\d{4})",
    re.I)
_DATE_SECTIONS = ("#detailBulletsWrapper_feature_div", "#detailBullets_feature_div",
                  "#productDetails_detailBullets_sections1", "#prodDetails")

_IMAGE_SELECTORS = ("#landingImage", "#imgBlkFront", "#imageBlock_feature_div img")


def _parent_asin(soup) -> str:
    """隐藏域 parentASIN → 10 位 ASIN；值不合法 → 空（缺失→空，不臆造）。"""
    for sel in _PARENT_ASIN_SELECTORS:
        el = soup.select_one(sel)
        if el is None:
            continue
        v = str(el.get("value") or "").strip()
        if _ASIN_RE.fullmatch(v):
            return v.upper()
    return ""


def _first_available_date_raw(soup) -> str:
    """详情页首次上架日期 → "D M YYYY"（供 parse_es_date 解析）；无 → 空。"""
    for sel in _DATE_SECTIONS:
        txt = _text(soup, sel)
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
    fb = soup.select_one(_FEATURE_BULLETS_SEL)
    if fb is None:
        return []
    bullets = []
    for li in fb.select("li"):
        t = _clean(li.get_text(" ", strip=True))
        if t:
            bullets.append(t)
    return bullets


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
    is_captcha = bool(CAPTCHA_RE.search(page_text[:300]))

    # 现价（主 BuyBox 价格回退链，与 JS 一致）
    price_el = (soup.select_one("#corePrice_feature_div .a-price .a-offscreen")
                or soup.select_one("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen")
                or soup.select_one(".apex-pricetopay-value .a-offscreen")
                or soup.select_one(".priceToPay .a-offscreen"))
    current_price_raw = _clean(price_el.get_text(" ", strip=True)) if price_el else ""

    # 划线价（原价）——仅取 corePrice 区域的 .a-text-price
    list_el = (soup.select_one("#corePrice_feature_div .a-text-price .a-offscreen")
               or soup.select_one("#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen"))
    original_price_raw = _clean(list_el.get_text(" ", strip=True)) if list_el else ""

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

    # 已选规格（变体）
    var_el = (soup.select_one("#variation_name .selection")
              or soup.select_one("#twister-plus-name-feature .selection")
              or soup.select_one(".twister-plus-buying-options-price-data .selection"))
    selected_variation_raw = _clean(var_el.get_text(" ", strip=True)) if var_el else ""

    return {
        "asin": asin,
        "is_captcha": is_captcha,
        "title_es_raw": _text(soup, "#productTitle"),
        "current_price_raw": current_price_raw,
        "original_price_raw": original_price_raw,
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
        # 无损全量详情（DATA_MODEL §4-§8）：完整 Key/Value 证据 + 卖点 + 描述
        "attributes": _collect_attributes(soup),
        "feature_bullets_raw": _feature_bullets_raw(soup),
        "product_description_raw": _product_description_raw(soup),
        "detail_bullets_raw": _detail_bullets_raw(soup),
    }


def verify_asin_on_page(url: str, asin: str) -> bool:
    """URL 与记录 ASIN 是否一致（QA_RULES §4）。"""
    if not url or not asin:
        return False
    m = re.search(r"/dp/([A-Z0-9]{10})", str(url), re.I)
    if not m:
        return False
    return m.group(1).upper() == str(asin).strip().upper()


def collect_details(asins: List[str], session, out_dir: str) -> List[dict]:
    """串行采集详情页：原始 HTML 落盘 html/<asin>.html + 结果 details.json。

    访问纪律（extract_details.js 语义）：goto → wait_for_product_page →
    wait_for_price_text → 页内 1.5s；页间 2.0s 显式延迟。

    访问门禁（ARCHITECTURE §6/§67）：受限页（CHALLENGE/BLOCKED/RATE_LIMITED/
    NETWORK_ERROR/UNKNOWN）HTML 先落盘保留证据，随即抛 AccessStopError——
    challenge 页绝不空解析进结果，details.json 不写出（不产出不可信详情）。
    """
    html_dir = os.path.join(str(out_dir), "html")
    os.makedirs(html_dir, exist_ok=True)

    details: List[dict] = []
    for asin in asins:
        status = session.goto("https://www.amazon.es/dp/" + asin)
        session.wait_for_product_page()
        session.wait_for_price_text()
        time.sleep(1.5)
        html = session.page.content()
        with open(os.path.join(html_dir, asin + ".html"), "w", encoding="utf-8") as f:
            f.write(html)  # 先保留证据，再判定访问状态
        state = detect_access_status(status, html)
        require_normal_access(state, "HTTP %s，ASIN %s，已采 %d 条"
                              % (status, asin, len(details)))
        rec = parse_detail_page(html, asin)
        rec["status_code"] = status
        rec["access_state"] = state.value
        details.append(rec)
        session.wait_between_requests()

    with open(os.path.join(str(out_dir), "details.json"), "w", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    return details
