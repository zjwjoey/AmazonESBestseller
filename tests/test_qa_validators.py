# -*- coding: utf-8 -*-
"""qa/validators.py + qa/run.py 测试：QA_RULES §3-§79 的落地校验。"""
from amazon_es_bestseller.models import QAStatus
from amazon_es_bestseller.qa.run import qa_summary, run_qa
from amazon_es_bestseller.qa.validators import (
    validate_asin,
    validate_bilingual_match,
    validate_brand,
    validate_category,
    validate_image_asin,
    validate_monthly_bought,
    validate_price,
    validate_rank_separation,
    validate_rating,
    validate_review_count,
    validate_source_conflict,
    validate_spec,
    validate_url_asin,
)


# ---------- ASIN ----------
def test_validate_asin_ok():
    assert validate_asin("b078c6qr1c") == (QAStatus.PASS, [])


def test_validate_asin_invalid():
    status, issues = validate_asin("ABC")
    assert status == QAStatus.FAIL
    assert issues[0].code == "ASIN_INVALID"
    assert issues[0].severity == "P0"


def test_validate_asin_missing():
    status, issues = validate_asin(None)
    assert status == QAStatus.FAIL
    assert issues[0].code == "ASIN_INVALID"


# ---------- URL ----------
def test_validate_url_asin_ok():
    assert validate_url_asin("B078C6QR1C", "https://www.amazon.es/dp/B078C6QR1C") == (QAStatus.PASS, [])


def test_validate_url_asin_mismatch():
    status, issues = validate_url_asin("B078C6QR1C", "https://www.amazon.es/dp/B075JJRFVV")
    assert status == QAStatus.FAIL
    assert issues[0].code == "URL_ASIN_MISMATCH"
    assert issues[0].severity == "P0"


def test_validate_url_asin_missing_url_not_failed():
    assert validate_url_asin("B078C6QR1C", None) == (QAStatus.PASS, [])


# ---------- 图片归属 ----------
def test_validate_image_asin_ok():
    assert validate_image_asin("B078C6QR1C", "B078C6QR1C") == (QAStatus.PASS, [])


def test_validate_image_asin_mismatch():
    status, issues = validate_image_asin("B078C6QR1C", "B075JJRFVV")
    assert status == QAStatus.FAIL
    assert issues[0].code == "IMAGE_ASIN_MISMATCH"


# ---------- 价格 ----------
def test_validate_price_ok():
    status, issues = validate_price("12,62", "13,29", currency="EUR", discount_rate="0.0504")
    assert status == QAStatus.PASS
    assert issues == []


def test_validate_price_unparseable():
    status, issues = validate_price("sin precio")
    assert status == QAStatus.FAIL
    assert issues[0].code == "PRICE_INVALID"


def test_validate_price_non_positive():
    status, issues = validate_price("0")
    assert status == QAStatus.FAIL
    assert issues[0].code == "PRICE_INVALID"


def test_validate_price_wrong_currency():
    status, issues = validate_price("12,62", currency="USD")
    assert status == QAStatus.FAIL
    assert issues[0].code == "PRICE_INVALID"


def test_validate_price_discount_without_evidence():
    # §17-§18：折扣必须有原价证据且原价>现价
    status, issues = validate_price("12,62", discount_rate="0.2309")
    assert status == QAStatus.FAIL
    assert issues[0].code == "PRICE_INVALID"


def test_validate_price_no_discount_ok():
    assert validate_price("16,98", original_price="", discount_rate="") == (QAStatus.PASS, [])


def test_validate_price_rejects_present_equal_original():
    status, issues = validate_price(14.99, 14.99, "EUR", None)
    assert status == QAStatus.FAIL
    assert any(i.code == "PRICE_INVALID" for i in issues)


# ---------- 评分 ----------
def test_validate_rating_ok():
    assert validate_rating("4,5 de 5 estrellas (3873)") == (QAStatus.PASS, [])


def test_validate_rating_out_of_range():
    # QA_RULES §20："45" 是危险的解析结果，必须 FAIL
    status, issues = validate_rating("45")
    assert status == QAStatus.FAIL
    assert issues[0].code == "RATING_INVALID"


def test_validate_rating_unparseable():
    status, issues = validate_rating("no rating")
    assert status == QAStatus.FAIL
    assert issues[0].code == "RATING_INVALID"


# ---------- 评论数 ----------
def test_validate_review_count_thousands():
    # QA_RULES §21：3.873 → 3873（千位点，不是小数）
    assert validate_review_count("3.873") == (QAStatus.PASS, [])
    assert validate_review_count("12.455") == (QAStatus.PASS, [])


def test_validate_review_count_unparseable():
    status, issues = validate_review_count("abc")
    assert status == QAStatus.FAIL
    assert issues[0].code == "REVIEW_COUNT_INVALID"


def test_validate_review_count_modern_paren_format():
    # 现代 Amazon.es 页面（2026-08-26 真实核实）：评论数显示为括号包裹
    # "(8.819)"（点=千位分隔，西语）→ 应解析通过，不误报 REVIEW_COUNT_INVALID
    assert validate_review_count("(8.819)") == (QAStatus.PASS, [])
    assert validate_review_count("(24.280)") == (QAStatus.PASS, [])


# ---------- 品牌 ----------
def test_validate_brand_ok():
    assert validate_brand("Tatay", "Tatay") == (QAStatus.PASS, [])


def test_validate_brand_missing_warn():
    status, issues = validate_brand("")
    assert status == QAStatus.WARN
    assert issues[0].code == "BRAND_MISSING"


def test_validate_brand_false_positive():
    # QA_RULES §24/§71：Limpiador 不得当品牌
    status, issues = validate_brand("Limpiador")
    assert status == QAStatus.FAIL
    assert issues[0].code == "BRAND_FALSE_POSITIVE"


def test_validate_brand_raw_uncleaned_prefix():
    # 显示前缀未清理 → 不干净证据
    status, issues = validate_brand("Tatay", "Marca: Tatay")
    assert status == QAStatus.FAIL
    assert issues[0].code == "BRAND_FALSE_POSITIVE"


def test_validate_brand_false_positive_expanded():
    # 真实标题首词证据：普通西语名词不得当品牌
    for b in ("Toallas", "Renovador", "Recambios", "Lote", "Set", "Pack"):
        status, issues = validate_brand(b)
        assert status == QAStatus.FAIL, b
        assert issues[0].code == "BRAND_FALSE_POSITIVE"


def test_validate_brand_long_phrase_suspicious():
    # 标题片段误判：超过 4 词或含停用词 → FAIL
    status, issues = validate_brand("Juego de sábanas de algodón egipcio")
    assert status == QAStatus.FAIL
    assert issues[0].code == "BRAND_FALSE_POSITIVE"


def test_validate_brand_real_brand_not_flagged():
    assert validate_brand("Todocama") == (QAStatus.PASS, [])
    assert validate_brand("Haberdashery Online") == (QAStatus.PASS, [])


# ---------- 规格 ----------
def _spec_record(details, **kw):
    r = {"details_json": details}
    r.update(kw)
    return r


def test_validate_spec_capacity_unit_mismatch():
    # QA_RULES §41：30cm 不能进容量
    r = _spec_record({"capacidad": "30cm"})
    status, issues = validate_spec(r)
    assert status == QAStatus.FAIL
    assert issues[0].code == "SPEC_UNIT_MISMATCH"


def test_validate_spec_capacity_weight_mismatch():
    # QA_RULES §42：992g 不能进容量
    r = _spec_record({"capacidad": "992g"})
    status, issues = validate_spec(r)
    assert status == QAStatus.FAIL
    assert issues[0].code == "SPEC_UNIT_MISMATCH"


def test_validate_spec_suspicious_dimension():
    # QA_RULES §45：1×1×1cm 占位
    r = _spec_record({"dimensiones_del_articulo_largo_x_ancho_x_alto": "1x1x1cm"})
    status, issues = validate_spec(r)
    assert status == QAStatus.WARN
    assert issues[0].code == "SPEC_SUSPICIOUS_VALUE"


def test_validate_spec_quantity_conflict():
    # QA_RULES §38：标题 4 件套 vs 泛型 package 数量 1
    r = _spec_record({"numero_de_articulos": "1"}, title_es_raw="Fiambrera de cristal con 4 piezas")
    status, issues = validate_spec(r)
    assert status == QAStatus.FAIL
    assert issues[0].code == "SPEC_QUANTITY_CONFLICT"


def test_validate_spec_ok():
    r = _spec_record({"tipo_de_material": "Acero inoxidable", "numero_de_articulos": "4"},
                     title_es_raw="Fiambrera de cristal con 4 piezas", spec_v2="4件套")
    assert validate_spec(r) == (QAStatus.PASS, [])


def test_validate_spec_variant_mismatch():
    # QA_RULES §37/§44：选中变体 30L 但规格输出仍是 20升 → FAIL
    r = _spec_record({"capacidad": "20 litros"}, selected_variation_raw="30L",
                     title_es_raw="Fiambrera de cristal con 4 piezas", spec_v2="20升")
    status, issues = validate_spec(r)
    assert status == QAStatus.FAIL
    assert issues[0].code == "SPEC_VARIANT_MISMATCH"


def test_validate_spec_variant_ok():
    # 变体 30L 已反映到规格输出 30升 → PASS
    r = _spec_record({"capacidad": "20 litros"}, selected_variation_raw="30L",
                     title_es_raw="Fiambrera de cristal con 4 piezas", spec_v2="30升")
    assert validate_spec(r) == (QAStatus.PASS, [])


# ---------- 排名与 BSR 隔离 ----------
def test_validate_rank_separation_mixed():
    # QA_RULES §9/§72：180285（detail BSR）绝不能进 bestseller_rank
    r = {"bestseller_rank": "180285", "detail_bsr_segments": [("Hogar y cocina", 180285)]}
    status, issues = validate_rank_separation(r)
    assert status == QAStatus.FAIL
    assert issues[0].code == "RANK_BSR_MIXED"
    assert issues[0].severity == "P0"


def test_validate_rank_separation_ok():
    r = {"bestseller_rank": "52", "detail_bsr_raw": "nº180285 en Hogar y cocina",
         "ranking_source_url": "https://www.amazon.es/Best-Sellers",
         "collected_at": "2026-08-26"}
    assert validate_rank_separation(r) == (QAStatus.PASS, [])


def test_validate_rank_separation_same_value_with_source_ok():
    # 合法同值：商品既是某子类榜单第 1、详情 BSR 也恰好第 1（2026-08-26 真实：
    # B078C6QR1C 榜单 kitchen 顶级第 1，detail_bsr "n.º 1 en Hogar y cocina"）。
    # 有独立榜单来源 → 数值碰巧相等不是混用。
    r = {"bestseller_rank": "1",
         "detail_bsr_segments": [("Hogar y cocina", 1)],
         "ranking_source_url": "https://www.amazon.es/gp/bestsellers/kitchen/",
         "collected_at": "2026-08-26"}
    assert validate_rank_separation(r) == (QAStatus.PASS, [])


def test_validate_rank_source_missing_warn():
    # QA_RULES §10：排名存在但无来源上下文 → WARN
    r = {"bestseller_rank": "52"}
    status, issues = validate_rank_separation(r)
    assert status == QAStatus.WARN
    assert issues[0].code == "RANK_SOURCE_MISSING"


# ---------- 类目 ----------
def test_validate_category_duplicated_level():
    # QA_RULES §13：L2=L3 复制充数
    r = {"category_l2": "家居与厨房", "category_l3": "家居与厨房", "leaf_category": "收纳盒套装"}
    status, issues = validate_category(r)
    assert status == QAStatus.FAIL
    assert issues[0].code == "CATEGORY_DUPLICATED_LEVEL"


def test_validate_category_unverified_leaf_warn():
    # QA_RULES §73：leaf 存在但无榜单证据
    r = {"category_l2": "Hogar y cocina", "leaf_category": "Juegos de recipientes"}
    status, issues = validate_category(r)
    assert status == QAStatus.WARN
    assert issues[0].code == "CATEGORY_UNVERIFIED_LEAF"


def test_validate_category_ok():
    r = {"category_l2": "Hogar y cocina", "leaf_category": "Juegos de recipientes",
         "browse_node_id": "428163031"}
    assert validate_category(r) == (QAStatus.PASS, [])


# ---------- 中西对照 ----------
def test_validate_bilingual_allowed_model_text():
    # QA_RULES §33：Dyson V15 属兼容型号，不判残留
    r = {"title_zh": "适用于 Dyson V15 的吸尘器支架"}
    assert validate_bilingual_match(r) == (QAStatus.PASS, [])


def test_validate_bilingual_untranslated_warn():
    # QA_RULES §32：拉丁营销残留 → WARN
    r = {"title_zh": "保鲜盒 para comida"}
    status, issues = validate_bilingual_match(r)
    assert status == QAStatus.WARN
    assert issues[0].code == "TITLE_UNTRANSLATED_TEXT"


# ---------- 源冲突 ----------
def test_validate_source_conflict_detected():
    # QA_RULES §29/§58：标题保温包 vs 细节 fiambrera → SOURCE_CONFLICT
    r = {"title_es_raw": "Bolsa térmica para comer",
         "details_json": {"tipo_de_material": "fiambrera de cristal"}}
    status, issues = validate_source_conflict(r)
    assert status == QAStatus.SOURCE_CONFLICT
    assert issues[0].code == "SOURCE_CONFLICT"
    assert issues[0].severity == "P0"


def test_validate_source_conflict_ok():
    r = {"title_es_raw": "Bolsa térmica para comer",
         "summary_v2": "材质：涤纶"}
    assert validate_source_conflict(r) == (QAStatus.PASS, [])


# ---------- 月购 ----------
def test_validate_monthly_bought_ok():
    r = {"monthly_bought_raw": "100+ comprados el mes pasado", "monthly_bought_min": "100"}
    assert validate_monthly_bought(r) == (QAStatus.PASS, [])


def test_validate_monthly_bought_unparseable():
    r = {"monthly_bought_raw": "abc", "monthly_bought_min": ""}
    status, issues = validate_monthly_bought(r)
    assert status == QAStatus.WARN
    assert issues[0].code == "MONTHLY_BOUGHT_UNPARSEABLE"


def test_validate_monthly_bought_inconsistent():
    r = {"monthly_bought_raw": "100+", "monthly_bought_min": "200"}
    status, issues = validate_monthly_bought(r)
    assert status == QAStatus.WARN


def test_validate_monthly_bought_inconsistency_is_warning():
    status, issues = validate_monthly_bought({
        "monthly_bought_raw": "100+", "monthly_bought_min": 50})
    assert status == QAStatus.WARN
    assert issues[0].severity == "P2"


def test_validate_source_conflict_reads_attributes():
    status, issues = validate_source_conflict({
        "title_es_raw": "Producto reutilizable",
        "attributes": [{"label_raw": "Tipo", "value_raw": "Tamper"}],
        "product_type": "可重复使用"})
    assert status == QAStatus.SOURCE_CONFLICT
    assert issues[0].code == "SOURCE_CONFLICT"


# ---------- 聚合 run_qa ----------
def _clean_record():
    return {
        "asin": "B078C6QR1C",
        "product_url": "https://www.amazon.es/dp/B078C6QR1C",
        "current_price": "12,62", "original_price": "13,29", "discount_rate": "0.0504",
        "rating_raw": "4,5 de 5 estrellas (3873)", "review_count_raw": "3.873",
        "brand": "Tatay", "brand_raw": "Tatay",
        "title_es_raw": "Fiambrera de cristal con 4 piezas",
        "details_json": {"tipo_de_material": "Acero inoxidable", "numero_de_articulos": "4"},
        "monthly_bought_raw": "100+ comprados el mes pasado", "monthly_bought_min": "100",
        "bestseller_rank": "1",
        "category_l2": "Hogar y cocina", "leaf_category": "Juegos de recipientes",
        "browse_node_id": "428163031",
        "ranking_source_url": "https://www.amazon.es/Best-Sellers",
    }


def test_run_qa_pass():
    out = run_qa(_clean_record())
    assert out["qa_status"] == "PASS"
    assert out["qa_issues"] == []
    assert out["counts"] == {"P0": 0, "P1": 0, "P2": 0, "P3": 0}


def test_run_qa_warn_on_brand_missing():
    r = _clean_record()
    r["brand"] = ""
    out = run_qa(r)
    assert out["qa_status"] == "WARN"
    assert any(i.code == "BRAND_MISSING" for i in out["qa_issues"])


def test_run_qa_fail_on_rank_mixed():
    # 旧构造模式：bestseller_rank 无独立榜单来源（无 ranking_source_url /
    # collected_at）且数值与详情 BSR 重合 → 疑似污染
    r = _clean_record()
    r.pop("ranking_source_url", None)
    r["bestseller_rank"] = "180285"
    r["detail_bsr_segments"] = [("", 180285)]
    out = run_qa(r)
    assert out["qa_status"] == "FAIL"
    assert any(i.code == "RANK_BSR_MIXED" for i in out["qa_issues"])
    assert out["counts"]["P0"] == 1


def test_run_qa_source_conflict_beats_fail():
    # D4：SOURCE_CONFLICT > FAIL
    r = _clean_record()
    r["title_es_raw"] = "Bolsa térmica para comer"
    r["details_json"] = {"fiambrera": "de cristal"}
    r["bestseller_rank"] = "180285"
    r["detail_bsr_segments"] = [("", 180285)]
    out = run_qa(r)
    assert out["qa_status"] == "SOURCE_CONFLICT"


# ---------- 汇总 ----------
def test_qa_summary_on_tiny_records(tiny_records):
    summary = qa_summary(tiny_records)
    assert summary["total_products"] == 3
    assert summary["pass_count"] == 3
    assert summary["warn_count"] == 0
    assert summary["fail_count"] == 0
    assert summary["source_conflict_count"] == 0
    fill = summary["field_completeness"]
    assert fill["asin"] == 3
    assert fill["brand"] == 3
    assert fill["original_price"] == 2   # 记录2 划线价为空
    assert fill["image_url"] == 3
    assert fill["monthly_bought_min"] == 1  # 只有记录3 有月购
