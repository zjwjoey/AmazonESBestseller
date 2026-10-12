"""Lightweight collection QA, separate from the formal Source/Release Gate."""
from __future__ import annotations

from collections import Counter
import re

from bs4 import BeautifulSoup

from ..identity import asin_from_url
from .detail_identity import (extract_detail_identity_signals, normalized_identity_value,
                              review_visible_asin_identity)

RULES_VERSION = "link-first-staged-fields-v1"


def resolve_selected_identity(html: str, requested_url: str, final_url: str) -> dict:
    signals = extract_detail_identity_signals(html)
    child = set(signals["input_asins"]) | set(signals["current_asins"])
    parent = set(signals["parent_asins"])
    canonical = set(signals["canonical_asins"])
    final = asin_from_url(final_url)
    requested = asin_from_url(requested_url)
    from ..collection.detail_v2 import _variation_evidence
    variation = _variation_evidence(html)
    family = {str(p["asin"]).upper() for p in variation["products"]}
    result = {"status": "BLOCK", "asin": "", "signals": signals,
              "requested_url": requested_url, "final_url": final_url,
              "code": "SELECTED_IDENTITY_UNCONFIRMED"}
    if len(child) != 1:
        result["code"] = "SELECTED_IDENTITY_CONFLICT" if child else result["code"]
        return result
    selected = next(iter(child))
    allowed = {selected} | (parent if len(parent) == 1 else set())
    if canonical - allowed or (final and final not in allowed):
        result["code"] = "LANDING_IDENTITY_CONFLICT"
        return result
    if requested and requested not in allowed and not {requested, selected} <= family:
        result["code"] = "UNRELATED_REDIRECT"
        return result
    # A single hidden input is not enough without a separate corroborating anchor.
    corroborated = (bool(signals["input_asins"] and signals["current_asins"])
                    or final == selected or selected in canonical)
    if not corroborated:
        return result
    result.update(status="CONFIRMED", asin=selected, code="SELECTED_CHILD_CONFIRMED",
                  parent_asin=next(iter(parent)) if len(parent) == 1 and selected not in parent else "",
                  variation_family_asins=sorted(family | {selected} | parent))
    return result


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def _comparison(value) -> str:
    return re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", re.sub(r"\s+", " ", str(value or ""))).strip()


def dom_detail_pairs(html: str) -> list[tuple[str, str]]:
    """Independent row-local audit: never zip all labels and values globally."""
    soup = BeautifulSoup(html, "lxml")
    rows, seen = [], set()
    for container in soup.select("#productOverview_feature_div, #productOverview, #prodDetails, #detailBullets_feature_div, #productDetails_detailBullets_sections1"):
        for tr in container.select("tr"):
            if id(tr) in seen or any("feedback" in str(p.get("id") or "").lower() for p in tr.parents):
                continue
            seen.add(id(tr))
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) == 2 and _text(cells[0]) and _text(cells[1]):
                rows.append((_comparison(_text(cells[0])), _comparison(_text(cells[1]))))
        for dt in container.select("dt"):
            dd = dt.find_next_sibling()
            if dd and dd.name == "dd" and _text(dt) and _text(dd):
                rows.append((_comparison(_text(dt)), _comparison(_text(dd))))
    return rows


def audit_detail_fields(html: str, row: dict, identity: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")
    issues = []

    def add(code, field, severity="WARN", **evidence):
        issues.append(dict(code=code, field=field, severity=severity, evidence=evidence))

    if identity["status"] != "CONFIRMED" or normalized_identity_value(row.get("asin")) != identity.get("asin"):
        add(identity.get("code") or "IDENTITY_CONFLICT", "asin", "BLOCK")
    if not row.get("title_es_raw"):
        add("PARSER_MISSED" if soup.select_one("#productTitle") and _text(soup.select_one("#productTitle")) else "SOURCE_MISSING", "title_es_raw", "BLOCK")
    attrs = row.get("attributes")
    if not isinstance(attrs, list) or any(not isinstance(a, dict) or not {"section", "label_raw", "value_raw", "position", "source"} <= set(a) for a in attrs):
        add("STRUCTURE_ERROR", "attributes", "BLOCK")
        attrs = []
    expected = dom_detail_pairs(html)
    actual = [(_comparison(a.get("label_raw")), _comparison(a.get("value_raw"))) for a in attrs]
    if Counter(expected) - Counter(actual):
        add("PARSER_MISSED", "attributes", "BLOCK", missing=list((Counter(expected) - Counter(actual)).elements()))
    # Ordered duplicate labels must retain the observed value order.
    for label in {pair[0] for pair in expected}:
        expected_values = [v for k, v in expected if k == label]
        actual_values = [v for k, v in actual if k == label]
        if actual_values != expected_values:
            add("FIELD_BINDING_ERROR", "attributes", "BLOCK", label=label, expected=expected_values, actual=actual_values)
    for field, selectors in (("feature_bullets_raw", "#feature-bullets, #featurebullets_feature_div, #pqv-feature-bullets"),
                             ("detail_bullets_raw", "#detailBulletsWrapper_feature_div")):
        observed = next(([ _comparison(_text(li)) for li in block.select("li") if _text(li)]
                         for block in soup.select(selectors) if any(_text(li) for li in block.select("li"))), [])
        values = row.get(field)
        if not isinstance(values, list) or [_comparison(v) for v in values] != observed:
            add("BULLETS_BINDING_ERROR", field, "BLOCK", expected_count=len(observed))
        elif not values:
            add("SOURCE_MISSING", field)
    if identity.get("asin"):
        reviewed = review_visible_asin_identity(row, identity.get("signals"))
        if reviewed["status"] == "IDENTITY_CONFLICT":
            add("DETAIL_ASIN_CONFLICT", "attributes", "BLOCK", review=reviewed)
        elif reviewed["status"] == "PARENT_DETAIL_REVIEW_REQUIRED":
            add("PARENT_DETAIL_SCOPE_REVIEW", "attributes", review=reviewed)
    # Immediate mapping audit: visible source values must not become empty RAW fields.
    source_selectors = {
        "brand_raw": "#bylineInfo", "image_url": "#landingImage, #imgBlkFront, #imageBlock_feature_div img",
        "rating_raw": "#acrPopover .a-icon-alt, #averageCustomerReviews_feature_div .a-icon-alt",
        "review_count_raw": "#acrCustomerReviewText",
        "current_price_raw": "#corePrice_feature_div .a-price .a-offscreen, #corePriceDisplay_desktop_feature_div .a-price .a-offscreen, #apex_price .apex-pricetopay-value",
    }
    for field, selector in source_selectors.items():
        if row.get(field):
            continue
        nodes = soup.select(selector)
        visible = any(_text(n) if field != "image_url" else n.get("src") or n.get("data-old-hires") for n in nodes)
        # Editorial/author bylines are not brand evidence. Never guess from a title.
        if field == "brand_raw":
            add("SOURCE_MISSING", field)
        elif visible:
            add("PARSER_MISSED", field, "BLOCK", selector=selector)
        else:
            add("SOURCE_MISSING", field)
    for field in ("category_l1", "category_l2", "category_l3", "leaf_category"):
        if not row.get(field):
            add("SOURCE_MISSING", field)
    for label, value in actual:
        if re.search(r"peso|weight", label, re.I) and value and not re.search(r"\d.*(?:kg|gram|kilogram|\bg\b|libr|pound|oz)", value, re.I):
            add("SOURCE_VALUE_TYPE_REVIEW", "attributes", label=label, value=value)
    return {"rules_version": RULES_VERSION,
            "status": "BLOCK" if any(i["severity"] == "BLOCK" for i in issues) else "WARN" if issues else "PASS",
            "issues": issues, "dom_attribute_count": len(expected), "parsed_attribute_count": len(attrs)}
