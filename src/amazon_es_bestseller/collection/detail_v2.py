"""Parser V2 enrichment that preserves the existing DetailEvidence contract."""
from __future__ import annotations

import json
import re
from html import unescape

from bs4 import BeautifulSoup

from ..categories.provenance import category_evidence_from_detail
from ..identity import asin_from_url, resolve_identity
from .detail import parse_detail_page


def _json_after(text: str, marker: str, opener: str = "{"):
    start = 0
    closer = "}" if opener == "{" else "]"
    while True:
        index = text.find(marker, start)
        if index < 0:
            return None
        index += len(marker)
        while index < len(text) and text[index] in " :\n\t\r":
            index += 1
        if index >= len(text) or text[index] != opener:
            start = index
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(index, len(text)):
            char = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(unescape(text[index:end + 1]))
                    except ValueError:
                        break
        start = index


def _variation_evidence(html: str) -> dict:
    display = _json_after(html, '"dimensionValuesDisplayData"') or {}
    labels = _json_after(html, '"variationDisplayLabels"') or {}
    dimensions = _json_after(html, '"dimensions"', "[") or list(labels.keys())
    values = _json_after(html, '"variationValues"') or {}
    current = (re.search(r'"currentAsin"\s*:\s*"([A-Z0-9]{10})"', html, re.I) or [None, ""])[1].upper()
    parent = (re.search(r'"parentAsin"\s*:\s*"([A-Z0-9]{10})"', html, re.I) or [None, ""])[1].upper()
    family = {str(asin).upper() for asin in display if re.fullmatch(r"[A-Z0-9]{10}", str(asin), re.I)}
    if current:
        family.add(current)
    if parent:
        family.add(parent)
    dimension_rows = []
    for key in dimensions:
        dimension_rows.append({"key": key, "label": labels.get(key, key),
                               "values": values.get(key) or []})
    products = []
    for asin, selected_values in display.items():
        products.append({"asin": str(asin).upper(), "values": selected_values,
                         "is_current": str(asin).upper() == current})
    return {"current_asin": current or None, "parent_asin": parent or None,
            "family_asins": sorted(family), "dimensions": dimension_rows,
            "products": products, "count": len(products)}


def _page_asins(html: str) -> set[str]:
    soup = BeautifulSoup(html or "", "lxml")
    values = set()
    for element in soup.select("input#ASIN, input[name='ASIN'], input#productAsin, [data-asin]"):
        value = str(element.get("value") or element.get("data-asin") or "").upper().strip()
        if re.fullmatch(r"[A-Z0-9]{10}", value):
            values.add(value)
    for value in re.findall(r"/dp/([A-Z0-9]{10})", html or "", re.I):
        values.add(value.upper())
    return values


def _canonical_asin(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    link = soup.select_one('link[rel="canonical"]')
    return asin_from_url(link.get("href") if link else "")


def parse_detail_evidence_v2(html: str, requested_asin: str, *, requested_url: str = "",
                             final_url: str = "", ranking_context: dict | None = None) -> dict:
    base = parse_detail_page(html, requested_asin)
    variation = _variation_evidence(html)
    page_asins = _page_asins(html)
    canonical_asin = _canonical_asin(html)
    identity = resolve_identity(
        ranking_asin=requested_asin, requested_asin=requested_asin,
        requested_url=requested_url, final_url_asin=final_url,
        canonical_asin=canonical_asin,
        embedded_asins=page_asins, parsed_detail_asin=variation.get("current_asin") or "",
        parent_asin=variation.get("parent_asin") or base.get("parent_asin") or "",
        variation_family_asins=variation.get("family_asins") or [],
    )
    evidence = category_evidence_from_detail(base, ranking_context)
    result = dict(base)
    result.update({
        "parser_version": "collection.detail_v2",
        "variation_evidence": variation,
        "variation_family_asins": variation["family_asins"],
        "requested_asin": identity["requested_asin"],
        "resolved_asin": identity["resolved_asin"],
        "identity_status": identity["identity_status"],
        "identity_status_code": identity["identity_status_code"],
        "identity_evidence": identity["identity_evidence"],
        "category_evidence": evidence.to_dict(),
        # Explicitly preserve ordered duplicate labels; this is the raw
        # evidence list, never a label->value dict.
        "ordered_detail_evidence": list(base.get("attributes") or []),
    })
    return result
