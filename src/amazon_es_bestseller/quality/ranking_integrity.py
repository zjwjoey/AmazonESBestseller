"""Conservative ranking slot and page integrity checks."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from ..models import is_valid_asin, normalize_asin
from .models import QualityStatus, check_result, issue


def _page_key(row: Mapping) -> tuple:
    return (
        str(row.get("page_instance_id") or row.get("ranking_page_instance_id") or ""),
        str(row.get("ranking_source_url") or row.get("source_url") or ""),
        row.get("ranking_page_number", row.get("page_number")),
    )


def audit_ranking_integrity(
    rankings: Iterable[Mapping], *, source_statuses: Iterable[Mapping] | None = None,
    asins: set[str] | None = None,
) -> object:
    rows = [dict(row) for row in (rankings or []) if isinstance(row, Mapping)]
    if asins:
        rows = [row for row in rows if normalize_asin(row.get("asin") or row.get("ranking_asin")) in asins]
    issues = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[_page_key(row)].append(row)
        asin = normalize_asin(row.get("asin") or row.get("ranking_asin"))
        if not is_valid_asin(asin):
            issues.append(issue(
                "ranking_integrity", QualityStatus.BLOCK, "P1", "INVALID_ASIN",
                asin=asin, source="ranking", message="排名记录包含无效 ASIN。",
                evidence={"record": row}))
    duplicate_slot_count = 0
    gap_count = 0
    missing_rank_count = 0
    for page, page_rows in groups.items():
        ranks: list[int] = []
        slots: dict[int, set[str]] = defaultdict(set)
        for row in page_rows:
            value = row.get("bestseller_rank", row.get("ranking_rank"))
            try:
                rank = int(value)
            except (TypeError, ValueError):
                rank = 0
            asin = normalize_asin(row.get("asin") or row.get("ranking_asin"))
            if rank <= 0:
                missing_rank_count += 1
                continue
            ranks.append(rank)
            slots[rank].add(asin)
        for rank, values in sorted(slots.items()):
            if len(values) > 1:
                duplicate_slot_count += 1
                issues.append(issue(
                    "ranking_integrity", QualityStatus.BLOCK, "P1", "DUPLICATE_RANK_SLOT",
                    source="ranking", message="同一 page instance 的同一排名槽位对应多个 ASIN。",
                    evidence={"page": page, "rank": rank, "asins": sorted(values)}))
        if len(ranks) >= 3:
            missing = sorted(set(range(min(ranks), max(ranks) + 1)) - set(ranks))
            if missing:
                gap_count += len(missing)
                issues.append(issue(
                    "ranking_integrity", QualityStatus.REVIEW, "P2", "RANK_GAP",
                    source="ranking", message="发现排名断档，需要确认 Amazon 页面是否省略了槽位。",
                    evidence={"page": page, "min_rank": min(ranks),
                              "max_rank": max(ranks), "missing_ranks": missing}))
    for row in source_statuses or ():
        if not isinstance(row, Mapping):
            continue
        state = str(row.get("access_state") or row.get("status") or "").upper()
        parse_status = str(row.get("parse_status") or "").upper()
        source_file = row.get("html_file") or row.get("evidence_file") or ""
        if parse_status in {"ACCESS_BLOCKED", "NETWORK_ERROR", "PARSER_ERROR", "PARSE_EMPTY"}:
            issues.append(issue(
                "ranking_integrity", QualityStatus.BLOCK, "P1",
                "RANKING_PAGE_NOT_USABLE", source="ranking", source_file=source_file,
                message="榜单页面状态不是可用于研究的完整页面。",
                evidence={"status": state, "parse_status": parse_status,
                          "source_url": row.get("source_url")}))
        elif parse_status in {"UNKNOWN", "NOT_STARTED", ""} and not row.get("parsed_record_count"):
            issues.append(issue(
                "ranking_integrity", QualityStatus.REVIEW, "P2",
                "RANKING_PAGE_STATUS_UNKNOWN", source="ranking", source_file=source_file,
                message="榜单页面缺少明确完成状态。",
                evidence={"status": state, "parse_status": parse_status,
                          "source_url": row.get("source_url")}))
    if missing_rank_count:
        issues.append(issue(
            "ranking_integrity", QualityStatus.WARN, "P2", "RANK_MISSING",
            source="ranking", message="部分记录没有明确 Amazon 排名字段；未将其直接判为阻断。",
            evidence={"count": missing_rank_count}))
    return check_result(
        "ranking_integrity", issues,
        summary={"page_count": len(groups), "records_checked": len(rows),
                 "duplicate_rank_slot_count": duplicate_slot_count,
                 "rank_gap_count": gap_count, "rank_missing_count": missing_rank_count},
    )
