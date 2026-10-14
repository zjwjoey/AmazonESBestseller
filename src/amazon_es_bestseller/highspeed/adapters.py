"""One-pass link-first parsing of saved ranking pages and ACP representations."""
from __future__ import annotations

from collections import Counter
import json
import re
import unicodedata
import uuid
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from ..multisource.contracts import RANK_SEMANTICS, timestamp
from ..multisource.storage import digest
from .links import candidate_id, normalize_product_url
from .sources import (discover_sources, normalize_source_url, same_source_category,
                      source_type_from_url, source_type_value)

ADAPTER_VERSION = "link_first_adapters_v2.2.title_priority"
V3_ADAPTER_VERSION = "link_first_adapters_v3.card_bound"


def parser_version(source):
    return V3_ADAPTER_VERSION if source.get("parser_profile") == "V3" else ADAPTER_VERSION
_HEADINGS = {
    "BEST_SELLERS": (r"los mas vendidos", r"best\s*sellers"),
    "MOVERS_SHAKERS": (r"productos del momento", r"movers (?:and|&) shakers", r"movimientos y tendencias"),
    "NEW_RELEASES": (r"ultimas novedades", r"novedades mas vendidas", r"(?:hot )?new releases"),
    "MOST_WISHED_FOR": (r"los mas deseados", r"most wished for"),
    "MOST_GIFTED": (r"los mas regalados", r"most gifted"),
}
_CARD_SELECTOR = "#gridItemRoot, .zg-grid-general-faceout, [id^='p13n-asin-index-'], [data-ranking-card='true'], .ranking-card"
_CONTEXT_SELECTOR = "#zg, #zg_left_col1, #bestsellers, .bestsellers, .zg-list, .p13n-desktop-grid, #movers-and-shakers, #new-releases, #most-wished-for, #most-gifted"


def _text(node):
    return node.get_text(" ", strip=True) if node is not None else None


def _fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _cards(soup, fragment=False):
    """Choose outer card nodes in DOM order, independent of identity attributes."""
    contexts = soup.select(_CONTEXT_SELECTOR)
    nodes = soup.select(_CARD_SELECTOR)
    accepted = []
    accepted_ids = set()
    context_ids = {id(n) for n in contexts}
    for node in nodes:
        parents = list(node.parents)
        if any(id(parent) in accepted_ids for parent in parents):
            continue
        if not fragment and contexts and not any(id(parent) in context_ids for parent in parents):
            continue
        accepted.append(node)
        accepted_ids.add(id(node))
    # Legacy ranked list items without a data identity field remain valid.
    if not accepted:
        for context in contexts:
            for badge in context.select(".zg-bdg-text, span.a-badge-text"):
                card = badge.find_parent("li")
                if card is not None and id(card) not in accepted_ids:
                    accepted.append(card)
                    accepted_ids.add(id(card))
    return accepted


def _scope(card, page_url):
    parents = list(card.parents)
    ancestor = next((p for p in parents if p.get("data-card-metrics-id")
        or p.get("data-carousel-id") or p.get("data-category-url")), None)
    if ancestor is None:
        ancestor = next((p for p in parents if "carousel" in " ".join(p.get("class", []))), None)
    if ancestor is None:
        return page_url
    category_anchor = ancestor.select_one("h2 a[href], h3 a[href], a[data-category-link][href]")
    if category_anchor is None:
        category_anchor = next((a for a in ancestor.select("a[href]")
            if source_type_from_url(urljoin(page_url, a.get("href") or "")) == source_type_from_url(page_url)), None)
    category = ancestor.get("data-category-url") or (category_anchor.get("href") if category_anchor else None)
    if category:
        return normalize_source_url(category, base_url=page_url) or page_url
    marker = ancestor.get("data-card-metrics-id") or ancestor.get("data-carousel-id") or ancestor.get("id")
    if not marker:
        # Stable DOM sibling-position path, no title/image/identifier inference.
        marker = "/".join(str(sum(1 for _ in p.previous_siblings)) for p in [ancestor] + list(ancestor.parents))
    return page_url + "#ranking-group=" + str(marker)


def _raw_href(card):
    links = card.select("a[href]")
    for anchor in links:
        raw = str(anchor.get("href") or "")
        if normalize_product_url(raw).get("is_valid"):
            return raw
    return str(links[0].get("href")) if links else None


_TITLE_SELECTORS = (
    "[data-product-title]",
    "[class*='p13n-sc-css-line-clamp']",
    ".p13n-sc-truncate-desktop-type2",
    ".p13n-sc-truncate",
    ".a-size-base-plus",
    ".a-link-normal .a-size-base",
)
_METADATA_CLASSES = ("price", "a-icon-star", "a-icon-alt", "rating", "review-count")


def _non_title_text(text):
    value = re.sub(r"\s+", " ", _fold(text or "")).strip()
    number = r"\d[\d., ]*"
    money = r"(?:[€$£]\s*" + number + r"|" + number + r"\s*(?:€|eur|usd|gbp|\$|£))"
    return bool(re.fullmatch(r"(?:(?:\d+\s+ofertas?\s+desde|desde|from|a partir de|precio:?)[ :]*\s*)?" + money + r"(?:\s*[-–]\s*" + money + r")?", value)
        or re.fullmatch(r"[\d.,]+\s*(?:de|out of)\s*5\s*(?:estrellas|stars)(?:[, ]+[\d.,]+\s*(?:calificaciones|ratings))?", value)
        or re.fullmatch(r"[\d.,]+\s*(?:calificaciones|ratings|valoraciones|resenas|reviews)", value))


def _title_from_card(card):
    """Prioritize actual title DOM, then literal image alt; never price order.

    Selectors are evaluated separately because a comma selector returns the
    earliest matching node in DOM order, even when that node is a price label.
    """
    for selector in _TITLE_SELECTORS:
        for match_index, node in enumerate(card.select(selector)):
            text = _text(node)
            if not text or _non_title_text(text):
                continue
            ancestors = [node]
            for parent in node.parents:
                if parent is card:
                    break
                ancestors.append(parent)
            if any(any(marker in cls for marker in _METADATA_CLASSES)
                   for parent in ancestors for cls in parent.get("class", [])):
                continue
            anchor = node.find_parent("a", href=True)
            if anchor is not None and not normalize_product_url(anchor.get("href"))["is_valid"]:
                continue
            if selector in _TITLE_SELECTORS[-2:] and re.fullmatch(r"[\d.,\s]+", text):
                continue
            return text, {"kind": "SOURCE_VISIBLE_PRODUCT_TITLE", "selector": selector,
                "selector_match_index": match_index, "text_raw": text}
    for match_index, image in enumerate(card.select("img[alt]")):
        text = str(image.get("alt") or "").strip()
        anchor = image.find_parent("a", href=True)
        if text and not _non_title_text(text) and (anchor is None or normalize_product_url(anchor.get("href"))["is_valid"]):
            return text, {"kind": "SOURCE_IMAGE_ALT", "selector": "img[alt]",
                "selector_match_index": match_index, "text_raw": text}
    return None, {"kind": "SOURCE_TITLE_MISSING", "text_raw": None}


def _title_from_card_v3(card, raw_href):
    """Literal title for this exact product anchor, never a neighbouring target."""
    selectors = _TITLE_SELECTORS + ("img[alt]",)
    targets = {a.get("href") for a in card.select("a[href]")
               if normalize_product_url(a.get("href"))["is_valid"]}
    for selector in selectors:
        for index, node in enumerate(card.select(selector)):
            image = selector == "img[alt]"
            text = str(node.get("alt") or "").strip() if image else _text(node)
            if not text or _non_title_text(text):
                continue
            ancestors = [node]
            for parent in node.parents:
                if parent is card:
                    break
                ancestors.append(parent)
            if any(any(m in cls for m in _METADATA_CLASSES)
                   for p in ancestors for cls in p.get("class", [])):
                continue
            anchor = node.find_parent("a", href=True)
            if anchor is not None and anchor.get("href") != raw_href:
                continue
            if anchor is None and (selector != "[data-product-title]" or targets != {raw_href}):
                continue
            if selector in _TITLE_SELECTORS[-2:] and re.fullmatch(r"[\d.,\s]+", text):
                continue
            status = "TRUNCATED" if re.search(r"(?:\.{3}|\u2026)\s*$", text) else "IMAGE_ALT" if image else "SOURCE_VISIBLE"
            return text, {"kind": "SOURCE_IMAGE_ALT" if image else "SOURCE_VISIBLE_PRODUCT_TITLE",
                "selector": selector, "selector_match_index": index, "text_raw": text,
                "raw_product_url": raw_href, "title_status": status}
    return None, {"kind": "SOURCE_TITLE_MISSING", "text_raw": None,
                  "raw_product_url": raw_href, "title_status": "MISSING"}


def _v3_advertisement(card):
    return any(n.get("data-ad-marker") or n.get("data-ad-id") or n.get("data-component-type") == "sp-sponsored-result"
               for n in [card] + list(card.parents))


def _content_state_v3(soup, page, rows, pending):
    """Describe zero capacity without turning it into COMPLETE or a safe skip."""
    if page["access_status"] != "NORMAL":
        return "ACCESS_RESTRICTED", None
    if not page["source_type_verified"] or not page["source_context_valid"]:
        return "WRONG_SOURCE", None
    if rows:
        return "PRODUCT_CARDS", None
    grid = soup.select_one("#zg-right-col .p13n-desktop-grid")
    phrases = {"BEST_SELLERS": "(?:de los )?mas vendidos", "MOST_WISHED_FOR": "de los mas deseados",
               "MOST_GIFTED": "de los mas regalados", "NEW_RELEASES": "(?:de las )?ultimas novedades",
               "MOVERS_SHAKERS": "(?:de los )?productos del momento"}
    if grid is not None and not pending:
        try:
            empty = json.loads(grid.get("data-client-recs-list", "null")) == []
        except (ValueError, TypeError):
            empty = False
        if (empty and grid.select_one("#endOfList") is not None
                and not grid.select_one(_CARD_SELECTOR + ", .zg-bdg-text, [data-expected-count], [data-acp-path]")
                and not any(normalize_product_url(a["href"], base_url=page["page_url"])["is_valid"] for a in grid.select("a[href]"))):
            for node in grid.find_all("h4", recursive=False):
                message = _fold(" ".join(node.get_text(" ", strip=True).split()))
                if re.fullmatch(r"lo sentimos, no hay productos " + phrases[page["source_type"]] + r" disponibles en esta categoria\. vuelve mas tarde\.", message):
                    return "SOURCE_EMPTY", {"kind": "OFFICIAL_EMPTY_COMPONENT", "selector": "#zg-right-col .p13n-desktop-grid > h4",
                        "message_raw": _text(node), "evidence_sha256": page["evidence_sha256"]}
    if pending:
        return "NATIVE_CONTINUATION_UNRESOLVED", None
    right = soup.select_one("#zg-right-col")
    if right is not None and right.find(True) is None and not right.get_text(strip=True):
        if soup.select_one("#zg-left-col a[href], #zg_browseRoot a[href]"):
            return "NAVIGATION_ONLY", None
    return "PARSER_EMPTY_UNVERIFIED", None


def _parse_cards(soup, *, source, run_id, snapshot_id, page_url, page_number,
                 observed_at, evidence_path, sha, representation, offset=0, fragment=False):
    rows = []
    for index, card in enumerate(_cards(soup, fragment)):
        if source.get("parser_profile") == "V3" and _v3_advertisement(card):
            continue
        badge = card.select_one(".zg-bdg-text, span.a-badge-text, [data-ranking-position]")
        rank_raw = _text(badge)
        match = re.search(r"#\s*(\d+)", rank_raw or "")
        rank = int(match.group(1)) if match else None
        if rank is None and badge is not None:
            raw = badge.get("data-ranking-position")
            rank = int(raw) if raw and str(raw).isdigit() and int(raw) > 0 else None
        raw = _raw_href(card)
        if source.get("parser_profile") == "V3":
            from .v3_links import normalize_product_url_v3
            link = normalize_product_url_v3(raw, base_url=page_url)
        else:
            link = normalize_product_url(raw, base_url=page_url)
        title, title_evidence = (_title_from_card_v3(card, raw) if source.get("parser_profile") == "V3"
                                 else _title_from_card(card))
        scope = _scope(card, page_url)
        category_wrapper = next((p for p in card.parents if p.get("data-card-metrics-id") or p.get("data-category-url")), None)
        category_heading = _text(category_wrapper.select_one("h2, h3")) if category_wrapper else None
        slot = index + offset
        obs_key = json.dumps([snapshot_id, source["source_id"], normalize_source_url(page_url), representation, index], separators=(",", ":"))
        row = dict(link, observation_id="obs_" + uuid.uuid5(uuid.NAMESPACE_URL, obs_key).hex,
            candidate_id=candidate_id(link["normalized_product_url"]) if link["is_valid"] else None,
            final_product_url=None, run_id=run_id, snapshot_id=snapshot_id,
            source_id=source["source_id"], source_type=source["source_type"],
            source_url=source.get("actual_source_url") or page_url, page_url=page_url,
            research_category=source.get("research_category"), amazon_category=category_heading or source.get("amazon_category"),
            category_scope_evidence={"ranking_context_url": scope, "heading_raw": category_heading,
                "evidence_sha256": sha},
            category_path=source.get("category_path", []), product_title_raw=title,
            product_title_evidence={**title_evidence, "evidence_path": evidence_path, "evidence_sha256": sha},
            ranking_position=rank, rank_value=rank, rank_raw=rank_raw, rank_context=scope,
            rank_semantics=RANK_SEMANTICS[source["source_type"]], page_number=page_number,
            slot_position=slot, representation_slot=index, representation=representation,
            observed_at=observed_at, evidence_path=evidence_path, evidence_sha256=sha,
            dedup_status="UNIQUE_LINK_CANDIDATE" if link["is_valid"] else "INVALID_PRODUCT_LINK",
            parser_version=parser_version(source))
        if source.get("parser_profile") == "V3":
            row["product_title_status"] = title_evidence["title_status"]
            from .ratings import extract_rating_fields
            row.update(extract_rating_fields(card, raw_product_url=raw,
                evidence_path=evidence_path, evidence_sha256=sha, observed_at=observed_at,
                representation=representation, representation_slot=index))
        if source["source_type"] == "MOVERS_SHAKERS":
            for field, selector in (("current_sales_rank_raw", ".zg-sales-rank-current, [data-current-sales-rank]"),
                                    ("previous_sales_rank_raw", ".zg-sales-rank-previous, [data-previous-sales-rank]"),
                                    ("sales_rank_improvement_percent_raw", ".zg-percent-change, [data-rank-improvement]")):
                row[field] = _text(card.select_one(selector))
            row["sales_rank_change_semantics"] = "SALES_RANK_IMPROVEMENT_NOT_SALES_GROWTH"
        rows.append(row)
    return rows


def parse_page(html, *, source, run_id, snapshot_id=None, page_url, page_number,
               observed_at, evidence_path, http_status=200, origin="OFFLINE_FIXTURE",
               acp_responses=None, research_mapping=None):
    source = dict(source)
    kind = source["source_type"] = source_type_value(source["source_type"])
    snapshot_id = snapshot_id or run_id
    observed_at = timestamp(observed_at)["observed_at"]
    if origin not in {"LIVE_CAPTURE", "HISTORICAL_REPLAY", "OFFLINE_FIXTURE"}:
        raise ValueError("UNKNOWN_ORIGIN")
    html = html or ""
    sha = digest(html.encode("utf-8"))
    soup = BeautifulSoup(html, "lxml")
    headings = (soup.select("#zg-right-col [class*='card-title'] > h1") or soup.select("#zg h1[class*='style_zgH1Tag__']") or
        soup.select("#zg_banner_text, .zg-banner-text") or soup.select("h1") or soup.select("head > title"))
    heading = _text(headings[0]) if headings else None
    recognized = source_type_from_url(page_url) == kind and bool(heading and any(re.search(p, _fold(heading)) for p in _HEADINGS[kind]))
    access = detect_access_status(http_status, html).value
    context_valid = same_source_category(page_url, source.get("actual_source_url") or page_url)
    page = {"source_id": source["source_id"], "source_type": kind, "page_url": page_url,
        "page_number": page_number, "evidence_path": evidence_path, "evidence_sha256": sha,
        "observed_at": observed_at, "origin": origin, "access_status": access,
        "recognized_page_type": recognized, "source_type_verified": recognized,
        "source_context_valid": context_valid, "heading_raw": heading,
        "research_category": source.get("research_category"), "amazon_category": source.get("amazon_category"),
        "rank_semantics": RANK_SEMANTICS[kind], "parser_version": parser_version(source), "flags": []}
    rows, pagination, pending, discovered = [], [], [], []
    expected, proof = None, None
    if access != "NORMAL" or not recognized or not context_valid:
        page["flags"].append("ACCESS_" + access if access != "NORMAL" else "WRONG_PAGE_TYPE" if not recognized else "PAGE_SOURCE_CATEGORY_MISMATCH")
    else:
        rows = _parse_cards(soup, source=source, run_id=run_id, snapshot_id=snapshot_id,
            page_url=page_url, page_number=page_number, observed_at=observed_at,
            evidence_path=evidence_path, sha=sha, representation="SERVER")
        expected_node = soup.select_one("[data-expected-count]")
        recs_node = (soup.select_one("#zg-right-col .p13n-desktop-grid[data-client-recs-list]")
            or soup.select_one(".p13n-desktop-grid[data-client-recs-list]") or soup.select_one("[data-client-recs-list]")) if source.get("parser_profile") == "V3" else soup.select_one("[data-client-recs-list]")
        entries = []
        if recs_node is not None:
            try:
                entries = json.loads(recs_node.get("data-client-recs-list") or "[]")
                if not isinstance(entries, list):
                    raise ValueError()
            except (ValueError, TypeError):
                entries = []
                page["flags"].append("ACP_METADATA_INVALID")
        if expected_node and str(expected_node.get("data-expected-count")).isdigit():
            expected = int(expected_node["data-expected-count"])
            proof = {"kind": "HTML_ATTRIBUTE", "locator": "[data-expected-count]", "value": expected, "evidence_sha256": sha}
        elif entries:
            expected = len(entries)
            proof = {"kind": "CLIENT_RECS_METADATA", "locator": "[data-client-recs-list]", "value": expected, "evidence_sha256": sha}
        for response_index, response in enumerate(acp_responses or []):
            envelope = response.get("response", response) if isinstance(response, dict) else {"body": response}
            body = envelope.get("body", envelope.get("text", "")) or ""
            code = envelope.get("status_code", envelope.get("status", 200))
            state = detect_access_status(code, body).value
            if state != "NORMAL":
                page["access_status"] = state
                page["flags"].append("ACP_ACCESS_" + state)
                break
            response_path = response.get("evidence_path") if isinstance(response, dict) else None
            if not response_path:
                page["flags"].append("ACP_EVIDENCE_PATH_MISSING")
                if source.get("parser_profile") == "V3":
                    continue
            response_soup = BeautifulSoup(body, "lxml")
            response_time = timestamp(envelope.get("observed_at",response.get("observed_at",observed_at)))["observed_at"] if source.get("parser_profile") == "V3" and isinstance(response,dict) else observed_at
            response_rows = _parse_cards(response_soup, source=source, run_id=run_id, snapshot_id=snapshot_id,
                page_url=page_url, page_number=page_number, observed_at=response_time,
                evidence_path=response_path or evidence_path, sha=digest(body.encode("utf-8")),
                representation="ACP_" + str(response_index), offset=len(rows), fragment=True)
            if (source.get("parser_profile") == "V3" and soup.select_one("[data-card-metrics-id^='p13n-zg-list-carousel']")
                    and soup.select_one(".p13n-desktop-grid") is None):
                binding = response.get("ranking_context_binding", {}) if isinstance(response, dict) else {}
                contexts = {r["rank_context"] for r in rows if r["representation"] == "SERVER"}
                context = binding.get("ranking_context_url")
                if (context not in contexts or binding.get("source_id") != source["source_id"]
                        or binding.get("page_evidence_sha256") != sha):
                    page["flags"].append("ACP_CAROUSEL_CONTEXT_UNVERIFIED")
                    continue
                for row in response_rows:
                    row["rank_context"] = context
                    row["category_scope_evidence"] = {"ranking_context_url": context,
                        "page_evidence_sha256": sha, "page_evidence_path": evidence_path,
                        "source_id": source["source_id"], "acp_evidence_sha256": row["evidence_sha256"],
                        "kind": "EXPLICIT_NATIVE_REQUEST_CONTEXT_BINDING"}
            rows.extend(response_rows)
        # Carousel ACP has a distinct native contract. Do not pretend the first
        # unrelated carousel endpoint is the list-grid continuation endpoint.
        root = next((n for n in recs_node.parents if n.get("data-acp-path")), None) if recs_node is not None else None
        # Metadata entries are opaque request evidence, never product candidates.
        distinct = {(r["rank_context"], r["ranking_position"]) for r in rows if r["ranking_position"] is not None}
        actual = len(distinct) if rows and all(r["ranking_position"] is not None for r in rows) else len(rows)
        if (root is not None or entries) and (expected is None or actual < expected):
            # Modern second pages may expose the full opaque client-rec list
            # without data-acp-path. Missing endpoint is a native-discovery
            # requirement, never proof that the incomplete prefix is final.
            path = str(root.get("data-acp-path") or "") if root is not None else None
            endpoint = urljoin(page_url, path.rstrip("/") + "/nextPage") if path else None
            grid = soup.select_one(".p13n-desktop-grid") or recs_node
            metadata = {"path": path, "params": root.get("data-acp-params") or "" if root is not None else "",
                "faceout": grid.get("data-faceoutkataname") if grid else None,
                "reftag": grid.get("data-reftag") if grid else None, "entries": entries,
                "index_offset_raw": recs_node.get("data-index-offset") if recs_node else None,
                "offset_raw": recs_node.get("data-offset") if recs_node else None,
                "client_recs_raw": recs_node.get("data-client-recs-list") if recs_node else None}
            if endpoint is None and entries:
                pending.append({"url": None, "method": "POST", "metadata": metadata,
                    "offset": actual, "count": max(0, expected - actual) if expected is not None else None,
                    "request_contract": "NATIVE_REQUEST_DISCOVERY_REQUIRED", "evidence_path": evidence_path})
                page["flags"].append("ACP_NATIVE_ENDPOINT_UNDISCOVERED")
            elif path and normalize_source_url(endpoint):
                pending.append({"url": endpoint, "method": "POST", "metadata": metadata,
                    "offset": actual, "count": max(0, expected - actual) if expected is not None else None,
                    "request_contract": "NATIVE_REQUEST_REQUIRED", "evidence_path": evidence_path})
            else:
                page["flags"].append("ACP_ENDPOINT_INVALID")
        for anchor in soup.select(".a-pagination a[href], .s-pagination-container a[href], a[rel='next'][href]"):
            url = normalize_source_url(anchor.get("href"), base_url=page_url)
            if url != normalize_source_url(page_url) and same_source_category(url, page_url) and url not in pagination:
                pagination.append(url)
        discovered = discover_sources(html, kind, page_url, research_mapping=research_mapping,
            evidence_path=evidence_path, observed_at=observed_at, origin=origin, soup=soup)
        if source.get("parser_profile") == "V3":
            carousel_groups = []
            server_cards = _cards(soup)
            for node in soup.select("[data-card-metrics-id^='p13n-zg-list-carousel'] [data-a-carousel-options]"):
                raw_options = node.get("data-a-carousel-options")
                try:
                    options = json.loads(raw_options)
                    ids = options.get("ajax", {}).get("id_list")
                    if not isinstance(ids, list):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    page["flags"].append("CAROUSEL_METADATA_INVALID")
                    continue
                cards = [card for card in server_cards if any(parent is node for parent in card.parents)]
                if not cards:
                    # Navigation-only tiles/opaque IDs never become product rows.
                    continue
                context = _scope(cards[0], page_url)
                group = {"ranking_context_url": context, "expected_count": len(ids),
                    "actual_count": len({r["ranking_position"] for r in rows if r["rank_context"] == context and r["ranking_position"] is not None}),
                    "evidence_path": evidence_path, "evidence_sha256": sha,
                    "metadata_raw": raw_options, "selector": "[data-a-carousel-options]"}
                carousel_groups.append(group)
                if group["actual_count"] < group["expected_count"]:
                    pending.append({"url": None, "method": "POST", "offset": group["actual_count"],
                        "count": group["expected_count"] - group["actual_count"],
                        "request_contract": "NATIVE_REQUEST_DISCOVERY_REQUIRED", "evidence_path": evidence_path,
                        "metadata": {"carousel": True, "ranking_context_url": context,
                            "carousel_options_raw": raw_options, "entries": ids,
                            "evidence_sha256": sha}})
            page["carousel_groups"] = carousel_groups
    # Preserve duplicate representations but don't call repeated carousel rank #1
    # a page-global identity or rank collision.
    slots = {}
    for row in rows:
        key = (row["rank_context"], row["ranking_position"])
        if row["ranking_position"] is not None:
            slots.setdefault(key, []).append(row)
    for group in slots.values():
        if len({r["representation"] for r in group}) < len(group):
            page["flags"].append("DUPLICATE_RANK_IN_CONTEXT")
        if len({r["normalized_product_url"] for r in group}) > 1:
            page["flags"].append("RANK_LINK_REPRESENTATION_CONFLICT")
    actual = len(slots) if rows and all(r["ranking_position"] is not None for r in rows) else len(rows)
    if not rows and access == "NORMAL" and recognized:
        page["flags"].append("PAGE_CONTENT_MISSING")
    if any(not r["raw_product_url"] for r in rows):
        page["flags"].append("PRODUCT_LINK_MISSING")
    if any(not r["is_valid"] for r in rows):
        page["flags"].append("INVALID_PRODUCT_LINK")
    if any(r["ranking_position"] is None for r in rows):
        page["flags"].append("RANK_MISSING")
    if expected is not None and actual != expected:
        page["flags"].append("EXPECTED_COUNT_MISMATCH")
    if pending:
        page["flags"].append("ACP_INCOMPLETE")
    flags = page["flags"] = list(dict.fromkeys(page["flags"]))
    status = "BLOCKED" if page["access_status"] in {"CHALLENGE", "BLOCKED", "RATE_LIMITED"} else "FAILED" if page["access_status"] != "NORMAL" or not recognized or not context_valid else "PARTIAL" if flags and rows else "FAILED" if flags else "UNVERIFIED" if expected is None else "COMPLETE"
    page.update(status=status, page_complete=status == "COMPLETE", raw_card_count=len(rows), actual_count=actual,
        expected_count=expected, expected_count_evidence=proof,
        expected_count_source=proof["kind"] if proof else "UNKNOWN", valid_link_count=sum(r["is_valid"] for r in rows),
        acp_pending=pending, acp_required=bool(pending), acp_complete=not pending,
        complete_evidence=[proof] if status == "COMPLETE" else [])
    if source.get("parser_profile") == "V3":
        state, state_proof = _content_state_v3(soup, page, rows, pending)
        page.update(page_content_state=state, page_content_evidence=state_proof)
    return {"page": page, "observations": rows, "pagination": pagination,
            "discovered_sources": discovered, "acp_pending": pending}
