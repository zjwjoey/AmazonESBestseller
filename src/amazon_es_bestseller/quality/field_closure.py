"""Quality-facing adapter for the existing read-only Field Closure audit."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..models import merge_ranking_and_detail
from ..qa.field_closure import audit_field_closure
from .models import QualityStatus, check_result, issue


_BLOCKING_CLASSIFICATIONS = {
    "PARSER_MISSED", "MAPPING_MISSED", "DERIVED_MISSING", "FIELD_DROPPED",
    "UNSUPPORTED_DERIVED_FACT", "FACT_CHANGED", "ORIGINAL_PRICE_INVALID",
}


def audit_field_closure_quality(
    products: Iterable[Mapping] | None, details: Iterable[Mapping], rankings: Iterable[Mapping], *,
    html_dirs=None, run_dir=None, translations=None, workbook_path=None,
    asins: set[str] | None = None,
) -> object:
    detail_rows = [dict(row) for row in (details or []) if isinstance(row, Mapping)]
    ranking_rows = [dict(row) for row in (rankings or []) if isinstance(row, Mapping)]
    product_rows = ([dict(row) for row in (products or []) if isinstance(row, Mapping)]
                    if products is not None else merge_ranking_and_detail(ranking_rows, detail_rows))
    if asins:
        product_rows = [row for row in product_rows if str(row.get("asin") or "").upper() in asins]
    report = audit_field_closure(
        product_rows, details=detail_rows, rankings=ranking_rows,
        html_dir=html_dirs, run_dir=run_dir, translations=translations,
        workbook_path=workbook_path)
    issues = []
    for row in report.get("records") or []:
        classification = str(row.get("classification") or "")
        if classification == "PASS":
            continue
        if classification in {"SOURCE_MISSING", "NOT_OBSERVED", "EVIDENCE_UNAVAILABLE"}:
            status, severity = QualityStatus.WARN, "P2"
        elif classification in _BLOCKING_CLASSIFICATIONS or str(row.get("severity")) == "P1":
            status, severity = QualityStatus.BLOCK, "P1"
        else:
            status, severity = QualityStatus.REVIEW, "P2"
        issues.append(issue(
            "field_closure", status, severity, "FIELD_CLOSURE_" + classification,
            asin=row.get("asin"), source="field_closure", source_file=row.get("source_file"),
            message=str(row.get("message") or classification),
            evidence={"field": row.get("field"), "classification": classification,
                      "source_evidence": row.get("source_evidence"),
                      "raw_evidence": row.get("raw_evidence"),
                      "canonical_value": row.get("canonical_value"),
                      "derived_value": row.get("derived_value")}))
    summary = dict(report.get("summary") or {})
    summary["quality_issue_count"] = len(issues)
    return check_result("field_closure", issues, summary=summary)
