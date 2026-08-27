# -*- coding: utf-8 -*-
"""离线主链 enrich：榜单+详情 → 规范化+中文派生商品表（QA_RULES §9/§10）。

只做确定性规范化与派生（西语=证据层、中文=派生层），全离线、不联网：
  - 合并（models.merge_ranking_and_detail）：ASIN 唯一主键，榜单字段优先；
  - 规范化：价格/评分/评论数/品牌/日期/规格/月购/BSR 段/类目中文；
  - 中文派生：title_zh（若提供翻译表）、product_type（仅从标题证据）、
    采集类目中文（只映射有把握的，未知留西语原文，绝不臆造）。

**遗留适配**（``legacy_flat_to_*``）：product_details.json 是老 extract_details.js
扁平输出。其中 ``BSR`` 列是 build_output.py 按 Rank 构造的历史伪造产物
（A4 已钉死，30/30 全部为 "n.º {Rank} en Hogar y cocina"），导入时**丢弃**
（缺失→null，宁缺毋假），绝不作为 detail BSR 证据。
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Mapping, Optional

from .models import merge_ranking_and_detail, normalize_asin
from .normalization.brand import clean_brand, normalize_brand_case
from .normalization.bsr import detail_bsr_segments
from .normalization.category import category_levels, category_zh
from .normalization.dates import parse_es_date
from .normalization.monthly_bought import parse_monthly_bought
from .normalization.price import CURRENCY, discount_rate, parse_price
from .normalization.specification import (
    attributes_to_spec_dict, build_spec_es, build_spec_v2,
    translate_spec_es_to_zh)
from .translation.full_detail import (
    render_bullets_es, render_bullets_zh, render_details_es, render_details_zh)
from .translation.ds import DeepSeekTranslator, TRANSLATION_SCHEMA_VERSION
from .translation.product_type import detect_product_type

_LEADING_NUM_RE = re.compile(r"^\(?\s*([\d.,]+)")  # 容忍前导 '('（现代评论数 "(8.819)"）


def _to_int_spanish(s: str) -> Optional[int]:
    """西语整数（'.' 千位分隔）→ int；无法解析 → None。"""
    t = str(s or "").strip()
    if not t:
        return None
    m = _LEADING_NUM_RE.match(t)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", "").replace(",", ""))
    except ValueError:
        return None


def _to_rating_num(s) -> Optional[str]:
    """评分前导数值 → 点分小数字符串（"4,5 de 5 estrellas" → "4.5"）。"""
    t = str(s or "").strip()
    if not t:
        return None
    m = _LEADING_NUM_RE.match(t)
    if not m:
        return None
    try:
        return str(float(m.group(1).replace(",", ".")))
    except ValueError:
        return None


def _details_of(prod: Mapping) -> Optional[dict]:
    """records 的 details_json（dict 或 JSON 字符串）→ dict；无 → None。"""
    dj = prod.get("details_json")
    if isinstance(dj, dict):
        return dj
    if isinstance(dj, str):
        try:
            return json.loads(dj)
        except (ValueError, TypeError):
            return None
    return None


def _brand_from_attributes(attributes) -> str:
    """Return a brand only from an explicit, reliable attribute label."""
    for attr in attributes or []:
        label = str(attr.get("label_raw") or "").strip().casefold()
        value = str(attr.get("value_raw") or "").strip()
        if label in {"marca", "brand"} and value:
            return value
    return ""


def normalize_product(prod: Mapping, translations: Optional[Mapping] = None) -> dict:
    """单条合并后商品 → 规范化 + 中文派生字段（不修改传入记录）。"""
    out = dict(prod)
    asin = normalize_asin(out.get("asin"))
    out["asin"] = asin

    # Parent ASIN is only useful when it identifies a confirmed variation
    # family.  A child ASIN copied into its own parent slot is not evidence of
    # a family; drop it unless an explicit confirmed status is present.
    parent = normalize_asin(out.get("parent_asin"))
    parent_status = str(out.get("parent_asin_status") or "").strip().casefold()
    if parent == asin and parent_status != "confirmed":
        out["parent_asin"] = ""
    elif parent:
        out["parent_asin"] = parent
    else:
        out["parent_asin"] = ""

    cur = parse_price(out.get("current_price_raw"))
    orig = parse_price(out.get("original_price_raw"))
    out["current_price"] = cur
    # A struck/list price at or below the current price is not a valid original
    # price.  Keep original_price_raw as evidence, but never display the bad number.
    out["original_price"] = orig if (cur is None or orig is None or orig > cur) else None
    out["currency"] = CURRENCY
    out["discount_rate"] = discount_rate(cur, orig)

    out["rating"] = _to_rating_num(out.get("rating_raw"))
    out["review_count"] = _to_int_spanish(out.get("review_count_raw"))

    brand = clean_brand(out.get("brand_raw"))
    if not brand:
        brand = clean_brand(_brand_from_attributes(out.get("attributes")))
    out["brand"] = normalize_brand_case(brand) if brand else ""

    dfa = parse_es_date(out.get("date_first_available_raw"))
    out["date_first_available"] = dfa.strftime("%Y-%m-%d") if dfa else None

    details = _details_of(out)
    if not details:
        # 现代无损全量模型：attributes（列表）→ 规格 dict，供 build_spec_v2
        details = attributes_to_spec_dict(out.get("attributes"))
    out["spec_v2"] = build_spec_v2(
        details, variant=out.get("selected_variation_raw"),
        title_es=out.get("title_es_raw"))
    out["specification_es"] = build_spec_es(
        attributes=out.get("attributes"), details=details,
        variant=out.get("selected_variation_raw"),
        title_es=out.get("title_es_raw"))

    out["detail_bsr_segments"] = detail_bsr_segments(out.get("detail_bsr_raw"))
    out["monthly_bought_min"] = parse_monthly_bought(out.get("monthly_bought_raw"))

    # 无损全量详情 → 展示渲染（DATA_MODEL §4-§8/§18-§19）：西语原文 + 中文派生
    attrs = out.get("attributes")
    bullets = out.get("feature_bullets_raw")
    out["product_details_es"] = render_details_es(attrs)
    out["product_details_zh"] = render_details_zh(attrs)
    out["feature_bullets_es"] = render_bullets_es(bullets)
    out["feature_bullets_zh"] = render_bullets_zh(bullets)

    title_es = out.get("title_es_raw")
    out["product_type"] = detect_product_type(title_es) if title_es else None

    # A root ranking page may expose only L1.  When deeper levels are absent,
    # use the explicit product-detail breadcrumb as a secondary source; never
    # overwrite a ranking-context value already present.
    detail_trail = out.get("detail_category_trail")
    if isinstance(detail_trail, (list, tuple)):
        dl1, dl2, dl3, dleaf = category_levels(detail_trail)
        for key, value in (("category_l1", dl1), ("category_l2", dl2),
                           ("category_l3", dl3), ("leaf_category", dleaf)):
            if not out.get(key) and value:
                out[key] = value

    # 类目中文（派生层）：只映射有把握的，未知保留西语原文（不臆造）
    l1 = out.get("category_l1")
    leaf = out.get("leaf_category")
    out["采集类目中文"] = category_zh(leaf) or category_zh(l1) or (l1 or "")
    for key in ("category_l1", "category_l2", "category_l3", "leaf_category"):
        value = out.get(key)
        # Unknown categories remain in Spanish as source evidence; known
        # reviewed labels receive a deterministic Chinese display overlay.
        out[f"{key}_zh"] = category_zh(value) or (value or "")

    tr = (translations or {}).get(asin) or {}
    # A translation overlay is valid only for the exact Spanish evidence it
    # was produced from; this prevents stale ASIN-only cache values from
    # surviving parser repairs or refreshed detail pages.
    translation_valid = isinstance(tr, dict) and (
        (tr.get("translation_source_hash") == DeepSeekTranslator.source_hash(out)
         and tr.get("translation_schema_version") == TRANSLATION_SCHEMA_VERSION)
        # Explicitly supplied legacy overlays remain readable for backwards
        # compatibility; the DS client never reuses such entries silently and
        # the closure audit still flags any untranslated residuals.
        or ("translation_source_hash" not in tr and "translation_schema_version" not in tr))
    if not translation_valid:
        tr = {}
    if isinstance(tr, dict):
        # DS is an optional display-layer overlay.  Only non-empty approved
        # fields are copied; every Spanish/raw field above remains untouched.
        if tr.get("title_zh"):
            out["title_zh"] = str(tr["title_zh"]).strip()
        else:
            out["title_zh"] = out.get("title_zh") or ""
        def usable_translation(value) -> bool:
            if value is None:
                return False
            if isinstance(value, (dict, list, tuple, set)):
                return bool(value)
            text = str(value).strip()
            return bool(text) and text not in {"{}", "[]", "null", "None"}

        source_for_translation = {
            "category_l1_zh": "category_l1",
            "category_l2_zh": "category_l2",
            "category_l3_zh": "category_l3",
            "leaf_category_zh": "leaf_category",
            "selected_variation_zh": "selected_variation_raw",
            "specification_zh": "specification_es",
            "product_details_zh": ("product_details_es", "attributes", "details_json"),
            "feature_bullets_zh": ("feature_bullets_es", "feature_bullets_raw", "detail_bullets_raw"),
        }
        for key in (
            "category_l1_zh", "category_l2_zh", "category_l3_zh", "leaf_category_zh",
            "selected_variation_zh", "specification_zh", "product_details_zh",
            "feature_bullets_zh",
        ):
            source_key = source_for_translation[key]
            source_keys = (source_key,) if isinstance(source_key, str) else source_key
            source_present = any(usable_translation(out.get(k)) for k in source_keys)
            # Legacy flat fixtures predate explicit raw evidence fields. Keep
            # their supplied overlays readable; modern records must prove the
            # corresponding Spanish source before a Chinese field is copied.
            legacy_overlay = "attributes" not in out and "feature_bullets_raw" not in out
            if (source_present or legacy_overlay) and usable_translation(tr.get(key)):
                out[key] = str(tr[key]).strip()
        # Deterministic Chinese spec fallback: spec_v2 is derived only from
        # explicit Spanish evidence (variation/title/attributes).  It is safe
        # to display when DS did not return a translated core-spec field.
        if not usable_translation(out.get("specification_zh")):
            zh_spec = translate_spec_es_to_zh(out.get("specification_es"))
            if not zh_spec:
                zh_spec = out.get("spec_v2")
            if zh_spec:
                out["specification_zh"] = str(zh_spec).strip()
    else:
        out["title_zh"] = out.get("title_zh") or ""
    return out


def enrich_products(ranking_records: List[Mapping],
                    details: List[Mapping],
                    translations: Optional[Mapping] = None) -> List[dict]:
    """榜单+详情 → 规范化+中文派生商品表（确定性排序：按 ASIN）。"""
    products = merge_ranking_and_detail(list(ranking_records), list(details))
    out = [normalize_product(p, translations) for p in products]
    out.sort(key=lambda r: normalize_asin(r.get("asin")))
    return out


# ---------- 遗留扁平数据适配（product_details.json，老 extract_details.js 输出） ----------

def legacy_flat_to_detail(rec: Mapping) -> dict:
    """老扁平记录 → 详情记录形状（丢弃构造型 BSR 列，QA_RULES §9 宁缺毋假）。

    证据层键与 parse_detail_page 对齐；``BSR`` 列为 build_output.py 按 Rank
    构造的历史伪造产物 → 丢弃为 ''（A4 已钉死），不进入 detail BSR。
    """
    return {
        "asin": normalize_asin(rec.get("ASIN")),
        "title_es_raw": (rec.get("Title") or "").strip(),
        "current_price_raw": (rec.get("Price_EUR") or "").strip(),
        "original_price_raw": (rec.get("ListPrice_EUR") or "").strip(),
        "rating_raw": (rec.get("Rating") or "").strip(),
        "review_count_raw": (rec.get("Reviews") or "").strip(),
        "brand_raw": (rec.get("Brand") or "").strip(),
        "seller_raw": (rec.get("Seller") or "").strip(),
        "availability_raw": (rec.get("Availability") or "").strip(),
        "detail_bsr_raw": "",  # 构造型 BSR 丢弃
        "selected_variation_raw": "",
        "sold_by_amazon": bool(str(rec.get("SoldByAmazon") or "").strip().lower() in ("yes", "sí", "si", "true")),
        "fulfilled_by_amazon": False,
        "product_url": (rec.get("URL") or "").strip(),
        "image_url": "",
    }


def legacy_flat_to_ranking(rec: Mapping, collected_at: str = "") -> dict:
    """老扁平记录 → 排行榜记录形状（Rank 即榜单位置，bestseller_rank 一等字段）。

    ranking_source_url 留空：该文件只有 /dp/ 商品 URL，没有榜单页 URL；
    缺来源上下文只产生 RANK_SOURCE_MISSING P2（WARN），不臆造榜单 URL。
    """
    return {
        "index": 0,
        "asin": normalize_asin(rec.get("ASIN")),
        "category_l1": None,
        "category_l2": None,
        "category_l3": None,
        "leaf_category": None,
        "browse_node_id": None,
        "bestseller_rank": _to_int_spanish(rec.get("Rank")),
        "ranking_source_url": "",
        "collected_at": collected_at,
    }
