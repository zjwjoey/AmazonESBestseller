"""Detail identity gate using the shared identity resolver."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..identity import IDENTITY_MATCH, MATCH_BY_EXTERNAL_EVIDENCE, PARENT_ASIN_MATCH, VARIATION_RELATED, resolve_identity
from ..models import normalize_asin
from .models import QualityStatus, check_result, issue


_ACCEPTED = {IDENTITY_MATCH, PARENT_ASIN_MATCH, VARIATION_RELATED, MATCH_BY_EXTERNAL_EVIDENCE, "MATCH"}


def _family(row: Mapping) -> list[str]:
    value = row.get("variation_family_asins") or row.get("family_asins") or []
    if isinstance(value, str):
        value = [value]
    return [normalize_asin(item) for item in value if normalize_asin(item)]


def audit_detail_identity(
    details: Iterable[Mapping], rankings: Iterable[Mapping] = (), *,
    asins: set[str] | None = None,
) -> object:
    detail_rows = [dict(row) for row in (details or []) if isinstance(row, Mapping)]
    ranking_by_asin = {}
    for row in rankings or ():
        if isinstance(row, Mapping):
            key = normalize_asin(row.get("asin") or row.get("ranking_asin"))
            if key:
                ranking_by_asin.setdefault(key, row)
    issues = []
    checked = 0
    for row in detail_rows:
        requested = normalize_asin(row.get("requested_asin") or row.get("asin"))
        ranking = normalize_asin(row.get("ranking_asin") or requested)
        if asins and ranking not in asins and requested not in asins:
            continue
        checked += 1
        ranking_row = ranking_by_asin.get(ranking, {})
        final_url = row.get("final_url") or row.get("final_url_asin") or ""
        canonical = row.get("canonical_asin") or ""
        parsed = row.get("parsed_detail_asin") or row.get("page_asin") or row.get("resolved_asin") or row.get("asin")
        result = resolve_identity(
            ranking_asin=ranking,
            requested_asin=requested,
            requested_url=row.get("requested_url") or row.get("requested_url_raw")
            or ranking_row.get("ranking_product_url_raw") or "",
            final_url_asin=final_url,
            canonical_asin=canonical,
            embedded_asins=row.get("embedded_asins") or row.get("page_asin_candidates") or [],
            parsed_detail_asin=parsed,
            parent_asin=row.get("parent_asin") or row.get("parent_asin_raw") or "",
            variation_family_asins=_family(row),
        )
        status = str(row.get("identity_status") or result.get("identity_status") or "").upper()
        detail_status = str(row.get("detail_status") or "").upper()
        identity_event = str(row.get("identity_event") or "").upper()
        if (detail_status == "REQUEST_URL_BINDING_MISMATCH" or
                identity_event == "REQUEST_URL_BINDING_MISMATCH"):
            issues.append(issue(
                "detail_identity", QualityStatus.BLOCK, "P0", "REQUEST_URL_BINDING_MISMATCH",
                asin=ranking or requested, source="detail",
                message="详情请求 URL 与冻结候选计划绑定不一致；页面未被可信请求。",
                evidence={"record": row, "resolver": result}))
            continue
        if (detail_status == "SUCCESS_WITH_IDENTITY_CHANGE" or
                identity_event == "NAVIGATION_IDENTITY_CHANGED"):
            issues.append(issue(
                "detail_identity", QualityStatus.REVIEW, "P1", "NAVIGATION_IDENTITY_CHANGED",
                asin=ranking or requested, source="detail",
                message="冻结榜单链接落地到不同或未确认的商品身份；已保留详情证据，需人工复核。",
                evidence={"record": row, "resolver": result}))
            continue
        if status in _ACCEPTED:
            # A contradictory final URL is not masked by a matching parsed
            # field unless explicit variation-family evidence explains it.
            final_asin = str(result.get("identity_evidence") and next(
                (item.get("asin") for item in result["identity_evidence"]
                 if item.get("source") == "final_url_asin"), "") or "")
            family = set(_family(row))
            if final_asin and final_asin != ranking and final_asin not in family:
                issues.append(issue(
                    "detail_identity", QualityStatus.BLOCK, "P1", "IDENTITY_MISMATCH",
                    asin=ranking or requested, source="detail",
                    message="详情最终 URL 指向另一 ASIN，且没有变体族证据解释。",
                    evidence={"requested_asin": requested, "ranking_asin": ranking,
                              "final_url_asin": final_asin, "resolver": result}))
        elif status in {"IDENTITY_UNCONFIRMED", "UNCONFIRMED", ""}:
            issues.append(issue(
                "detail_identity", QualityStatus.REVIEW, "P1", "IDENTITY_UNCONFIRMED",
                asin=ranking or requested, source="detail",
                message="详情页身份证据不足，需要人工确认。",
                evidence={"resolver": result, "record": row}))
        else:
            issues.append(issue(
                "detail_identity", QualityStatus.BLOCK, "P1", "IDENTITY_MISMATCH",
                asin=ranking or requested, source="detail",
                message="详情页明确不是榜单请求的商品。",
                evidence={"resolver": result, "record_status": status}))
    if not detail_rows:
        issues.append(issue(
            "detail_identity", QualityStatus.REVIEW, "P2", "DETAIL_EVIDENCE_MISSING",
            source="detail", message="没有可审查的详情记录。"))
    return check_result(
        "detail_identity", issues,
        summary={"records_checked": checked, "records_total": len(detail_rows)},
    )
