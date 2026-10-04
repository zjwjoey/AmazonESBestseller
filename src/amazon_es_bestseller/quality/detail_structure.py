"""Lossless ordered-detail evidence checks."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from ..models import normalize_asin
from .models import QualityStatus, check_result, issue


def audit_detail_structure(
    details: Iterable[Mapping], *, asins: set[str] | None = None,
) -> object:
    rows = [dict(row) for row in (details or []) if isinstance(row, Mapping)]
    issues = []
    checked = 0
    for row in rows:
        asin = normalize_asin(row.get("asin") or row.get("requested_asin"))
        if asins and asin not in asins:
            continue
        checked += 1
        attrs = row.get("attributes")
        ordered = row.get("ordered_detail_evidence")
        if attrs is None or (attrs == [] and isinstance(ordered, list) and ordered):
            attrs = ordered
        if attrs in (None, ""):
            continue
        if not isinstance(attrs, list):
            issues.append(issue(
                "detail_structure", QualityStatus.BLOCK, "P1", "DETAIL_ATTRIBUTES_SCHEMA",
                asin=asin, source="detail", message="详情属性不是有序数组，可能发生 label/value 丢失。"))
            continue
        positions = []
        fingerprints = Counter()
        for index, attr in enumerate(attrs):
            if not isinstance(attr, Mapping):
                issues.append(issue(
                    "detail_structure", QualityStatus.BLOCK, "P1", "DETAIL_ATTRIBUTE_NOT_OBJECT",
                    asin=asin, source="detail", message="详情属性项不是对象。",
                    evidence={"index": index, "value": attr}))
                continue
            label = str(attr.get("label_raw") or "").strip()
            value = str(attr.get("value_raw") or "").strip()
            section = str(attr.get("section") or "").strip()
            source = str(attr.get("source") or "").strip()
            if not label or not value:
                issues.append(issue(
                    "detail_structure", QualityStatus.REVIEW, "P2", "DETAIL_LABEL_VALUE_MISSING",
                    asin=asin, source="detail", message="详情属性的 label 或 value 为空。",
                    evidence={"index": index, "attribute": dict(attr)}))
            if not section or not source:
                issues.append(issue(
                    "detail_structure", QualityStatus.WARN, "P2", "DETAIL_PROVENANCE_INCOMPLETE",
                    asin=asin, source="detail", message="详情属性缺少 section/source 来源标记。",
                    evidence={"index": index, "attribute": dict(attr)}))
            position = attr.get("position")
            try:
                position_value = int(position)
            except (TypeError, ValueError):
                position_value = None
            if position_value is None:
                issues.append(issue(
                    "detail_structure", QualityStatus.REVIEW, "P2", "DETAIL_POSITION_MISSING",
                    asin=asin, source="detail", message="详情属性缺少可排序的 position。",
                    evidence={"index": index, "attribute": dict(attr)}))
            else:
                positions.append(position_value)
            fingerprints[(section.casefold(), label.casefold(), value)] += 1
        if len(positions) != len(set(positions)):
            issues.append(issue(
                "detail_structure", QualityStatus.BLOCK, "P1", "DETAIL_POSITION_DUPLICATE",
                asin=asin, source="detail", message="详情属性出现重复 position，顺序证据不可靠。",
                evidence={"positions": positions}))
        if positions and positions != sorted(positions):
            issues.append(issue(
                "detail_structure", QualityStatus.REVIEW, "P2", "DETAIL_POSITION_ORDER",
                asin=asin, source="detail", message="详情属性 position 与原始顺序不一致。",
                evidence={"positions": positions}))
        duplicates = [list(key) + [count] for key, count in fingerprints.items() if count > 1]
        if duplicates:
            issues.append(issue(
                "detail_structure", QualityStatus.REVIEW, "P2", "DETAIL_ITEM_DUPLICATED",
                asin=asin, source="detail", message="同一详情属性项被重复保存；同名 label 本身不视为错误。",
                evidence={"duplicates": duplicates}))
        bullets = row.get("feature_bullets_raw")
        if bullets is not None and not isinstance(bullets, list):
            issues.append(issue(
                "detail_structure", QualityStatus.WARN, "P2", "FEATURE_BULLETS_NOT_LIST",
                asin=asin, source="detail", message="卖点原始证据不是数组，需人工确认结构。"))
    if not rows:
        issues.append(issue(
            "detail_structure", QualityStatus.REVIEW, "P2", "DETAIL_EVIDENCE_MISSING",
            source="detail", message="没有可审查的详情结构记录。"))
    return check_result(
        "detail_structure", issues,
        summary={"records_checked": checked, "records_total": len(rows)},
    )
