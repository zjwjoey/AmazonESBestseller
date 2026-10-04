"""Pure, multi-layout adapters for saved Amazon bestseller evidence."""
from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Mapping
from typing import Any

from bs4 import BeautifulSoup

from .models import IdentityCandidate
from .urls import asin_from_product_url, is_valid_asin, normalize_asin

SERVER_SELECTORS = (
    "#gridItemRoot",
    ".zg-grid-general-faceout",
    "[id^='p13n-asin-index-']",
)
HREF_SELECTORS = (
    "a[href*='/dp/']",
    "a[href*='/gp/product/']",
    "a[href*='/gp/aw/d/']",
    "a[href*='/product/']",
)
RANK_SELECTORS = (".zg-bdg-text", "span.a-badge-text")
_NUMBER_RE = re.compile(r"#\s*(\d+)")
_RANKING_CONTEXT_SELECTORS = (
    "#zg",
    "#zg_left_col1",
    "#bestsellers",
    ".bestsellers",
    ".zg-no-numbers",
    ".zg-list",
    ".p13n-desktop-grid",
)
_RANKING_MARKER_SELECTORS = (
    ".zg-bdg-text",
    "span.a-badge-text",
    "[data-ranking-card='true']",
    ".ranking-card",
)


def _text(node: Any) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def _unique_nodes(nodes: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[int] = set()
    for node in nodes:
        marker = id(node)
        if marker not in seen:
            seen.add(marker)
            result.append(node)
    return result


def select_ranking_cards(soup: BeautifulSoup, source_url: str = "") -> list[Any]:
    """Select one stable card layer, avoiding nested duplicate layouts."""
    grid_nodes = _unique_nodes(list(soup.select("#gridItemRoot")))
    if grid_nodes:
        return grid_nodes
    nodes = _unique_nodes([
        node for selector in SERVER_SELECTORS[1:]
        for node in soup.select(selector)
    ])
    if nodes:
        # A modern faceout can contain a p13n node. Keep the outer adapter so
        # the same card is not counted twice, while allowing genuinely mixed
        # layouts to contribute both cards.
        return [node for node in nodes
                if not any(node is not other and node in other.descendants for other in nodes)]
    # Generic data-asin is accepted only inside a known bestseller context.
    # A broad ``main`` fallback is deliberately forbidden: Amazon places
    # recommendations, sponsored cards and carousels in the same element.
    contexts = [node for selector in _RANKING_CONTEXT_SELECTORS
                for node in soup.select(selector)]
    if not contexts and re.search(r"/(?:zgbs|gp/bestsellers)/", source_url, re.I):
        # URL evidence alone is not enough to scan arbitrary page markup.  A
        # legacy page must still expose a ranking-shaped list/container.
        contexts = list(soup.select("ol, ul"))
    candidates = []
    for context in contexts:
        for node in context.select("[data-asin]"):
            if node is context:
                continue
            # A container name alone is not sufficient. Require a ranking
            # badge or an explicit ranking-card marker in the card ancestry.
            current = node
            marked = False
            while current is not None and current is not context.parent:
                if current is not context and current.select_one(", ".join(_RANKING_MARKER_SELECTORS)):
                    marked = True
                    break
                current = getattr(current, "parent", None)
            if marked:
                candidates.append(node)
    return _unique_nodes(candidates)


def _first_href(card: Any) -> str:
    for selector in HREF_SELECTORS:
        link = card.select_one(selector)
        if link is not None and link.get("href"):
            return str(link.get("href")).strip()
    fallback = card.select_one("a[href]") if card is not None else None
    if fallback is not None and fallback.get("href"):
        return str(fallback.get("href")).strip()
    return ""


def _card_asin(card: Any) -> str:
    raw = str(card.get("data-asin") or "").strip() if card else ""
    if not raw and card is not None:
        nested = card.select_one("[data-asin]")
        raw = str(nested.get("data-asin") or "").strip() if nested else ""
    return normalize_asin(raw)


def _rank(card: Any) -> tuple[int | None, str | None]:
    for selector in RANK_SELECTORS:
        node = card.select_one(selector)
        if node is None:
            continue
        raw = _text(node) or None
        match = _NUMBER_RE.search(raw or "")
        return (int(match.group(1)) if match else None), raw
    return None, None


def extract_server_candidates(
    html: str,
    *,
    source_url: str = "",
    page_number: int | None = 1,
    evidence_file: str | None = None,
    page_instance_id: str | None = None,
    representation_type: str = "RENDERED",
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    candidates: list[dict[str, Any]] = []
    for index, card in enumerate(select_ranking_cards(soup, source_url)):
        card_asin = _card_asin(card)
        raw_href = _first_href(card)
        href_asin = asin_from_product_url(raw_href)
        rank, rank_raw = _rank(card)
        asin = card_asin if is_valid_asin(card_asin) else href_asin
        if not asin and not card_asin and not href_asin and not raw_href:
            continue
        candidates.append(
            IdentityCandidate(
                asin=asin,
                asin_source=("CARD_DATA_ASIN" if is_valid_asin(card_asin)
                             else "PRODUCT_HREF_ASIN" if href_asin else "INVALID_ASIN"),
                card_asin=card_asin,
                href_asin=href_asin,
                raw_href=raw_href or None,
                rank=rank,
                rank_raw=rank_raw,
                page_number=page_number,
                card_index=index,
                source_url=source_url,
                evidence_source="SERVER_RENDERED_CARD",
                evidence_file=evidence_file,
                page_instance_id=page_instance_id,
                representation_type=representation_type,
            ).as_dict()
        )
    return candidates


def _jsonish(value: object) -> object:
    if not isinstance(value, str):
        return value
    text = html_lib.unescape(value).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _mapping_candidate(value: Mapping[str, Any], source: str, page_number: int | None,
                      source_url: str, evidence_file: str | None,
                      index: int, page_instance_id: str | None,
                      representation_type: str) -> dict[str, Any] | None:
    raw_href = value.get("href") or value.get("url") or value.get("product_url")
    raw_href = str(raw_href).strip() if raw_href else ""
    raw_asin = (value.get("asin") or value.get("ASIN") or value.get("data-asin")
                or value.get("originalAsin")
                or (value.get("id") if source == "CLIENT_RECS" else "") or "")
    card_asin = normalize_asin(raw_asin)
    href_asin = asin_from_product_url(raw_href)
    asin = card_asin if is_valid_asin(card_asin) else href_asin
    if not asin and not card_asin and not href_asin:
        return None
    rank_value = value.get("rank", value.get("position"))
    try:
        rank = int(rank_value) if rank_value is not None else None
    except (TypeError, ValueError):
        rank = None
    return IdentityCandidate(
        asin=asin,
        asin_source=(f"{source}_ASIN" if source else "STRUCTURED_ASIN"),
        card_asin=card_asin,
        href_asin=href_asin,
        raw_href=raw_href or None,
        rank=rank,
        rank_raw=str(value.get("rank_raw")) if value.get("rank_raw") is not None else None,
        page_number=page_number,
        card_index=index,
        source_url=source_url,
        evidence_source=source,
        evidence_file=evidence_file,
        page_instance_id=page_instance_id,
        representation_type=representation_type,
        raw={"structured": dict(value)},
    ).as_dict()


def extract_structured_candidates(
    payload: object,
    *,
    evidence_source: str,
    page_number: int | None = 1,
    source_url: str = "",
    evidence_file: str | None = None,
    page_instance_id: str | None = None,
    representation_type: str = "SUPPLEMENTAL",
) -> list[dict[str, Any]]:
    """Extract candidates from structured client-recs/ACP evidence only.

    This intentionally never searches arbitrary text for ten-character
    strings.  ASINs must come from a known identity field or a product URL.
    """
    candidates, _ = extract_structured_candidates_with_status(
        payload, evidence_source=evidence_source, page_number=page_number,
        source_url=source_url, evidence_file=evidence_file,
        page_instance_id=page_instance_id, representation_type=representation_type)
    return candidates


def extract_structured_candidates_with_status(
    payload: object,
    *,
    evidence_source: str,
    page_number: int | None = 1,
    source_url: str = "",
    evidence_file: str | None = None,
    page_instance_id: str | None = None,
    representation_type: str = "SUPPLEMENTAL",
) -> tuple[list[dict[str, Any]], str]:
    """Return structured candidates and an explicit parse status."""
    parse_status = "OK"
    if isinstance(payload, str):
        text = html_lib.unescape(payload).strip()
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            # A standalone product URL is explicit identity evidence. Any
            # other unparsed text is retained as a parse diagnostic only.
            payload = text
            parse_status = "INVALID_JSON"
    elif not isinstance(payload, (Mapping, list, tuple)):
        return [], "UNSUPPORTED_SCHEMA"
    result: list[dict[str, Any]] = []

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            candidate = _mapping_candidate(
                value, evidence_source, page_number, source_url, evidence_file,
                len(result), page_instance_id, representation_type)
            if candidate:
                result.append(candidate)
            for child in value.values():
                if isinstance(child, (Mapping, list, tuple)):
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            asin = asin_from_product_url(value)
            if asin:
                result.append(IdentityCandidate(
                    asin=asin,
                    asin_source=f"{evidence_source}_URL_ASIN",
                    href_asin=asin,
                    raw_href=value,
                    page_number=page_number,
                    card_index=len(result),
                    source_url=source_url,
                    evidence_source=evidence_source,
                    evidence_file=evidence_file,
                    page_instance_id=page_instance_id,
                    representation_type=representation_type,
                ).as_dict())

    visit(payload)
    if parse_status == "INVALID_JSON" and result:
        parse_status = "OK_URL_EVIDENCE"
    return result, parse_status


def extract_embedded_supplemental(
    html: str, *, source_url: str = "", page_number: int | None = 1,
    evidence_file: str | None = None, page_instance_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    client, acp, _ = extract_embedded_supplemental_with_status(
        html, source_url=source_url, page_number=page_number,
        evidence_file=evidence_file, page_instance_id=page_instance_id)
    return client, acp


def extract_embedded_supplemental_with_status(
    html: str, *, source_url: str = "", page_number: int | None = 1,
    evidence_file: str | None = None, page_instance_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "lxml")
    client: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for node in soup.select("[data-client-recs-list]"):
        value = node.get("data-client-recs-list")
        rows, status = extract_structured_candidates_with_status(
            value, evidence_source="CLIENT_RECS", page_number=page_number,
            source_url=source_url, evidence_file=evidence_file,
            page_instance_id=page_instance_id, representation_type="CLIENT_RECS")
        client.extend(rows)
        statuses.append({"evidence_source": "CLIENT_RECS", "status": status,
                         "evidence_file": evidence_file})
    acp: list[dict[str, Any]] = []
    for node in soup.select("script"):
        text = node.string or node.get_text()
        if re.search(r"\bACP\b|acp|client-recs", text, re.I):
            rows, status = extract_structured_candidates_with_status(
                text, evidence_source="ACP", page_number=page_number,
                source_url=source_url, evidence_file=evidence_file,
                page_instance_id=page_instance_id, representation_type="ACP")
            acp.extend(rows)
            statuses.append({"evidence_source": "ACP", "status": status,
                             "evidence_file": evidence_file})
    return client, acp, statuses


def expected_count_from_html(html: str) -> int | None:
    soup = BeautifulSoup(html, "lxml")
    client_counts: list[int] = []
    for node in soup.select("[data-client-recs-list]"):
        value = node.get("data-client-recs-list")
        try:
            payload = json.loads(html_lib.unescape(str(value or "")))
        except (TypeError, ValueError):
            continue
        if isinstance(payload, list) and payload:
            client_counts.append(len(payload))
    if client_counts:
        return max(client_counts)
    match = re.search(
        r"(?:expected[_-]?(?:count|items)|data-expected-count)\s*[:=]\s*[\"']?(\d+)",
        html, re.I,
    )
    return int(match.group(1)) if match else None
