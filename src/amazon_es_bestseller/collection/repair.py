# -*- coding: utf-8 -*-
"""Offline repair of canonical products from saved Amazon detail HTML.

The repair step is deliberately evidence-only: a page must expose one
confirmed ASIN and only non-empty parsed fields are merged into the matching
record. Translation overlays are preserved only when their source hash matches.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Mapping

from bs4 import BeautifulSoup

from ..models import normalize_asin
from ..pipeline import normalize_product
from ..translation.ds import DeepSeekTranslator, TRANSLATION_SCHEMA_VERSION
from .detail import parse_detail_page

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.I)
_DP_RE = re.compile(r"/dp/([A-Z0-9]{10})", re.I)
_RAW_FIELDS = (
    "title_es_raw", "current_price_raw", "original_price_raw",
    "monthly_bought_raw", "rating_raw", "review_count_raw",
    "availability_raw", "detail_bsr_raw", "seller_raw", "brand_raw",
    "selected_variation_raw", "date_first_available_raw", "product_url",
    "image_url", "detail_category_trail", "attributes",
    "feature_bullets_raw", "product_description_raw", "detail_bullets_raw",
)
_DISPLAY_FIELDS = (
    "title_zh", "category_l1_zh", "category_l2_zh", "category_l3_zh",
    "leaf_category_zh", "selected_variation_zh", "specification_zh",
    "product_details_zh", "feature_bullets_zh",
)


def _has_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return str(value).strip() != ""


def extract_page_asin(html: str) -> str:
    """Return the detail-page ASIN only when a product title is present."""
    soup = BeautifulSoup(html, "lxml")
    if soup.select_one("#productTitle") is None:
        return ""
    for selector in ("input#ASIN", "input[name=ASIN]", "input[name=asin]"):
        el = soup.select_one(selector)
        value = str(el.get("value") or "").strip() if el is not None else ""
        if _ASIN_RE.fullmatch(value):
            return value.upper()
    canonical = soup.select_one('link[rel="canonical"]')
    href = str(canonical.get("href") or "") if canonical is not None else ""
    match = _DP_RE.search(href)
    if match:
        return match.group(1).upper()
    for match in _DP_RE.finditer(str(soup)):
        return match.group(1).upper()
    return ""


def repair_cached_products(records: Iterable[Mapping], html_dir: str | Path):
    """Merge ASIN-matched cached detail-page evidence and re-normalize rows."""
    products = {normalize_asin(r.get("asin")): dict(r)
                for r in records if normalize_asin(r.get("asin"))}
    report = {
        "html_files": 0,
        "matched_pages": 0,
        "ignored_pages": 0,
        "changed_products": 0,
        "changed_fields": 0,
        "ignored_asins": [],
    }
    root = Path(html_dir)
    if not root.is_dir():
        return [dict(r) for r in records], report

    for path in sorted(root.glob("*.html")):
        report["html_files"] += 1
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            report["ignored_pages"] += 1
            continue
        asin = extract_page_asin(html)
        if not asin or asin not in products:
            report["ignored_pages"] += 1
            if asin:
                report["ignored_asins"].append(asin)
            continue
        parsed = parse_detail_page(html, asin)
        if parsed.get("is_captcha"):
            report["ignored_pages"] += 1
            continue
        target = products[asin]
        changed = 0
        for field in _RAW_FIELDS:
            if not _has_value(target.get(field)) and _has_value(parsed.get(field)):
                target[field] = parsed[field]
                changed += 1
        parsed_parent = normalize_asin(parsed.get("parent_asin"))
        existing_parent = normalize_asin(target.get("parent_asin"))
        if parsed_parent and parsed_parent != asin and (not existing_parent or existing_parent == asin):
            target["parent_asin"] = parsed_parent
            target["parent_asin_status"] = "confirmed"
            changed += 1
        report["matched_pages"] += 1
        if changed:
            report["changed_products"] += 1
            report["changed_fields"] += changed

    repaired = []
    for asin, record in products.items():
        normalized = normalize_product(record)
        # Preserve a display overlay only when it was produced from the exact
        # current Spanish evidence; old ASIN-only overlays are stale after a
        # parser repair and must be retranslated.
        if (record.get("translation_source_hash") == DeepSeekTranslator.source_hash(normalized)
                and record.get("translation_schema_version") == TRANSLATION_SCHEMA_VERSION):
            normalized.update({k: record[k] for k in _DISPLAY_FIELDS if _has_value(record.get(k))})
        repaired.append(normalized)
    return repaired, report
