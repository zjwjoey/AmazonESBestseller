# -*- coding: utf-8 -*-
"""QA 聚合与统计（docs/QA_RULES.md §28 状态 / §29 缺失数据）。

D4：单条记录的最终状态按优先级 SOURCE_CONFLICT > FAIL > WARN > PASS 取最高。
"""
from __future__ import annotations

from collections import Counter
from typing import List, Mapping

from ..models import QAStatus, QaIssue
from . import validators as v

#: 全量校验管线：每个元素是 (validator, 需要从记录取字段的方式)
_VALIDATORS = (
    lambda r: v.validate_asin(r.get('asin')),
    lambda r: v.validate_url_asin(r.get('asin'), r.get('product_url')),
    lambda r: v.validate_image_asin(r.get('asin'), r.get('image_asin')),
    lambda r: v.validate_price(
        r.get('current_price'), r.get('original_price'),
        r.get('currency'), r.get('discount_rate')),
    lambda r: v.validate_rating(r.get('rating_raw') or r.get('rating')),
    lambda r: v.validate_review_count(r.get('review_count_raw') or r.get('review_count')),
    lambda r: v.validate_brand(r.get('brand'), r.get('brand_raw')),
    lambda r: v.validate_spec(r),
    lambda r: v.validate_rank_separation(r),
    lambda r: v.validate_category(r),
    lambda r: v.validate_bilingual_match(r),
    lambda r: v.validate_source_conflict(r),
    lambda r: v.validate_monthly_bought(r),
)

#: D4 聚合优先级（QA_RULES §28 状态）
_D4_RANK = {
    QAStatus.SOURCE_CONFLICT: 4,
    QAStatus.FAIL: 3,
    QAStatus.WARN: 2,
    QAStatus.PASS: 1,
}

#: 填充率统计字段（QA_RULES §29 缺失数据）
FILL_FIELDS = (
    'asin', 'current_price', 'original_price', 'brand', 'image_url',
    'monthly_bought_min', 'spec_v2', 'date_first_available',
    'leaf_category', 'browse_node_id', 'bestseller_rank',
)


def run_qa(record: Mapping) -> dict:
    """对单条记录跑全量校验，返回 {qa_status, qa_issues, counts}。"""
    issues: List[QaIssue] = []
    worst = QAStatus.PASS
    for fn in _VALIDATORS:
        st, iss = fn(record)
        issues.extend(iss)
        if _D4_RANK[st] > _D4_RANK[worst]:
            worst = st
    counts = Counter(i.severity for i in issues)
    return {
        'qa_status': worst.value,
        'qa_issues': issues,
        'counts': {k: counts[k] for k in ('P0', 'P1', 'P2', 'P3')},
    }


def qa_summary(records: List[Mapping]) -> dict:
    """整批记录 QA 汇总（QA_RULES §28）+ 关键字段填充率（§29）。"""
    status_counts = Counter()
    for r in records:
        status_counts[run_qa(r)['qa_status']] += 1
    fill = {
        f: sum(1 for r in records if r.get(f) not in (None, ''))
        for f in FILL_FIELDS
    }
    return {
        'total_products': len(records),
        'pass_count': status_counts.get(QAStatus.PASS.value, 0),
        'warn_count': status_counts.get(QAStatus.WARN.value, 0),
        'fail_count': status_counts.get(QAStatus.FAIL.value, 0),
        'source_conflict_count': status_counts.get(QAStatus.SOURCE_CONFLICT.value, 0),
        'field_completeness': fill,
    }
