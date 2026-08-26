# -*- coding: utf-8 -*-
"""models.py 测试：状态枚举、键集常量、榜单×详情合并。"""
from amazon_es_bestseller.models import (
    QAStatus,
    AccessState,
    QaIssue,
    RANKING_KEYS,
    PRODUCT_KEYS,
    DETAIL_RAW_KEYS,
    merge_ranking_and_detail,
    normalize_asin,
)


def test_qastatus_values():
    assert QAStatus.PASS == "PASS"
    assert QAStatus.WARN == "WARN"
    assert QAStatus.FAIL == "FAIL"
    assert QAStatus.SOURCE_CONFLICT == "SOURCE_CONFLICT"


def test_accessstate_values():
    assert AccessState.NORMAL == "NORMAL"
    assert AccessState.BLOCKED == "BLOCKED"
    assert AccessState.RATE_LIMITED == "RATE_LIMITED"
    assert AccessState.CHALLENGE == "CHALLENGE"
    assert AccessState.NETWORK_ERROR == "NETWORK_ERROR"
    assert AccessState.UNKNOWN == "UNKNOWN"


def test_qaissue_shape():
    issue = QaIssue("ASIN_INVALID", "P0", "asin", "msg")
    assert issue.code == "ASIN_INVALID"
    assert issue.severity == "P0"
    assert issue.field == "asin"


def test_ranking_keys_include_bestseller_rank():
    assert "asin" in RANKING_KEYS
    assert "bestseller_rank" in RANKING_KEYS
    assert "ranking_source_url" in RANKING_KEYS
    assert "collected_at" in RANKING_KEYS


def test_product_keys_include_identity():
    assert "asin" in PRODUCT_KEYS
    assert "title_es_raw" in PRODUCT_KEYS
    assert "brand" in PRODUCT_KEYS


def test_detail_raw_keys_separate_bsr():
    # 详情原始键必须用 detail_bsr_raw，而不是 bestseller_rank（QA_RULES §9）
    assert "detail_bsr_raw" in DETAIL_RAW_KEYS
    assert "bestseller_rank" not in DETAIL_RAW_KEYS


def test_normalize_asin():
    assert normalize_asin("b078c6qr1c") == "B078C6QR1C"
    assert normalize_asin(" B078C6QR1C ") == "B078C6QR1C"
    assert normalize_asin(None) == ""
    assert normalize_asin("") == ""


def test_merge_joins_by_asin_case_insensitive():
    ranking = [
        {"asin": "b078c6qr1c", "bestseller_rank": 3,
         "ranking_source_url": "https://www.amazon.es/gp/bestsellers/kitchen/",
         "collected_at": "2026-08-26 10:00:00", "leaf_category": "Juegos de recipientes"},
    ]
    details = [
        {"asin": "B078C6QR1C", "title_es_raw": "Fiambrera de cristal",
         "brand_raw": "Tatay", "current_price_raw": "12,62 €",
         "detail_bsr_raw": "n.º 233 en Hogar y cocina"},
    ]
    out = merge_ranking_and_detail(ranking, details)
    assert len(out) == 1
    p = out[0]
    assert p["asin"] == "B078C6QR1C"
    assert p["bestseller_rank"] == 3
    assert p["title_es_raw"] == "Fiambrera de cristal"
    assert p["detail_bsr_raw"] == "n.º 233 en Hogar y cocina"


def test_merge_bestseller_rank_never_overwritten_by_detail_bsr():
    # 详情 BSR（如 180285）绝不能覆盖榜单排名（QA_RULES §9/§72）
    ranking = [
        {"asin": "B078C6QR1C", "bestseller_rank": 3,
         "ranking_source_url": "u", "collected_at": "t", "leaf_category": "c"},
    ]
    details = [
        {"asin": "B078C6QR1C", "detail_bsr_raw": "180285",
         "title_es_raw": "x", "brand_raw": "y"},
    ]
    out = merge_ranking_and_detail(ranking, details)
    assert out[0]["bestseller_rank"] == 3
    assert out[0]["detail_bsr_raw"] == "180285"


def test_merge_keeps_first_ranking_context_only():
    # 商品表每 ASIN 一行：第二条榜单记录不覆盖第一条的榜单上下文
    ranking = [
        {"asin": "B078C6QR1C", "bestseller_rank": 3,
         "ranking_source_url": "url-a", "collected_at": "t1", "leaf_category": "A"},
        {"asin": "B078C6QR1C", "bestseller_rank": 8,
         "ranking_source_url": "url-b", "collected_at": "t2", "leaf_category": "B"},
    ]
    out = merge_ranking_and_detail(ranking, [])
    assert len(out) == 1
    assert out[0]["bestseller_rank"] == 3
    assert out[0]["ranking_source_url"] == "url-a"


def test_merge_detail_only_asin_included():
    details = [{"asin": "B0DDDDDDDD", "title_es_raw": "Solo detalle", "brand_raw": "z"}]
    out = merge_ranking_and_detail([], details)
    assert len(out) == 1
    assert out[0]["asin"] == "B0DDDDDDDD"
    assert out[0]["title_es_raw"] == "Solo detalle"


def test_merge_multiple_unique_asins():
    ranking = [
        {"asin": "B078C6QR1C", "bestseller_rank": 1, "ranking_source_url": "u1",
         "collected_at": "t", "leaf_category": "c1"},
        {"asin": "B075JJRFVV", "bestseller_rank": 2, "ranking_source_url": "u2",
         "collected_at": "t", "leaf_category": "c2"},
    ]
    out = merge_ranking_and_detail(ranking, [])
    assert len(out) == 2
    assert {p["asin"] for p in out} == {"B078C6QR1C", "B075JJRFVV"}
