# -*- coding: utf-8 -*-
"""Shared product identity resolution for ranking and detail evidence.

Identity decisions are deliberately evidence based.  A URL mismatch is only
one signal; it is not, by itself, proof that a product page is unrelated.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

from .models import is_valid_asin, normalize_asin

IDENTITY_MATCH = "IDENTITY_MATCH"
# V2 names keep the existing persisted value stable while exposing the
# vocabulary used by the reviewed contract.
EXACT_ASIN = IDENTITY_MATCH
PARENT_ASIN_MATCH = "PARENT_ASIN_MATCH"
VARIATION_RELATED = "VARIATION_RELATED"
MATCH_BY_EXTERNAL_EVIDENCE = "MATCH_BY_EXTERNAL_EVIDENCE"
EXTERNAL_EVIDENCE_MATCH = MATCH_BY_EXTERNAL_EVIDENCE
IDENTITY_UNCONFIRMED = "IDENTITY_UNCONFIRMED"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"

_V2_STATUS_CODES = {
    IDENTITY_MATCH: "EXACT_ASIN",
    MATCH_BY_EXTERNAL_EVIDENCE: "EXTERNAL_EVIDENCE_MATCH",
}

_URL_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?#]|$)", re.I)


def asin_from_url(value: object) -> str:
    match = _URL_ASIN_RE.search(str(value or ""))
    return normalize_asin(match.group(1)) if match else ""


def _asins(values: Iterable[object] | object | None) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, (str, bytes)):
        values = [values]
    return {normalize_asin(value) for value in values if is_valid_asin(normalize_asin(value))}


def resolve_identity(*, ranking_asin: object = "", requested_asin: object = "",
                     requested_url: object = "", final_url_asin: object = "",
                     canonical_asin: object = "", embedded_asins=None,
                     parsed_detail_asin: object = "", parent_asin: object = "",
                     variation_family_asins=None) -> dict:
    """Resolve an identity and return status, resolved ASIN and evidence.

    The returned ``evidence`` list is audit material, not merely diagnostic
    text.  Only explicit family membership can authorize parent/variation
    relationships.
    """
    ranking = normalize_asin(ranking_asin)
    requested = normalize_asin(requested_asin)
    final = normalize_asin(final_url_asin) or asin_from_url(final_url_asin)
    canonical = normalize_asin(canonical_asin) or asin_from_url(canonical_asin)
    requested_from_url = asin_from_url(requested_url)
    parsed = normalize_asin(parsed_detail_asin)
    parent = normalize_asin(parent_asin)
    embedded = _asins(embedded_asins)
    family = _asins(variation_family_asins)
    evidence = []

    for label, value in (("requested_asin", requested),
                         ("requested_url_asin", requested_from_url),
                         ("final_url_asin", final),
                         ("canonical_asin", canonical),
                         ("parsed_detail_asin", parsed)):
        if value:
            evidence.append({"source": label, "asin": value})
    for value in sorted(embedded):
        evidence.append({"source": "embedded_asin", "asin": value})
    if parent:
        evidence.append({"source": "parent_asin", "asin": parent})
    for value in sorted(family):
        evidence.append({"source": "variation_family_asin", "asin": value})

    resolved = parsed or canonical or final or requested_from_url or (next(iter(embedded)) if len(embedded) == 1 else "")
    if ranking and resolved == ranking:
        status = IDENTITY_MATCH
    elif ranking and resolved and parent == resolved and ranking in family and resolved in family:
        status = PARENT_ASIN_MATCH
    elif ranking and resolved and ranking in family and resolved in family:
        status = VARIATION_RELATED
    elif ranking and not resolved and requested == ranking and requested_from_url in {"", ranking}:
        status = MATCH_BY_EXTERNAL_EVIDENCE
        resolved = ranking
    elif ranking and resolved:
        status = IDENTITY_MISMATCH
    else:
        status = IDENTITY_UNCONFIRMED
    return {
        "ranking_asin": ranking,
        "requested_asin": requested or ranking,
        "resolved_asin": resolved,
        "parent_asin": parent,
        "variation_family_asins": sorted(family),
        "identity_status": status,
        "identity_status_code": _V2_STATUS_CODES.get(status, status),
        "identity_evidence": evidence,
    }
