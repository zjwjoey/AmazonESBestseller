"""Shared offline Spanish views for link-first collection and saved-evidence repair."""

from copy import deepcopy

from .category import category_levels
from .price import canonical_price_fields
from .specification import build_spec_es
from .measure_evidence import preferred_capacity
from ..translation.full_detail import (
    detail_bullets_to_attributes,
    render_bullets_es,
    render_details_es,
)


def spanish_candidate_fields(row, *, ranking_context=None):
    """Return derived fields only; never mutate identity, raw arrays or human notes."""
    attributes = deepcopy(row.get("attributes") or [])
    pairs = {(a["label_raw"], a["value_raw"]) for a in attributes}
    for attr in detail_bullets_to_attributes(row.get("detail_bullets_raw") or []):
        pair = (attr["label_raw"], attr["value_raw"])
        if pair not in pairs:
            attributes.append(attr)
            pairs.add(pair)
    spec = build_spec_es(
        attributes=attributes,
        variant=row.get("selected_variation_raw"),
        title_es=row.get("title_es_raw"),
    )
    out = canonical_price_fields(
        row.get("current_price_raw"), row.get("original_price_raw")
    )
    out.update(
        specification=spec,
        specification_es=spec,
        product_details_es=render_details_es(attributes).replace("\n", "; "),
        feature_bullets_es=render_bullets_es(row.get("feature_bullets_raw")),
        specification_evidence={
            unit: {**evidence, "amount": str(evidence["amount"])}
            for unit, evidence in preferred_capacity(
                row.get("selected_variation_raw"), row.get("title_es_raw")
            ).items()
        },
    )
    from ..quality.source_fields import _sf_hash

    trail = row.get("detail_category_trail") or []
    if trail and row.get("html_sha256") and row.get("html_file"):
        binding = {
            k: deepcopy(row.get(k))
            for k in ("asin", "html_file", "html_sha256", "detail_category_trail")
        }
        out["category_provenance"] = {
            "source": "product_breadcrumb",
            "selector": "#wayfinding-breadcrumbs_feature_div",
            "evidence_binding": binding,
            "evidence_hash": _sf_hash(binding),
            "levels": dict(
                zip(
                    ("category_l1", "category_l2", "category_l3", "leaf_category"),
                    category_levels(trail),
                )
            ),
        }
    elif ranking_context:
        levels = dict(
            zip(
                ("category_l1", "category_l2", "category_l3", "leaf_category"),
                category_levels(ranking_context.get("category_trail") or []),
            )
        )
        out["category_provenance"] = {
            "source": "ranking_context",
            "ranking_context_hash": _sf_hash(ranking_context),
            "ranking_source_url": ranking_context.get("ranking_source_url"),
            "ranking_page_number": ranking_context.get("ranking_page_number"),
            "ranking_source_category_path": " > ".join(
                ranking_context.get("category_trail") or []
            ),
            "levels": levels,
            "leaf_category": levels["leaf_category"],
        }
    return out
