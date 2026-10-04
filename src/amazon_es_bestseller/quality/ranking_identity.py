"""Ranking identity checks over immutable V1 records."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..identity import asin_from_url
from ..models import is_valid_asin, normalize_asin
from .models import QualityStatus, check_result, issue


def audit_ranking_identity(
    rankings: Iterable[Mapping], *, asins: set[str] | None = None,
) -> object:
    rows = [dict(row) for row in (rankings or []) if isinstance(row, Mapping)]
    issues = []
    checked = 0
    for row in rows:
        asin = normalize_asin(row.get("ranking_asin") or row.get("asin"))
        if asins and asin not in asins:
            continue
        checked += 1
        card_asin = normalize_asin(
            row.get("card_asin") or row.get("card_data_asin") or row.get("ranking_card_asin")
        )
        href_asin = normalize_asin(row.get("ranking_link_asin")) or asin_from_url(
            row.get("ranking_product_url_raw") or row.get("product_url_raw")
        )
        recorded_status = str(row.get("ranking_link_identity_status") or "").upper()
        if not is_valid_asin(asin):
            issues.append(issue(
                "ranking_identity", QualityStatus.BLOCK, "P1", "INVALID_ASIN",
                asin=asin, source="ranking", message="榜单记录没有有效十位 ASIN。",
                evidence={"record": row}))
            continue
        if recorded_status in {"LINK_ASIN_MISMATCH", "MISMATCH", "IDENTITY_CONFLICT"}:
            issues.append(issue(
                "ranking_identity", QualityStatus.BLOCK, "P1", "LINK_ASIN_MISMATCH",
                asin=asin, source="ranking", message="卡片 ASIN 与商品链接 ASIN 明确冲突。",
                evidence={"card_asin": card_asin, "href_asin": href_asin,
                          "recorded_status": recorded_status}))
            continue
        if card_asin and href_asin and card_asin != href_asin:
            issues.append(issue(
                "ranking_identity", QualityStatus.BLOCK, "P1", "LINK_ASIN_MISMATCH",
                asin=asin, source="ranking", message="卡片 ASIN 与商品 href ASIN 不一致。",
                evidence={"card_asin": card_asin, "href_asin": href_asin,
                          "raw_href": row.get("ranking_product_url_raw")}))
        elif href_asin and href_asin != asin:
            issues.append(issue(
                "ranking_identity", QualityStatus.BLOCK, "P1", "LINK_ASIN_MISMATCH",
                asin=asin, source="ranking", message="记录 ASIN 与商品链接 ASIN 不一致。",
                evidence={"record_asin": asin, "href_asin": href_asin,
                          "raw_href": row.get("ranking_product_url_raw")}))
        elif not href_asin:
            issues.append(issue(
                "ranking_identity", QualityStatus.WARN, "P2", "LIMITED_IDENTITY_EVIDENCE",
                asin=asin, source="ranking", message="榜单记录缺少可解析的商品链接 ASIN。",
                evidence={"card_asin": card_asin,
                          "raw_href": row.get("ranking_product_url_raw")}))
    return check_result(
        "ranking_identity", issues,
        summary={"records_checked": checked, "records_total": len(rows)},
    )
