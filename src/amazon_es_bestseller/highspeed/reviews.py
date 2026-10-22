"""Offline validation of explicit human destination/variant review evidence.

This module never navigates, unwraps redirects, manufactures destinations or
promotes title similarity into identity. Projection is separate from snapshots.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from .links import candidate_id, normalize_product_url


def _instant(value):
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError()
        return instant
    except (ValueError, TypeError):
        raise ValueError("REVIEW_AWARE_TIMESTAMP_REQUIRED") from None


def _file_bytes(path, expected_sha, kind):
    if not path or not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise ValueError(kind + "_HASH_REQUIRED")
    target = Path(path)
    if not target.is_absolute() or not target.is_file():
        raise ValueError(kind + "_FILE_REQUIRED")
    body = target.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha:
        raise ValueError(kind + "_HASH_MISMATCH")
    return body


def _selected_evidence(soup, evidence):
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("INDEPENDENT_SELECTED_VARIANT_EVIDENCE_REQUIRED")
    fingerprints = []
    for item in evidence:
        if not isinstance(item, dict) or not item.get("selector"):
            raise ValueError("VARIANT_SELECTOR_REQUIRED")
        try:
            matches = soup.select(item["selector"])
        except Exception:
            raise ValueError("VARIANT_SELECTOR_INVALID") from None
        if len(matches) != 1:
            raise ValueError("VARIANT_EVIDENCE_AMBIGUOUS")
        node = matches[0]
        selected = (node.has_attr("selected") or node.has_attr("checked")
            or node.get("aria-selected") == "true" or node.get("aria-checked") == "true"
            or node.get("data-selected") == "true" or "a-button-selected" in node.get("class", [])
            or ("selection" in node.get("class", []) and node.find_parent(id=lambda x: bool(x and x.startswith("variation_")))))
        if not selected or node.has_attr("disabled"):
            raise ValueError("VARIANT_EVIDENCE_NOT_SELECTED")
        attr = item.get("attribute")
        actual = node.get(attr) if attr else node.get_text(" ", strip=True)
        expected = item.get("value_raw") if attr else item.get("text_raw")
        if not isinstance(expected, str) or not expected.strip() or actual != expected:
            raise ValueError("VARIANT_EVIDENCE_VALUE_MISMATCH")
        fingerprints.append([attr or "text", actual])
    return json.dumps(fingerprints, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _receipt(item, candidate, reviewed_at, independent):
    raw = _file_bytes(item.get("receipt_path"), item.get("receipt_sha256"), "NAVIGATION_RECEIPT")
    try:
        receipt = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("NAVIGATION_RECEIPT_INVALID") from None
    if not isinstance(receipt, dict) or receipt.get("origin") != "LIVE_CAPTURE":
        raise ValueError("ACTUAL_NAVIGATION_ORIGIN_REQUIRED")
    if _instant(receipt.get("observed_at")) > reviewed_at:
        raise ValueError("REVIEW_PRECEDES_NAVIGATION")
    request = normalize_product_url(receipt.get("request_url"))
    final = normalize_product_url(receipt.get("final_url"))
    if (not request["is_valid"] or request["normalized_product_url"] != candidate.get("normalized_product_url")
            or not final["is_valid"] or final["reason"] == "REDIRECT_DESTINATION_UNVERIFIED"):
        raise ValueError("NAVIGATION_CANDIDATE_DESTINATION_MISMATCH")
    html_bytes = _file_bytes(receipt.get("html_path"), receipt.get("html_sha256"), "PRODUCT_PAGE")
    try:
        html = html_bytes.decode("utf-8")
    except UnicodeError:
        raise ValueError("PRODUCT_PAGE_ENCODING_INVALID") from None
    if (type(receipt.get("http_status")) is not int or receipt["http_status"] != 200
            or receipt.get("access_status") != "NORMAL" or detect_access_status(200, html).value != "NORMAL"):
        raise ValueError("PRODUCT_PAGE_ACCESS_NOT_NORMAL")
    soup = BeautifulSoup(html, "lxml")
    evidence = receipt.get("product_page_evidence") or {}
    title, purchase = evidence.get("title") or {}, evidence.get("purchase_control") or {}
    title_selector = title.get("selector")
    purchase_selector = purchase.get("selector")
    if title_selector not in {"#productTitle", "h1#title", "h1[data-product-title]"}:
        raise ValueError("PRODUCT_TITLE_LOCATOR_REQUIRED")
    if purchase_selector not in {"#add-to-cart-button", "#buy-now-button", "input[name='submit.add-to-cart']", "button[name='submit.add-to-cart']"}:
        raise ValueError("PRODUCT_PURCHASE_CONTROL_REQUIRED")
    titles, controls = soup.select(title_selector), soup.select(purchase_selector)
    if len(titles) != 1 or not title.get("text_raw") or titles[0].get_text(" ", strip=True) != title["text_raw"]:
        raise ValueError("PRODUCT_TITLE_EVIDENCE_MISMATCH")
    if len(controls) != 1 or controls[0].has_attr("disabled") or controls[0].get("aria-disabled") == "true":
        raise ValueError("PRODUCT_PURCHASE_CONTROL_UNAVAILABLE")
    variant = _selected_evidence(soup, receipt.get("selection_evidence")) if independent or receipt.get("selection_evidence") else None
    return final["normalized_product_url"], variant, {
        "receipt_path": item["receipt_path"], "receipt_sha256": item["receipt_sha256"],
        "html_path": receipt["html_path"], "html_sha256": receipt["html_sha256"],
        "observed_at": receipt["observed_at"], "origin": receipt["origin"],
        "product_page_evidence": evidence, "selection_evidence": receipt.get("selection_evidence")}


def validate_destination_review(review, candidates):
    """Return candidate-keyed projection only after every saved proof validates.

    ``candidates`` is a list of candidate dictionaries or an ID-keyed dictionary.
    Review schema: review_id, decision, reviewed_by, reviewed_at, candidate_ids,
    evidence [{candidate_id, receipt_path, receipt_sha256}]. No partial success.
    """
    decisions = {"VERIFIED_SAME_DESTINATION", "VERIFIED_INDEPENDENT_PURCHASABLE"}
    if (not isinstance(review, dict) or review.get("decision") not in decisions
            or not isinstance(review.get("review_id"), str) or not review["review_id"].strip()
            or not isinstance(review.get("reviewed_by"), str) or not review["reviewed_by"].strip()):
        raise ValueError("EXPLICIT_HUMAN_REVIEW_REQUIRED")
    reviewed_at = _instant(review.get("reviewed_at"))
    ids = review.get("candidate_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("UNIQUE_REVIEW_CANDIDATES_REQUIRED")
    independent = review["decision"] == "VERIFIED_INDEPENDENT_PURCHASABLE"
    if not independent and len(ids) < 2:
        raise ValueError("DESTINATION_REVIEW_REQUIRES_MULTIPLE_CANDIDATES")
    lookup = candidates if isinstance(candidates, dict) else {c["candidate_id"]: c for c in candidates}
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != len(ids):
        raise ValueError("EACH_CANDIDATE_RECEIPT_REQUIRED")
    evidence_ids = [e.get("candidate_id") for e in evidence if isinstance(e, dict)]
    if len(evidence_ids) != len(evidence) or set(evidence_ids) != set(ids) or len(set(evidence_ids)) != len(ids):
        raise ValueError("REVIEW_RECEIPT_BINDING_MISMATCH")
    verified = {}
    for item in evidence:
        cid = item["candidate_id"]
        candidate = lookup.get(cid)
        if not isinstance(candidate, dict) or not candidate.get("normalized_product_url"):
            raise ValueError("REVIEW_CANDIDATE_NOT_FOUND")
        if candidate_id(candidate["normalized_product_url"]) != cid:
            raise ValueError("REVIEW_CANDIDATE_KEY_MISMATCH")
        verified[cid] = _receipt(item, candidate, reviewed_at, independent)
    finals = [v[0] for v in verified.values()]
    if not independent and len(set(finals)) != 1:
        raise ValueError("DIFFERENT_DESTINATIONS_CANNOT_MERGE")
    if not independent and len({v[1] for v in verified.values()}) != 1:
        raise ValueError("DIFFERENT_SELECTED_VARIANTS_CANNOT_MERGE")
    if independent and (len(set(finals)) != len(finals) or len({v[1] for v in verified.values()}) != len(ids)):
        raise ValueError("DISTINCT_PURCHASABLE_VARIANT_PROOF_REQUIRED")
    return {cid: {"url_identity_status": review["decision"],
        "dedup_status": "VERIFIED_SAME_DESTINATION" if not independent else "UNIQUE_LINK_CANDIDATE",
        "final_product_url": value[0], "verified_destination_key": candidate_id(value[0]),
        "review_id": review["review_id"], "reviewed_by": review["reviewed_by"],
        "reviewed_at": review["reviewed_at"], "review_evidence": value[2]}
        for cid, value in verified.items()}
