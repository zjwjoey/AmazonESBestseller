"""Promote only individually evidence-verified candidates into detail planning."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, Mapping

from ..access.detector import detect_access_status
from ..collection.detail import _page_asin_evidence, parse_detail_page
from ..identity import IDENTITY_MATCH, resolve_identity
from ..models import is_valid_asin


def verify_candidate_selection(candidate_snapshot: Mapping, saved_detail_html: str | Path,
                               *, candidate_ids: Iterable[str] | None = None) -> dict:
    """Create a verified per-candidate selection without claiming snapshot completeness.

    Candidate snapshot completeness remains exactly as supplied. This adapter
    checks the card/link pair, then the saved detail page and identity resolver.
    """
    root = Path(saved_detail_html)
    allowed = {str(value) for value in candidate_ids or ()}
    verified, rejected = [], []
    for candidate in candidate_snapshot.get("candidates", []):
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        if allowed and candidate_id not in allowed:
            continue
        for observation in candidate.get("observations", []):
            if not isinstance(observation, Mapping):
                continue
            asin = str(observation.get("asin_observed") or "").upper()
            card_asin = str(observation.get("card_asin_observed") or "").upper()
            url_asin = str(observation.get("url_asin_observed") or "").upper()
            reason = ""
            detail_path = root / (asin + ".html")
            if not is_valid_asin(asin) or card_asin != asin or url_asin != asin:
                reason = "RANKING_CARD_LINK_IDENTITY_MISMATCH"
            elif str(observation.get("ranking_link_identity_status") or "").upper() != "MATCH":
                reason = "RANKING_LINK_NOT_CONFIRMED"
            elif not detail_path.is_file():
                reason = "SAVED_DETAIL_HTML_MISSING"
            else:
                source_file = Path(str(observation.get("evidence_path") or "")).name
                source_body = (candidate_snapshot.get("html_by_name") or {}).get(source_file)
                source_hash = hashlib.sha256(source_body).hexdigest() if isinstance(source_body, bytes) else ""
                if not source_hash or source_hash != str(observation.get("evidence_sha256") or "").lower():
                    reason = "RANKING_HTML_EVIDENCE_NOT_HASH_VERIFIED"
            source_file = Path(str(observation.get("evidence_path") or "")).name
            artifact_root = str(candidate_snapshot.get("artifact_root") or "")
            if artifact_root:
                source_path = Path(artifact_root) / "html" / source_file
            else:
                source_meta = next((item for item in candidate_snapshot.get("source_statuses", [])
                                    if Path(str(item.get("evidence_path") or "")).name == source_file), {})
                source_path = Path(str(source_meta.get("source_input_path") or ""))
            if reason:
                rejected.append({"candidate_id": candidate_id,
                                 "candidate_observation_id": observation.get("candidate_observation_id"),
                                 "asin_observed": asin, "reason": reason})
                continue

            body = detail_path.read_bytes()
            html = body.decode("utf-8", errors="replace")
            if detect_access_status(200, html).value != "NORMAL":
                rejected.append({"candidate_id": candidate_id,
                                 "candidate_observation_id": observation.get("candidate_observation_id"),
                                 "asin_observed": asin, "reason": "DETAIL_ACCESS_RESTRICTED"})
                continue
            parsed = parse_detail_page(html, asin)
            page_asins = _page_asin_evidence(html)
            identity = resolve_identity(
                ranking_asin=asin, requested_asin=asin,
                requested_url=str(observation.get("normalized_product_url") or ""),
                canonical_asin=parsed.get("canonical_asin") or "",
                embedded_asins=page_asins,
                parsed_detail_asin=asin if parsed.get("title_es_raw") and asin in page_asins else "",
            )
            if (identity.get("identity_status") != IDENTITY_MATCH or not parsed.get("title_es_raw")
                    or parsed.get("is_captcha")):
                rejected.append({"candidate_id": candidate_id,
                                 "candidate_observation_id": observation.get("candidate_observation_id"),
                                 "asin_observed": asin, "reason": "DETAIL_IDENTITY_NOT_CONFIRMED",
                                 "identity_status": identity.get("identity_status")})
                continue
            digest = hashlib.sha256(body).hexdigest()
            detail_record = {
                **parsed, "asin": asin, "ranking_asin": asin,
                "requested_asin": asin, "requested_url": observation.get("normalized_product_url") or "",
                "resolved_asin": identity.get("resolved_asin"), "identity_status": "MATCH",
                "identity_status_code": identity.get("identity_status"),
                "identity_evidence": identity.get("identity_evidence", []),
                "detail_status": "SUCCESS", "cache_classification": "VALID_PRODUCT_PAGE",
                "detail_source_html_sha256": digest,
                "detail_source_html_path": str(detail_path),
            }
            verified.append({
                "asin": asin, "ranking_asin": asin,
                "snapshot_id": candidate_snapshot.get("snapshot_id") or "",
                "candidate_id": candidate_id,
                "candidate_observation_id": observation.get("candidate_observation_id") or "",
                "candidate_evidence_path": observation.get("evidence_path") or "",
                "candidate_evidence_file_path": str(source_path),
                "candidate_evidence_sha256": observation.get("evidence_sha256") or "",
                "candidate_identity_status": "IDENTITY_MATCH",
                "ranking_link_identity_status": "MATCH",
                "ranking_product_url_raw": observation.get("raw_product_url") or "",
                "ranking_product_url_normalized": observation.get("normalized_product_url") or "",
                "ranking_source_url": observation.get("source_url") or "",
                "ranking_page_number": observation.get("page_number"),
                "bestseller_rank": observation.get("bestseller_rank"),
                "identity_status": "IDENTITY_MATCH",
                "identity_evidence": identity.get("identity_evidence", []),
                "detail_record": detail_record,
                "detail_evidence_path": str(detail_path),
                "detail_evidence_sha256": digest,
            })
    return {
        "snapshot_kind": "CANDIDATE_IDENTITY_SELECTION",
        "snapshot_status": "VERIFIED_SELECTION" if verified else "NO_VERIFIED_CANDIDATES",
        "snapshot_id": candidate_snapshot.get("snapshot_id") or "",
        "source_snapshot_id": candidate_snapshot.get("snapshot_id") or "",
        "source_snapshot_status": candidate_snapshot.get("snapshot_status") or "UNKNOWN",
        "identity_complete": False,
        "authoritative": False,
        "verified_candidate_count": len(verified),
        "records": verified,
        "rejected": rejected,
    }
