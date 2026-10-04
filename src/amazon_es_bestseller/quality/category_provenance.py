"""Category evidence comparison without choosing or overwriting a category."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..models import normalize_asin
from .models import QualityStatus, check_result, issue


def _path(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(">") if ">" in value else [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = []
    return tuple(" ".join(str(item).split()).casefold() for item in values if str(item).strip())


def _ranking_path(row: Mapping) -> tuple[str, ...]:
    return _path(row.get("ranking_category_path") or row.get("ranking_source_category_path")
                 or row.get("category_path_raw") or row.get("category_path"))


def _detail_path(row: Mapping) -> tuple[str, ...]:
    evidence = row.get("category_evidence")
    if isinstance(evidence, Mapping):
        return _path(evidence.get("product_breadcrumb_category_path")
                     or evidence.get("detail_category_trail"))
    return _path(row.get("detail_category_trail") or row.get("category_trail")
                 or row.get("detail_category_path"))


def audit_category_provenance(
    rankings: Iterable[Mapping], details: Iterable[Mapping], *, asins: set[str] | None = None,
) -> object:
    ranking_rows = [row for row in (rankings or ()) if isinstance(row, Mapping)]
    detail_rows = [row for row in (details or ()) if isinstance(row, Mapping)]
    ranking_by = {}
    for row in ranking_rows:
        asin = normalize_asin(row.get("asin") or row.get("ranking_asin"))
        if asin:
            ranking_by.setdefault(asin, row)
    issues = []
    compared = 0
    for detail in detail_rows:
        asin = normalize_asin(detail.get("asin") or detail.get("requested_asin"))
        if asins and asin not in asins:
            continue
        ranking_path = _ranking_path(ranking_by.get(asin, {}))
        detail_path = _detail_path(detail)
        if ranking_path and detail_path:
            compared += 1
            if ranking_path != detail_path:
                issues.append(issue(
                    "category_provenance", QualityStatus.REVIEW, "P2",
                    "CATEGORY_EVIDENCE_CONFLICT", asin=asin, source="category",
                    message="榜单类目证据与详情面包屑证据不一致，不能自动覆盖。",
                    evidence={"ranking_path": list(ranking_path), "detail_path": list(detail_path)}))
        elif ranking_path or detail_path:
            compared += 1
    if not ranking_by and not detail_rows:
        issues.append(issue(
            "category_provenance", QualityStatus.REVIEW, "P2", "CATEGORY_EVIDENCE_MISSING",
            source="category", message="没有可审查的类目来源证据。"))
    return check_result(
        "category_provenance", issues,
        summary={"compared_count": compared, "ranking_records": len(ranking_by)},
    )
