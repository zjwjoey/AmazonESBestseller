"""Five saved-evidence ranking adapters with source-specific recognition.

Shared card/structured-payload primitives come from the existing ranking identity
parser. Its legacy ASIN deduplication/confirmation is intentionally not reused.
Every extracted slot remains an observation, including uncertain identities.
"""
from __future__ import annotations

from collections import Counter
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from ..collection.ranking_v2 import _recs_metadata
from ..monitoring.ranking_identity.adapters import (
    extract_embedded_supplemental_with_status, extract_server_candidates,
    select_ranking_cards,
)
from ..monitoring.ranking_identity.urls import (absolute_raw_url,
    asin_from_product_url, is_valid_asin, normalize_asin)
from .contracts import ORIGINS, RANK_SEMANTICS, make_observation, timestamp
from .sources import (browse_node_id, discover_sources, normalize_source_url,
                      same_source_category, source_type_from_url, source_type_value)
from .storage import digest

ADAPTER_VERSION = "ranking_adapters_v1.1.p0.1"


def _fold(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(char))


def _text(node) -> str | None:
    return node.get_text(" ", strip=True) or None if node is not None else None


def _integer(value) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d[\d.,\s]*", str(value))
    if not match:
        return None
    text = re.sub(r"[.,\s]", "", match.group())
    return int(text) if text.isdigit() and int(text) > 0 else None


def _positive_attr(soup, name: str) -> tuple[int | None, dict | None]:
    node = soup.select_one("[" + name + "]")
    if node is None:
        return None, None
    raw_value = str(node.get(name) or "")
    if not raw_value.isdigit() or int(raw_value) <= 0:
        return None, {"kind": "HTML_ATTRIBUTE", "locator": "[" + name + "]",
                      "raw": name + '="' + raw_value + '"', "value": raw_value,
                      "invalid": True}
    # Raw attr serialization may use single quotes. The value + locator remains
    # bound to the page digest; preserve the exact original marker when possible.
    return int(raw_value), {"kind": "HTML_ATTRIBUTE", "locator": "[" + name + "]",
                           "value": int(raw_value), "raw_value": raw_value}


def _response_body(response) -> tuple[str, int | None, str | None]:
    if isinstance(response, str):
        return response, 200, None
    if not isinstance(response, dict):
        return "", None, None
    envelope = response.get("response") if isinstance(response.get("response"), dict) else response
    return (str(envelope.get("body") or envelope.get("text") or ""),
            envelope.get("status_code", envelope.get("status", 200)),
            response.get("evidence_path"))


class RankingAdapter:
    source_type: str
    heading_patterns: tuple[str, ...] = ()
    alternate_contexts: tuple[str, ...] = ()

    def recognize(self, html: str, page_url: str) -> dict:
        soup = BeautifulSoup(html or "", "lxml")
        # Navigation mentions other lists on the same page. Match headings only.
        # Modern landing pages separate the marketing banner (e.g. 'Ideas
        # para regalos') from the actual list H1. Its scoped structural class
        # is evidence; arbitrary secondary H1s and footer copy are not.
        visible = (soup.select("#zg h1[class*='p13n-zg-banner-landing-page-header_style_zgH1Tag__']")
                   or soup.select("#zg_banner_text, .zg-banner-text") or soup.select("h1"))
        # A stale document title cannot overrule the actual list header. SVG
        # and product recommendation titles also must not become page headers.
        headings = [_text(node) or "" for node in (visible[:1] or soup.select("head > title"))]
        heading = next((text for text in headings if any(
            re.search(pattern, _fold(text)) for pattern in self.heading_patterns)), None)
        matched = source_type_from_url(page_url) == self.source_type and heading is not None
        return {"recognized": matched, "heading_raw": heading,
                "url_source_type": source_type_from_url(page_url)}

    def discover(self, html: str, page_url: str, **kwargs) -> list[dict]:
        return discover_sources(html, self.source_type, page_url, **kwargs)

    def _candidates(self, html: str, page_url: str, page_number: int | None,
                    evidence_path: str) -> tuple[list[dict], list, list[dict]]:
        soup = BeautifulSoup(html or "", "lxml")
        cards = select_ranking_cards(soup, page_url)
        extracted = extract_server_candidates(html, source_url=page_url,
            page_number=page_number, evidence_file=evidence_path)
        # Legacy list-specific containers are scoped, so recommendation cards
        # outside the actual ranked list cannot become observations.
        if not cards and self.alternate_contexts:
            nodes = [node for selector in self.alternate_contexts for node in soup.select(selector)]
            if nodes:
                fragment = "<div id='zg'>" + "".join(str(node) for node in nodes) + "</div>"
                wrapped = BeautifulSoup(fragment, "lxml")
                cards = select_ranking_cards(wrapped, page_url)
                extracted = extract_server_candidates(fragment, source_url=page_url,
                    page_number=page_number, evidence_file=evidence_path)
        by_slot = {row["card_index"]: row for row in extracted}
        # A visibly ranked slot lacking ASIN and URL is still an observation;
        # identity resolution must be able to quarantine it rather than lose it.
        for index, card in enumerate(cards):
            if index not in by_slot:
                raw_rank = _text(card.select_one(".zg-bdg-text, span.a-badge-text"))
                match = re.search(r"#\s*(\d+)", raw_rank or "")
                extracted.append({"card_index": index, "card_asin": "", "href_asin": "",
                    "raw_href": None, "rank": int(match.group(1)) if match else None,
                    "rank_raw": raw_rank, "evidence_file": evidence_path,
                    "evidence_source": "SERVER_RENDERED_CARD"})
        extracted.sort(key=lambda row: row["card_index"])
        client, embedded_acp, statuses = extract_embedded_supplemental_with_status(
            html, source_url=page_url, page_number=page_number, evidence_file=evidence_path)
        return extracted, cards, statuses + [{"evidence_source": "CLIENT_RECS",
            "candidate_count": len(client), "metadata_only": True},
            {"evidence_source": "EMBEDDED_ACP", "candidate_count": len(embedded_acp),
             "metadata_only": True}]

    def _observation_values(self, candidate: dict, card, page_url: str) -> dict:
        raw_url = candidate.get("raw_href")
        absolute = absolute_raw_url(raw_url, page_url) if raw_url else None
        normalized = normalize_source_url(absolute) if absolute else None
        card_asin_raw = normalize_asin(candidate.get("card_asin"))
        card_asin = card_asin_raw if is_valid_asin(card_asin_raw) else ""
        href_asin = normalize_asin(candidate.get("href_asin"))
        conflict = bool(is_valid_asin(card_asin) and is_valid_asin(href_asin)
                        and card_asin != href_asin)
        title = price = image = None
        if card is not None:
            title = _text(card.select_one("[data-product-title], .p13n-sc-truncate-desktop-type2, "
                ".p13n-sc-truncate, .a-link-normal .a-size-base, .a-size-base-plus"))
            image_node = card.select_one("img[src]")
            image = str(image_node.get("src")) if image_node else None
            if title is None and image_node:
                title = str(image_node.get("alt") or "") or None
            price = _text(card.select_one(".a-price .a-offscreen, .p13n-sc-price, [data-price]"))
        conflict_reasons = ["CARD_URL_ASIN_MISMATCH"] if conflict else []
        if raw_url and not normalized:
            conflict_reasons.append("PRODUCT_URL_NOT_AMAZON_ES")
        evidence = []
        if card_asin_raw:
            evidence.append({"kind": "CARD_ASIN", "value": card_asin_raw, "asin": card_asin or None,
                "origin_group": "RANKING_CARD", "evidence_path": candidate.get("evidence_file")})
        if raw_url:
            evidence.append({"kind": "RAW_PRODUCT_URL", "value": raw_url, "asin": href_asin or None,
                "origin_group": "PRODUCT_LINK", "evidence_path": candidate.get("evidence_file")})
        return {"rank_value": candidate.get("rank"), "rank_raw": candidate.get("rank_raw"),
            "observed_asin": card_asin or href_asin or None,
            "asin_observed": card_asin or href_asin or None,
            "asin_from_card": card_asin or None, "asin_from_card_raw": card_asin_raw or None,
            "asin_from_raw_url": href_asin or None,
            "product_url_raw": raw_url, "raw_product_url": raw_url,
            "normalized_product_url": normalized or None,
            "requested_product_url": absolute,
            "title_raw": title, "price_raw": price, "image_url_raw": image,
            "identity_status": "CONFLICT" if conflict_reasons else "UNCONFIRMED",
            "identity_conflict_reason": conflict_reasons,
            "identity_evidence": evidence, "representation": candidate.get("evidence_source"),
            "parser_version": self.version}

    @property
    def version(self) -> str:
        return ADAPTER_VERSION + ":" + self.source_type

    def parse(self, html: str, *, source: dict, run_id: str, snapshot_id: str,
              page_url: str, page_number: int | None, observed_at: str,
              evidence_path: str, http_status: int | None = 200,
              acp_responses: list | None = None, origin: str = "OFFLINE_FIXTURE",
              research_mapping: dict | None = None) -> dict:
        source = dict(source)
        source["source_type"] = source_type_value(source["source_type"])
        if source["source_type"] != self.source_type:
            raise ValueError("ADAPTER_SOURCE_TYPE_MISMATCH")
        if origin not in ORIGINS:
            raise ValueError("UNKNOWN_ORIGIN")
        access = detect_access_status(http_status, html).value
        recognized = self.recognize(html, page_url)
        page = {"source_id": source["source_id"], "source_type": self.source_type,
            "page_url": page_url, "page_number": page_number,
            "evidence_path": evidence_path, "evidence_sha256": digest(html.encode("utf-8")),
            "access_status": access, "parser_version": self.version,
            "rank_semantics": RANK_SEMANTICS[self.source_type],
            "recognized_page_type": recognized["recognized"],
            "source_type_verified": recognized["recognized"],
            "heading_raw": recognized["heading_raw"], "complete_evidence": [],
            "flags": [], "observed_at": timestamp(observed_at)["observed_at"],
            "origin": origin, "research_category": source.get("research_category"),
            "source_context_valid": same_source_category(page_url,
                source.get("actual_source_url") or source.get("root_url") or ""),
            "browse_node_id": source.get("browse_node_id"),
            "category_navigation_valid": None, "contiguous_ranks": False,
            "expected_rank_start": None, "expected_rank_end": None,
            "acp_required": False, "acp_complete": True, "acp_pending": []}
        if access != "NORMAL" or not recognized["recognized"] or not page["source_context_valid"]:
            page.update(status="BLOCKED" if access in {"CHALLENGE", "BLOCKED", "RATE_LIMITED"}
                        else "FAILED", actual_count=0, expected_count=None,
                        expected_count_evidence=None, page_complete=False)
            page["flags"].append("ACCESS_" + access if access != "NORMAL" else
                "PAGE_SOURCE_CATEGORY_MISMATCH" if not page["source_context_valid"] else "WRONG_PAGE_TYPE")
            return {"observations": [], "page": page, "pagination": [], "discovered_sources": []}

        soup = BeautifulSoup(html or "", "lxml")
        recs, acp_metadata = _recs_metadata(html)
        if acp_metadata:
            # Preserve both distinct server offsets and full recommendation
            # objects. Neither field proves the site's outbound POST contract;
            # bounded browser transport obtains that from a native request.
            grid = soup.select_one("[data-client-recs-list]")
            if grid is not None:
                acp_metadata["index_offset_raw"] = grid.get("data-index-offset")
                acp_metadata["offset_raw"] = grid.get("data-offset")
                acp_metadata["client_recs_raw"] = grid.get("data-client-recs-list")
        expected, expected_proof = _positive_attr(soup, "data-expected-count")
        if expected_proof:
            marker = re.search(r"data-expected-count\s*=\s*(['\"])\d+\1", html)
            if marker:
                expected_proof["raw"] = marker.group()
        if expected is None and recs:
            expected = len(recs)
            recs_node = soup.select_one("[data-client-recs-list]")
            expected_proof = {"kind": "CLIENT_RECS_METADATA", "locator": "[data-client-recs-list]",
                "value": expected, "raw": str(recs_node.get("data-client-recs-list") or "")}
        page.update(expected_count=expected, expected_count_evidence=expected_proof,
                    expected_count_source=expected_proof["kind"] if expected_proof else "UNKNOWN")
        if expected_proof and expected_proof.get("invalid"):
            page["flags"].append("EXPECTED_COUNT_METADATA_INVALID")
        if expected_proof:
            expected_proof["evidence_sha256"] = page["evidence_sha256"]
        continuous = soup.select_one("[data-contiguous-ranks='true']")
        start, start_proof = _positive_attr(soup, "data-rank-start")
        end, end_proof = _positive_attr(soup, "data-rank-end")
        if continuous is not None:
            page.update(contiguous_ranks=True, expected_rank_start=start, expected_rank_end=end)
            page["rank_continuity_evidence"] = {"kind": "HTML_ATTRIBUTE",
                "locator": "[data-contiguous-ranks='true']", "raw": "data-contiguous-ranks",
                "start": start_proof, "end": end_proof}
        nav_node = soup.select_one("[data-browse-node-id]")
        if nav_node and source.get("browse_node_id"):
            page["category_navigation_valid"] = str(nav_node.get("data-browse-node-id")) == str(source["browse_node_id"])
        elif source.get("browse_node_id"):
            nav_urls = [normalize_source_url(node.get("href") or "", base_url=page_url)
                        for node in soup.select("#zg_browseRoot a[href], .zg-browse-root a[href]")]
            if any(browse_node_id(url) == str(source["browse_node_id"]) for url in nav_urls):
                page["category_navigation_valid"] = True
        if page["category_navigation_valid"] is False:
            page["flags"].append("CATEGORY_NAVIGATION_MISMATCH")

        rows, cards, supplemental_statuses = self._candidates(html, page_url, page_number, evidence_path)
        page["supplemental_parse_status"] = supplemental_statuses
        page["rendered_card_count"] = len(cards)
        page["rendered_count"] = len(rows)
        if len(rows) < len(cards):
            page["flags"].append("PARSER_MISSED_CARD")
        if any(item.get("status") not in {None, "OK", "OK_URL_EVIDENCE"}
               for item in supplemental_statuses):
            page["flags"].append("SUPPLEMENTAL_PARSE_FAILED")
        observations = []
        for candidate in rows:
            slot = candidate["card_index"]
            card = cards[slot] if slot < len(cards) else None
            observations.append(make_observation(run_id=run_id, snapshot_id=snapshot_id,
                source=source, page_url=page_url, page_number=page_number,
                slot_position=slot, observed_at=observed_at, evidence_path=evidence_path,
                **self._observation_values(candidate, card, page_url)))

        # Saved ACP response HTML is a rendered representation. Metadata-only
        # CLIENT_RECS IDs do not prove cards were actually retrieved.
        continuation_count = 0
        for response in acp_responses or []:
            body, code, response_path = _response_body(response)
            response_access = detect_access_status(code, body).value
            if response_access != "NORMAL":
                page["access_status"] = response_access
                page["flags"].append("ACP_ACCESS_" + response_access)
                # Once a restricted continuation is seen, no later saved
                # response can turn this page back into NORMAL.
                break
            if not response_path:
                page["flags"].append("ACP_EVIDENCE_PATH_MISSING")
            candidates, response_cards, statuses = self._candidates(body, page_url, page_number,
                response_path or evidence_path)
            if len(candidates) < len(response_cards):
                page["flags"].append("ACP_PARSER_MISSED_CARD")
            page["supplemental_parse_status"].extend(statuses)
            if any(item.get("status") not in {None, "OK", "OK_URL_EVIDENCE"} for item in statuses):
                page["flags"].append("SUPPLEMENTAL_PARSE_FAILED")
            # Only reconcile against cards from earlier representations. Two
            # equal ranks inside this response are two anomaly slots, not one
            # synthetic duplicate representation to be silently discarded.
            earlier_representations = list(observations)
            for candidate in candidates:
                existing = next((row for row in earlier_representations if candidate.get("rank") is not None
                    and row["rank_value"] == candidate["rank"]), None)
                if existing:
                    if (existing.get("asin_from_card"), existing.get("asin_from_raw_url")) != (
                            candidate.get("card_asin") or None, candidate.get("href_asin") or None):
                        existing["identity_status"] = "CONFLICT"
                        existing["identity_conflict_reason"].append("REPRESENTATION_IDENTITY_MISMATCH")
                        existing["identity_evidence"].append({"kind": "ACP_CONFLICTING_CARD",
                            "value": candidate, "origin_group": "RANKING_CARD",
                            "evidence_path": response_path or evidence_path})
                        page["flags"].append("ACP_REPRESENTATION_CONFLICT")
                    continue
                slot = len(observations)
                card_index = candidate["card_index"]
                card = response_cards[card_index] if card_index < len(response_cards) else None
                candidate["evidence_source"] = "ACP_RENDERED_CARD"
                observations.append(make_observation(run_id=run_id, snapshot_id=snapshot_id,
                    source=source, page_url=page_url, page_number=page_number,
                    slot_position=slot, observed_at=observed_at,
                    evidence_path=response_path or evidence_path,
                    **self._observation_values(candidate, card, page_url)))
                continuation_count += 1
        page["acp_loaded_count"] = continuation_count
        actual = len(observations)
        page["actual_count"] = actual
        if recs:
            expected_asins = [normalize_asin(item.get("id") or item.get("asin")) for item in recs]
            actual_asins = [row.get("asin_from_card") or row.get("asin_from_raw_url") for row in observations]
            if all(is_valid_asin(value) for value in expected_asins) and (
                    any(value not in expected_asins for value in actual_asins)
                    or (actual == len(recs) and Counter(actual_asins) != Counter(expected_asins))):
                page["flags"].append("ACP_METADATA_IDENTITY_MISMATCH")
        if acp_metadata:
            page["acp_required"] = expected is None or actual < expected
            page["acp_complete"] = expected is not None and actual >= expected
            if expected is None:
                page["flags"].append("ACP_EXPECTATION_UNVERIFIED")
            elif actual < expected:
                page["flags"].append("ACP_INCOMPLETE")
                path = str(acp_metadata.get("path") or "")
                endpoint = urljoin(page_url, path.rstrip("/") + "/nextPage")
                if normalize_source_url(endpoint) and path:
                    ids = [str(item.get("id") or item.get("asin") or "") for item in recs]
                    payload = urlencode({"faceoutkataname": acp_metadata.get("faceout") or "GeneralFaceout",
                        "ids": json.dumps(ids, separators=(",", ":")),
                        "indexes": json.dumps(list(range(actual, expected)), separators=(",", ":")),
                        "offset": str(actual), "reftagprefix": acp_metadata.get("reftag") or ""})
                    page["acp_pending"].append({"url": endpoint, "method": "POST", "payload": payload,
                        "headers": {"accept": "text/html,application/xhtml+xml",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "x-amz-acp-params": acp_metadata.get("params") or ""},
                        "offset": actual, "count": expected - actual,
                        "metadata": dict(acp_metadata, entries=recs), "evidence_path": evidence_path})
                else:
                    page["flags"].append("ACP_ENDPOINT_INVALID")
        if not observations:
            page["flags"].append("PAGE_CONTENT_MISSING")
        if expected is not None and actual != expected:
            page["flags"].append("EXPECTED_COUNT_MISMATCH")
        ranks = [row["rank_value"] for row in observations if row["rank_value"] is not None]
        if len(ranks) != actual:
            page["flags"].append("RANK_MISSING")
        if any(count > 1 for count in Counter(ranks).values()):
            page["flags"].append("DUPLICATE_RANK")
        if page["contiguous_ranks"] and start is not None and end is not None:
            if set(ranks) != set(range(start, end + 1)):
                page["flags"].append("RANK_GAP")
        if any(not row.get("raw_product_url") for row in observations):
            page["flags"].append("PRODUCT_LINK_MISSING")
        if any(row.get("asin_from_card_raw") and not row.get("asin_from_card") for row in observations):
            page["flags"].append("CARD_ASIN_INVALID")
        if any(not row.get("asin_from_raw_url") for row in observations):
            page["flags"].append("PRODUCT_URL_IDENTITY_MISSING")
        if any(row["identity_status"] == "CONFLICT" for row in observations):
            page["flags"].append("IDENTITY_CONFLICT")

        pagination = []
        pagination_evidence = []
        for anchor in soup.select(".a-pagination a[href], .s-pagination-container a[href], a[rel='next'][href]"):
            target = normalize_source_url(anchor.get("href") or "", base_url=page_url)
            if source_type_from_url(target) != self.source_type:
                page["flags"].append("PAGINATION_SOURCE_TYPE_MISMATCH")
                continue
            if not same_source_category(target, page_url):
                page["flags"].append("PAGINATION_CATEGORY_MISMATCH")
                continue
            if target == normalize_source_url(page_url):
                page["flags"].append("PAGINATION_SELF_LINK")
                continue
            if target not in pagination:
                pagination.append(target)
                pagination_evidence.append({"url": target, "raw_href": anchor.get("href"),
                    "label_raw": _text(anchor)})
        page["pagination_evidence"] = pagination_evidence
        page["flags"] = list(dict.fromkeys(page["flags"]))
        if page["access_status"] != "NORMAL":
            status = "BLOCKED" if page["access_status"] in {"CHALLENGE", "BLOCKED", "RATE_LIMITED"} else "FAILED"
        elif page["flags"]:
            status = "PARTIAL" if observations else "FAILED"
        elif expected is None or page["category_navigation_valid"] is None:
            status = "UNVERIFIED"
        else:
            status = "COMPLETE"
            page["complete_evidence"] = [expected_proof,
                {"kind": "SOURCE_HEADING", "raw": recognized["heading_raw"]},
                {"kind": "RENDERED_SLOT_COUNT", "value": actual}]
        page.update(status=status, page_complete=status == "COMPLETE")
        return {"observations": observations, "page": page, "pagination": pagination,
            "discovered_sources": self.discover(html, page_url,
                research_mapping=research_mapping, evidence_path=evidence_path,
                observed_at=observed_at, origin=origin)}


class BestSellersAdapter(RankingAdapter):
    source_type = "BEST_SELLERS"
    heading_patterns = (r"\blos mas vendidos\b", r"\bbest sellers\b", r"\bbestsellers\b")
    alternate_contexts = ("#bestsellers", ".bestsellers")


class MoversShakersAdapter(RankingAdapter):
    source_type = "MOVERS_SHAKERS"
    heading_patterns = (r"\b(?:los )?productos del momento\b", r"\bmovers (?:and|&) shakers\b",
                        r"\bmovimientos y tendencias\b")
    alternate_contexts = ("#movers-and-shakers", ".zg-movers-shakers")

    def _observation_values(self, candidate: dict, card, page_url: str) -> dict:
        values = super()._observation_values(candidate, card, page_url)
        text = _text(card) or ""
        current_raw = previous_raw = improvement_raw = None
        if card is not None:
            current_raw = _text(card.select_one(".zg-sales-rank-current, [data-current-sales-rank], .zg-current-sales-rank"))
            previous_raw = _text(card.select_one(".zg-sales-rank-previous, [data-previous-sales-rank], .zg-previous-sales-rank"))
            improvement_raw = _text(card.select_one(".zg-percent-change, .zg-sales-rank-improvement, [data-rank-improvement]"))
        if current_raw is None:
            match = re.search(r"(?:rango|ranking|clasificacion)\s+(?:de\s+ventas\s+)?actual\s*:?\s*#?[\d.,]+", _fold(text))
            current_raw = match.group() if match else None
        if previous_raw is None:
            match = re.search(r"(?:rango|ranking|clasificacion)\s+(?:de\s+ventas\s+)?anterior\s*:?\s*#?[\d.,]+", _fold(text))
            previous_raw = match.group() if match else None
        if improvement_raw is None:
            match = re.search(r"[\d.,]+\s*%", text)
            improvement_raw = match.group() if match else None
        improvement = None
        if improvement_raw:
            match = re.search(r"([\d.,]+)\s*%", improvement_raw)
            if match:
                raw = match.group(1)
                # Spanish comma decimal, dot thousands; English fixtures may
                # use commas as thousands only when three trailing digits.
                if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
                    numeric = raw.replace(",", "")
                elif "," in raw:
                    numeric = raw.replace(".", "").replace(",", ".")
                elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
                    numeric = raw.replace(".", "")
                else:
                    numeric = raw
                improvement = float(numeric)
        values.update(current_sales_rank=_integer(current_raw), current_sales_rank_raw=current_raw,
            previous_sales_rank=_integer(previous_raw), previous_sales_rank_raw=previous_raw,
            sales_rank_improvement_percent=improvement,
            sales_rank_improvement_percent_raw=improvement_raw,
            sales_rank_change_semantics="SALES_RANK_IMPROVEMENT_PERCENT_NOT_SALES_GROWTH")
        return values


class NewReleasesAdapter(RankingAdapter):
    source_type = "NEW_RELEASES"
    heading_patterns = (r"\bultimas novedades\b", r"\bnovedades mas vendidas\b",
                        r"\bhot new releases\b", r"\bnew releases\b")
    alternate_contexts = ("#new-releases", ".zg-new-releases")


class MostWishedForAdapter(RankingAdapter):
    source_type = "MOST_WISHED_FOR"
    heading_patterns = (r"\blos mas deseados\b", r"\bmost wished for\b")
    alternate_contexts = ("#most-wished-for", ".zg-most-wished-for")


class MostGiftedAdapter(RankingAdapter):
    source_type = "MOST_GIFTED"
    heading_patterns = (r"\blos mas regalados\b", r"\bmost gifted\b")
    alternate_contexts = ("#most-gifted", ".zg-most-gifted")


_ADAPTERS = {cls.source_type: cls for cls in (BestSellersAdapter, MoversShakersAdapter,
    NewReleasesAdapter, MostWishedForAdapter, MostGiftedAdapter)}


def get_adapter(source_type: str) -> RankingAdapter:
    return _ADAPTERS[source_type_value(source_type)]()
