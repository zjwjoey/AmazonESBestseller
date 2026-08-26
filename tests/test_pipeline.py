# -*- coding: utf-8 -*-
"""pipeline.py 测试：离线主链 enrich + 遗留扁平数据适配。

QA_RULES §9（榜单 BSR 绝不混用）：遗留 product_details.json 的 BSR 列是
build_output.py 按 Rank 构造的历史伪造产物（A4 钉死）→ 导入时丢弃。
"""
from pathlib import Path

import pytest

from amazon_es_bestseller.pipeline import (
    enrich_products,
    legacy_flat_to_detail,
    legacy_flat_to_ranking,
    normalize_product,
)
from amazon_es_bestseller.qa.run import run_qa

REPO = Path(__file__).resolve().parent.parent

RANKING = [{
    "index": 0, "asin": "B078C6QR1C",
    "category_l1": "Hogar y cocina", "category_l2": "Almacenamiento y organización",
    "category_l3": "Juegos de recipientes", "leaf_category": "Juegos de recipientes",
    "browse_node_id": "689078031", "bestseller_rank": 1,
    "ranking_source_url": "https://www.amazon.es/zgbs/689078031", "collected_at": "2026-08-26T00:00:00Z",
}]

DETAIL = [{
    "asin": "B078C6QR1C",
    "title_es_raw": "Fiambrera de cristal con 4 piezas",
    "current_price_raw": "12,62 €", "original_price_raw": "13,29 €",
    "rating_raw": "4,5 de 5 estrellas (3873)", "review_count_raw": "3.873",
    "brand_raw": "Marca: Tatay", "seller_raw": "Tatay",
    "availability_raw": "En stock", "selected_variation_raw": "4 piezas",
    "detail_bsr_raw": "n.º 52 en Hogar y cocina",
    "details_json": {"capacidad": "1 litros", "numero_de_sets": "4", "tipo_de_material": "Vidrio templado"},
    "product_url": "https://www.amazon.es/dp/B078C6QR1C", "image_url": "https://img",
}]


def test_enrich_normalizes_fields():
    products = enrich_products(RANKING, DETAIL)
    assert len(products) == 1
    p = products[0]
    assert p["current_price"] == 12.62
    assert p["original_price"] == 13.29
    assert p["discount_rate"] == round((13.29 - 12.62) / 13.29, 4)
    assert p["brand"] == "Tatay"          # 剥 "Marca:" 前缀
    assert p["rating"] == "4.5"
    assert p["review_count"] == 3873
    assert p["bestseller_rank"] == 1      # 只来自榜单
    assert p["category_l1"] == "Hogar y cocina"
    assert p["leaf_category"] == "Juegos de recipientes"
    assert p["采集类目中文"] == "收纳盒套装"


def test_enrich_spec_and_product_type():
    p = enrich_products(RANKING, DETAIL)[0]
    assert p["spec_v2"] != ""
    assert "件套" in p["spec_v2"] or "4件套" in p["spec_v2"]
    # product_type 只从标题证据；fiambrera → 便当盒
    assert p["product_type"] == "便当盒"


def test_enrich_discount_only_when_original():
    d = dict(DETAIL[0], original_price_raw="")
    p = enrich_products(RANKING, [d])[0]
    assert p["discount_rate"] is None


def test_enrich_no_discount_when_original_not_greater():
    d = dict(DETAIL[0], original_price_raw="10,00 €")  # orig < cur
    p = enrich_products(RANKING, [d])[0]
    assert p["discount_rate"] is None


def test_enrich_title_zh_from_translations():
    tr = {"B078C6QR1C": {"title_zh": "玻璃便当盒 4 件套"}}
    p = enrich_products(RANKING, DETAIL, translations=tr)[0]
    assert p["title_zh"] == "玻璃便当盒 4 件套"
    # 无翻译 → 空（缺失不臆造）
    assert enrich_products(RANKING, DETAIL)[0]["title_zh"] == ""


def test_enrich_deterministic_order_by_asin():
    r2 = dict(RANKING[0], asin="B075JJRFVV", bestseller_rank=2)
    d2 = dict(DETAIL[0], asin="B075JJRFVV", current_price_raw="16,98 €")
    products = enrich_products([r2, RANKING[0]], [d2, DETAIL[0]])
    assert [p["asin"] for p in products] == ["B075JJRFVV", "B078C6QR1C"]


def test_legacy_flat_drops_fabricated_bsr():
    # 30/30 历史 BSR 都是 "n.º {Rank} en Hogar y cocina" 构造产物 → 丢弃
    rec = {"ASIN": "B078C6QR1C", "Rank": "1", "BSR": "n.º 1 en Hogar y cocina",
           "Title": "Protector", "Price_EUR": "12,62", "ListPrice_EUR": "13,29",
           "Rating": "4.6", "Reviews": "47375", "Brand": "Utopia Bedding",
           "Seller": "Utopia Brands", "Availability": "En stock",
           "SoldByAmazon": "No", "URL": "https://www.amazon.es/dp/B078C6QR1C"}
    d = legacy_flat_to_detail(rec)
    assert d["detail_bsr_raw"] == ""      # 不导入伪造 BSR
    assert d["asin"] == "B078C6QR1C"
    assert d["title_es_raw"] == "Protector"
    r = legacy_flat_to_ranking(rec)
    assert r["bestseller_rank"] == 1
    assert r["ranking_source_url"] == ""  # 无榜单页 URL → 不臆造


def test_real_30_offline_chain_qa_0_p0_p1():
    """30 条真实数据走离线主链 → QA 0 P0 / 0 P1（P2 缺失类 WARN 允许）。"""
    p = REPO / "product_details.json"
    if not p.exists():
        pytest.skip("product_details.json 不在仓库")
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data) == 30
    rankings = [legacy_flat_to_ranking(r) for r in data]
    details = [legacy_flat_to_detail(r) for r in data]
    products = enrich_products(rankings, details)
    assert len(products) == 30
    p0p1 = []
    for prod in products:
        res = run_qa(prod)
        for issue in res["qa_issues"]:
            if issue.severity in ("P0", "P1"):
                p0p1.append((prod.get("asin"), issue.code, issue.message))
    assert not p0p1, "离线主链出现 P0/P1: %r" % p0p1[:5]


def test_real_30_no_rank_bsr_mix():
    """遗留 BSR 丢弃后，bestseller_rank 绝不与 detail BSR 混用（QA_RULES §9）。"""
    p = REPO / "product_details.json"
    if not p.exists():
        pytest.skip("product_details.json 不在仓库")
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    products = enrich_products(
        [legacy_flat_to_ranking(r) for r in data],
        [legacy_flat_to_detail(r) for r in data])
    for prod in products:
        assert not prod.get("detail_bsr_segments"), "遗留 BSR 不得进入 detail_bsr_segments"
