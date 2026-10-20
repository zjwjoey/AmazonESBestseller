"""Offline, source-bound candidate parsing and immutable snapshot artifacts.

The module accepts already-saved HTML only. It reuses the mainline identity
candidate adapter for ranked pages and the V4 DOM adapter for Customer
Favorites. Its output is a candidate snapshot artifact, not a second database
or a declaration that sources are complete or products are confirmed SKUs.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from ..candidate_sources.v3_links import normalize_product_url_v3
from ..candidate_sources.v4_adapters import parse_v4_page
from ..candidate_sources.v4_sources import amazon_url, source_for_url
from ..handoff import CandidateObservation
from ..monitoring.ranking_identity.adapters import (
    extract_server_candidates,
    select_ranking_cards,
)

_RANKED_SOURCE_TYPES = {
    "BEST_SELLERS", "NEW_RELEASES", "MOVERS_SHAKERS", "MOST_WISHED_FOR", "MOST_GIFTED"
}
_SUPPORTED_SOURCE_TYPES = _RANKED_SOURCE_TYPES | {"CUSTOMER_FAVORITES"}
_SOURCE_PATHS = {
    "BEST_SELLERS": "/gp/bestsellers",
    "NEW_RELEASES": "/gp/new-releases",
    "MOVERS_SHAKERS": "/gp/movers-and-shakers",
    "MOST_WISHED_FOR": "/gp/most-wished-for",
    "MOST_GIFTED": "/gp/most-gifted",
}
_HEADINGS = {
    "BEST_SELLERS": (r"los mas vendidos", r"best\s*sellers", r"bestsellers"),
    "NEW_RELEASES": (r"ultimas novedades", r"new releases"),
    "MOVERS_SHAKERS": (r"productos del momento", r"movers (?:and|&) shakers"),
    "MOST_WISHED_FOR": (r"los mas deseados", r"most wished for"),
    "MOST_GIFTED": (r"los mas regalados", r"most gifted"),
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fold(value: str) -> str:
    import unicodedata
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).casefold()


def _page_heading_matches(html: str, source_type: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    text = _fold(" ".join(node.get_text(" ", strip=True)
                           for node in soup.select("h1, h2")))
    return any(re.search(pattern, text, re.I) for pattern in _HEADINGS[source_type])


def _card_facts(card: Any) -> dict[str, Any]:
    """Keep displayed observations raw; parsing is left to canonical pipeline."""
    title_node = card.select_one(
        ".p13n-sc-truncate-desktop-type2, .p13n-sc-truncate, .a-size-base-plus, h2"
    )
    title = title_node.get_text(" ", strip=True) if title_node else None
    if not title:
        image = card.select_one("img[alt]")
        title = str(image.get("alt") or "").strip() if image else None
    rating_nodes = card.select(".a-icon-alt, [aria-label]")
    rating_raw = next((str(node.get("aria-label") or node.get_text(" ", strip=True)).strip()
                       for node in rating_nodes
                       if re.search(r"\b(?:de|out of)\s*5\b", str(node.get("aria-label") or node.get_text(" ", strip=True)), re.I)), None)
    review_raw = next((str(node.get("aria-label") or node.get_text(" ", strip=True)).strip()
                       for node in rating_nodes
                       if re.search(r"(?:calificaciones|valoraciones|ratings)\b", str(node.get("aria-label") or node.get_text(" ", strip=True)), re.I)), None)
    price_node = card.select_one(".a-price .a-offscreen, .p13n-sc-price")
    price_raw = price_node.get_text(" ", strip=True) if price_node else None
    return {"product_title_raw": title or None, "rating_raw": rating_raw,
            "review_count_raw": review_raw, "current_price_raw": price_raw}


def _ranked_observations(html: str, *, source_type: str, source_url: str,
                         page_url: str, evidence_path: str, evidence_sha256: str,
                         snapshot_id: str, observed_at: str, page_number: int) -> tuple[list[dict], str]:
    if not _page_heading_matches(html, source_type):
        return [], "SOURCE_PAGE_TYPE_UNVERIFIED"
    parsed = extract_server_candidates(
        html, source_url=page_url, page_number=page_number,
        evidence_file=evidence_path, page_instance_id=snapshot_id,
    )
    soup = BeautifulSoup(html, "lxml")
    cards = select_ranking_cards(soup, page_url)
    observations = []
    for index, candidate in enumerate(parsed):
        raw_url = candidate.get("raw_href") or ""
        link = normalize_product_url_v3(raw_url, base_url=page_url)
        card = cards[candidate.get("card_index", -1)] if candidate.get("card_index", -1) < len(cards) else None
        card_asin = str(candidate.get("card_asin") or "").upper()
        href_asin = str(candidate.get("href_asin") or "").upper()
        mismatch = bool(card_asin and href_asin and card_asin != href_asin)
        rank = candidate.get("rank")
        row = {
            **link,
            **(_card_facts(card) if card is not None else {}),
            "source_type": source_type,
            "source_url": source_url,
            "source_page_url": page_url,
            "page_number": page_number,
            "snapshot_id": snapshot_id,
            "observed_at": observed_at,
            "ranking_position": rank,
            "ranking_position_raw": candidate.get("rank_raw"),
            "ranking_semantics": {
                "BEST_SELLERS": "BEST_SELLER_LIST_POSITION",
                "NEW_RELEASES": "NEW_RELEASE_LIST_POSITION",
                "MOVERS_SHAKERS": "MOVERS_SHAKERS_LIST_POSITION",
                "MOST_WISHED_FOR": "WISH_LIST_POSITION",
                "MOST_GIFTED": "GIFT_LIST_POSITION",
            }[source_type],
            "bestseller_rank": rank if source_type == "BEST_SELLERS" else None,
            "bestseller_rank_raw": candidate.get("rank_raw") if source_type == "BEST_SELLERS" else None,
            "asin_observed": card_asin or href_asin or "",
            "card_asin_observed": card_asin,
            "url_asin_observed": href_asin,
            "identity_status": "UNCONFIRMED",
            "ranking_link_identity_status": (
                "MISMATCH" if mismatch else "MATCH" if card_asin and href_asin and card_asin == href_asin
                else "UNCONFIRMED"
            ),
            "observation_id": "ranking_observation_" + _sha256(
                (snapshot_id + "|" + source_url + "|" + str(page_number) + "|"
                 + str(candidate.get("card_index", index)) + "|" + str(raw_url)).encode("utf-8")
            )[:24],
            "evidence_path": evidence_path,
            "evidence_sha256": evidence_sha256,
            "candidate_page_completeness": "UNVERIFIED",
        }
        observations.append(row)
    return observations, "OBSERVED_UNVERIFIED" if observations else "NO_CANDIDATES_UNVERIFIED"


def _favorites_observations(html: str, *, source_type: str, source_subtype: str,
                            page_url: str, evidence_path: str, snapshot_id: str,
                            observed_at: str, page_number: int) -> tuple[list[dict], dict, str]:
    source = source_for_url(page_url, source_type=source_type, source_subtype=source_subtype)
    source["page_urls"] = [page_url]
    parsed = parse_v4_page(
        html, source=source, page_url=page_url, run_id=snapshot_id,
        snapshot_id=snapshot_id, observed_at=observed_at, evidence_path=evidence_path,
        http_status=None, origin="OFFLINE_FIXTURE", page_number=page_number,
    )
    page = dict(parsed["page"])
    observations = []
    for row in parsed["observations"]:
        row = dict(row)
        row["identity_status"] = "UNCONFIRMED"
        row["candidate_page_completeness"] = "UNVERIFIED"
        row["ranking_position"] = row.get("ranking_position")
        row["ranking_semantics"] = row.get("ranking_semantics") or source_subtype
        row["asin_observed"] = row.get("asin_from_card") or row.get("asin_from_raw_url") or ""
        row["source_root_url"] = source["actual_source_url"]
        row["source_url"] = source["actual_source_url"]
        row["source_type"] = source_type
        row["source_page_url"] = page_url
        row["page_number"] = page_number
        observations.append(row)
    status = ("BLOCKED" if page.get("access_status") in {
        "BLOCKED", "CHALLENGE", "RATE_LIMITED", "CAPTCHA", "BOT_BLOCK", "ACCESS_DENIED"
    } else "OBSERVED_UNVERIFIED" if observations else "NO_CANDIDATES_UNVERIFIED")
    return observations, page, status


def parse_saved_candidate_pages(pages: Sequence[Mapping[str, Any]], *, snapshot_id: str,
                                observed_at: str) -> dict[str, Any]:
    """Parse checksummed local HTML into source-bound, unconfirmed candidates."""
    try:
        instant = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("OBSERVED_AT_INVALID") from exc
    if instant.tzinfo is None:
        raise ValueError("OBSERVED_AT_MUST_BE_TIMEZONE_AWARE")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(snapshot_id)):
        raise ValueError("SNAPSHOT_ID_INVALID")

    all_observations: list[dict[str, Any]] = []
    page_results: list[dict[str, Any]] = []
    html_by_name: dict[str, bytes] = {}
    for index, spec in enumerate(pages):
        if not isinstance(spec, Mapping):
            raise TypeError("candidate page specification must be a mapping")
        source_type = str(spec.get("source_type") or "").upper()
        if source_type not in _SUPPORTED_SOURCE_TYPES:
            raise ValueError("CANDIDATE_SOURCE_TYPE_UNSUPPORTED:%s" % source_type)
        page_url = str(spec.get("page_url") or spec.get("source_url") or "")
        source_url = str(spec.get("source_url") or page_url)
        if not amazon_url(page_url) or not amazon_url(source_url):
            raise ValueError("AMAZON_ES_SOURCE_URL_REQUIRED")
        if source_type in _SOURCE_PATHS:
            page_path = urlsplit(page_url).path.rstrip("/").casefold()
            source_path = _SOURCE_PATHS[source_type]
            if page_path != source_path and not page_path.startswith(source_path + "/"):
                raise ValueError("SOURCE_URL_TYPE_MISMATCH:%s" % source_type)
        page_number = int(spec.get("page_number") or 1)
        filename = Path(str(spec.get("html_path") or "")).name
        if not filename:
            raise ValueError("SAVED_HTML_PATH_REQUIRED")
        body = Path(str(spec["html_path"])).read_bytes()
        actual_sha = _sha256(body)
        expected_sha = str(spec.get("sha256") or "").lower()
        if not expected_sha or expected_sha != actual_sha:
            raise ValueError("SAVED_HTML_SHA256_MISMATCH:%s" % filename)
        if filename in html_by_name and html_by_name[filename] != body:
            raise ValueError("CANDIDATE_EVIDENCE_FILENAME_COLLISION:%s" % filename)
        html_by_name[filename] = body
        evidence_path = "html/" + filename
        html = body.decode("utf-8", errors="replace")
        access_state = detect_access_status(spec.get("status_code"), html).value
        parsed_page: dict[str, Any] = {
            "source_type": source_type, "source_url": source_url, "page_url": page_url,
            "page_number": page_number, "snapshot_id": snapshot_id,
            "observed_at": observed_at, "evidence_path": evidence_path,
            "source_input_path": str(spec.get("html_path") or ""),
            "evidence_sha256": actual_sha, "access_state": access_state,
            "source_completeness_status": "UNVERIFIED", "status": "UNVERIFIED",
            "candidate_count": 0,
        }
        if access_state in {"BLOCKED", "CHALLENGE", "RATE_LIMITED", "CAPTCHA", "BOT_BLOCK", "ACCESS_DENIED"}:
            parsed_page.update(status="BLOCKED", source_completeness_status="BLOCKED",
                                error="ACCESS_RESTRICTED")
            observations = []
        elif source_type == "CUSTOMER_FAVORITES":
            source_subtype = str(spec.get("source_subtype") or "")
            if not source_subtype:
                raise ValueError("CUSTOMER_FAVORITES_SOURCE_SUBTYPE_REQUIRED")
            observations, v4_page, source_status = _favorites_observations(
                html, source_type=source_type, source_subtype=source_subtype,
                page_url=page_url, evidence_path=evidence_path, snapshot_id=snapshot_id,
                observed_at=observed_at, page_number=page_number,
            )
            parsed_page.update(v4_page)
            parsed_page.update(source_type=source_type, source_url=source_url,
                                page_url=page_url, page_number=page_number,
                                snapshot_id=snapshot_id, observed_at=observed_at,
                                source_input_path=str(spec.get("html_path") or ""),
                                evidence_path=evidence_path, evidence_sha256=actual_sha,
                                source_completeness_status="UNVERIFIED", status=source_status)
        else:
            observations, source_status = _ranked_observations(
                html, source_type=source_type, source_url=source_url, page_url=page_url,
                evidence_path=evidence_path, evidence_sha256=actual_sha,
                snapshot_id=snapshot_id, observed_at=observed_at, page_number=page_number,
            )
            parsed_page.update(status=source_status,
                               source_completeness_status="UNVERIFIED")
        parsed_page["candidate_count"] = len(observations)
        all_observations.extend(observations)
        page_results.append(parsed_page)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_observations:
        handoff = CandidateObservation.from_ranking_record(row).to_dict()
        grouped[str(handoff["candidate_id"])].append(handoff)
    candidates = []
    for candidate_id, rows in sorted(grouped.items()):
        first = rows[0]
        memberships = [{key: row.get(key) for key in (
            "candidate_observation_id", "candidate_source_type", "candidate_source_root_url",
            "candidate_source_page_url", "candidate_snapshot_id", "candidate_observed_at",
            "candidate_ranking_position", "candidate_ranking_position_raw",
            "candidate_evidence_file", "evidence_sha256", "ranking_semantics",
            "candidate_link_identity_status",
        )} for row in rows]
        candidates.append({
            "candidate_id": candidate_id,
            "candidate_identity_status": "UNCONFIRMED",
            "strict_buyable_confirmed": False,
            "original_product_urls": list(dict.fromkeys(
                row.get("candidate_original_product_url") for row in rows
                if row.get("candidate_original_product_url"))),
            "normalized_product_url": first.get("candidate_normalized_product_url") or "",
            "candidate_asins_observed": list(dict.fromkeys(
                row.get("candidate_asin_observed") for row in rows
                if row.get("candidate_asin_observed"))),
            "source_memberships": memberships,
            "observations": rows,
            "observation_count": len(rows),
            "requestable_by_url": any(row.get("candidate_original_product_url") for row in rows),
            "identity_confirmation_required": True,
        })
    status = "BLOCKED" if any(row.get("status") == "BLOCKED" for row in page_results) else \
             "UNVERIFIED" if candidates else "EMPTY_UNVERIFIED"
    return {
        "snapshot_id": snapshot_id,
        "snapshot_status": status,
        "source_complete": False,
        "ready_for_detail_plan": False,
        "source_statuses": page_results,
        "candidate_count": len(candidates),
        "observation_count": len(all_observations),
        "candidates": candidates,
        "html_by_name": html_by_name,
    }


def _write_once(path: Path, body: bytes) -> None:
    if path.exists():
        raise FileExistsError("candidate snapshot artifact already exists: %s" % path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def build_candidate_snapshot(pages: Sequence[Mapping[str, Any]], output_root: str | Path,
                             *, snapshot_id: str, observed_at: str,
                             target_candidates: int | None = None) -> dict[str, Any]:
    """Persist an append-only candidate artifact; never advances main authority."""
    if target_candidates is not None and (type(target_candidates) is not int or target_candidates < 1):
        raise ValueError("TARGET_CANDIDATES_INVALID")
    parsed = parse_saved_candidate_pages(pages, snapshot_id=snapshot_id, observed_at=observed_at)
    root = Path(output_root) / "candidate_snapshots" / snapshot_id
    root.mkdir(parents=True, exist_ok=False)
    html_dir = root / "html"
    html_dir.mkdir()
    file_refs = []
    for name, body in sorted(parsed.pop("html_by_name").items()):
        target = html_dir / name
        _write_once(target, body)
        file_refs.append({"name": "html/" + name, "bytes": len(body), "sha256": _sha256(body)})
    candidates_bytes = (json.dumps(parsed["candidates"], ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n").encode("utf-8")
    _write_once(root / "candidates.json", candidates_bytes)
    target_count = target_candidates
    manifest = {key: value for key, value in parsed.items() if key != "candidates"}
    manifest.update({
        "schema_version": 1,
        "snapshot_mode": "SAVED_HTML_REPLAY",
        "target_candidates": target_count,
        "target_shortfall": max(0, target_count - parsed["candidate_count"]) if target_count else None,
        "source_complete": False,
        "ready_for_detail_plan": False,
        "candidate_file": "candidates.json",
        "candidate_file_sha256": _sha256(candidates_bytes),
        "files": file_refs,
    })
    _write_once(root / "manifest.json", (json.dumps(manifest, ensure_ascii=False,
        indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"path": root, "manifest": manifest,
            "candidates": parsed["candidates"]}


__all__ = ["parse_saved_candidate_pages", "build_candidate_snapshot"]
