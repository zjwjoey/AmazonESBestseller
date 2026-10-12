"""Evidence-preserving offline repairs for link-first Spanish research views.

Raw source fields, identities and ranking contexts are never edited. Repairs
produce a separate candidate, never an automatic Spanish Master promotion.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

from bs4 import BeautifulSoup, SoupStrainer

from ..collection.detail import _detail_category_trail
from ..normalization.category import category_levels
from ..normalization.spanish_candidate import spanish_candidate_fields
from ..quality.source_fields import _sf_hash
from ..translation.full_detail import plain_bullet as plain_bullet

LEVELS = ("category_l1", "category_l2", "category_l3", "leaf_category")


def repair_record(raw: dict, html_bytes: bytes) -> tuple[dict, list[dict]]:
    if sha256(html_bytes).hexdigest() != raw.get("html_sha256"):
        raise ValueError("DETAIL_HTML_HASH_MISMATCH: " + raw["asin"])
    row, changes = deepcopy(raw), []

    def assign(field, value, reason):
        if field not in row or row.get(field) != value:
            changes.append({"asin": row["asin"], "field": field, "before": deepcopy(row.get(field)),
                            "after": deepcopy(value), "reason": reason,
                            "html_file": raw["html_file"], "html_sha256": raw["html_sha256"]})
            row[field] = value

    # Parse just the archived breadcrumb container, not unrelated page widgets.
    soup = BeautifulSoup(html_bytes.decode("utf-8"), "lxml",
                         parse_only=SoupStrainer(id="wayfinding-breadcrumbs_feature_div"))
    trail = _detail_category_trail(soup)
    if trail != (raw.get("detail_category_trail") or []):
        raise ValueError("ARCHIVED_BREADCRUMB_BINDING_MISMATCH: " + raw["asin"])
    if trail:
        values = category_levels(trail)
        for key, value in zip(LEVELS, values):
            assign(key, value, "category reconstructed from exact saved product breadcrumb")
        binding = {key: raw.get(key) for key in ("asin", "html_file", "html_sha256", "detail_category_trail")}
        provenance = {"source": "product_breadcrumb", "selector": "#wayfinding-breadcrumbs_feature_div",
                      "evidence_binding": binding, "evidence_hash": _sf_hash(binding),
                      "levels": dict(zip(LEVELS, values))}
    else:
        contexts = raw.get("ranking_contexts") or []
        context = contexts[0] if contexts else None
        if context is None:
            raise ValueError("CATEGORY_EVIDENCE_MISSING: " + raw["asin"])
        values = category_levels(context.get("category_trail") or [])
        if tuple(row.get(key) for key in LEVELS) != values:
            raise ValueError("RANKING_CATEGORY_BINDING_MISMATCH: " + raw["asin"])
        provenance = {"source": "ranking_context", "ranking_context_hash": _sf_hash(context),
                      "ranking_source_url": context.get("ranking_source_url"),
                      "ranking_page_number": context.get("ranking_page_number"),
                      "ranking_source_category_path": " > ".join(context.get("category_trail") or []),
                      "levels": dict(zip(LEVELS, values)), "leaf_category": values[-1]}
    assign("category_provenance", provenance, "attach source-bound category provenance, without inventing a deeper level")

    # Exactly the same derived contract is used by new link-first collections.
    for field,value in spanish_candidate_fields(raw).items():
        if field!='category_provenance':
            assign(field,value,'shared Spanish candidate contract; source evidence remains unchanged')
    # Raw data are invariant, including duplicate-key ordering and human notes.
    protected = [key for key in raw if key.endswith("_raw")]
    protected += ["asin", "parent_asin", "attributes", "ranking_contexts", "detail_category_trail",
                  "product_url", "requested_url", "final_url", "html_file", "html_sha256", "notes", "备注"]
    if any(raw.get(key) != row.get(key) for key in protected):
        raise AssertionError("RAW_EVIDENCE_CHANGED")
    return row, changes
