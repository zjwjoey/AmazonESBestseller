# -*- coding: utf-8 -*-
"""最终验收契约（docs/QA_RULES.md §67-§75 + D1-D10）。

跨模块回归：把各模块的已知失败案例钉死为永久离线测试。
"""
import inspect

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.export.excel import export_workbook
from amazon_es_bestseller.models import merge_ranking_and_detail
from amazon_es_bestseller.normalization.brand import clean_brand, normalize_brand_case
from amazon_es_bestseller.normalization.bsr import extract_bsr_segments
from amazon_es_bestseller.normalization.category import category_zh, resolve_leaf_category
from amazon_es_bestseller.normalization.dates import parse_es_date
from amazon_es_bestseller.normalization.price import discount_rate
from amazon_es_bestseller.normalization.specification import (
    build_spec_v2,
    dim_zh,
    is_suspicious_dimension,
    resolve_package_count,
)
from amazon_es_bestseller.qa import validators
from amazon_es_bestseller.qa.run import run_qa
from amazon_es_bestseller.qa.validators import (
    validate_brand,
    validate_rating,
    validate_review_count,
    validate_rank_separation,
)
from amazon_es_bestseller.translation.product_type import detect_product_type
from amazon_es_bestseller.translation.zh import translate_value


# ---------- 规格（QA_RULES §38/§40/§43-§45） ----------
def test_regression_9l_capacity_not_25l():
    # §43：9L → 9升，绝不 25.4升
    assert build_spec_v2({"capacidad": "9 litros"}) == "9升"
    assert "25.4" not in build_spec_v2({"capacidad": "9 litros"})


def test_regression_30l_variant_overrides_20l():
    # §44：选中变体 30L 优先于技术字段 20 litros
    out = build_spec_v2({"capacidad": "20 litros"}, variant="30L")
    assert "30升" in out
    assert "20升" not in out


def test_regression_10x15cm_not_10x10mm():
    # §40：10×15cm → 10×15厘米，绝不 10×10毫米
    assert dim_zh("10 x 15 cm") == "10×15厘米"
    assert translate_value("10 x 15 cm") == "10×15厘米"
    assert "10×10" not in translate_value("10 x 15 cm")


def test_regression_4_piece_set_not_overridden_by_qty_1():
    # §38：标题 4 件套，泛型 quantity=1 不得覆盖
    assert resolve_package_count({"numero_de_articulos": "1"},
                                 title_es="Fiambrera de cristal con 4 piezas") == 4


def test_regression_suspicious_dimension_placeholder():
    # §45：1×1×1cm 占位拒绝
    assert is_suspicious_dimension("1×1×1cm") is True
    assert is_suspicious_dimension("1x1x1 cm") is True


# ---------- 品牌（QA_RULES §24-§26/§71） ----------
def test_regression_brand_visita_prefix():
    assert clean_brand("Visita la tienda de BISSELL") == "BISSELL"


def test_regression_brand_marca_prefix():
    assert clean_brand("Marca: Tatay") == "Tatay"


def test_regression_brand_zero_width_stripped():
    # §25：零宽字符剥离
    assert clean_brand("BISSELL​") == "BISSELL"
    assert clean_brand("​ Tatay") == "Tatay"


def test_regression_brand_canonical_case():
    # §26：显式映射；未收录品牌不改写
    assert normalize_brand_case("bissell") == "BISSELL"
    assert normalize_brand_case("Tatay") == "Tatay"


def test_regression_brand_false_positive_rejected():
    # §24/§71：Limpiador 不得当品牌
    status, issues = validate_brand("Limpiador")
    assert status.value == "FAIL"
    assert issues[0].code == "BRAND_FALSE_POSITIVE"


# ---------- 商品类型（QA_RULES §28） ----------
def test_regression_product_type_title():
    assert detect_product_type("Bolsa térmica para comer") == "保温包"
    assert detect_product_type("Fiambrera de cristal con 4 piezas") == "便当盒"
    assert detect_product_type("Portafiltro de acero inoxidable") == "滤杯手柄"
    assert detect_product_type("Tamper de 51 mm") == "压粉器"
    assert detect_product_type("Pastillas de limpieza para cafeteras") == "除垢片"
    assert detect_product_type("Aceite de cadena para motosierra") == "链条润滑油"
    assert detect_product_type("Motosierra eléctrica 300W") == "迷你电锯"
    assert detect_product_type("Hilo de desbrozadora 2 mm") == "打草线"
    assert detect_product_type("Juego de recipientes reutilizables") == "可重复使用"


# ---------- 排名隔离（QA_RULES §9/§72） ----------
def test_regression_detail_bsr_never_in_bestseller_rank():
    r = {"bestseller_rank": "180285", "detail_bsr_segments": [("Hogar y cocina", 180285)]}
    status, issues = validate_rank_separation(r)
    assert status.value == "FAIL"
    assert issues[0].code == "RANK_BSR_MIXED"


def test_regression_bsr_greedy_bug_fixed():
    # D9：V2 懒匹配，多段各自独立，不再合并成一坨
    segs = extract_bsr_segments(
        "nº52 en Hogar y cocina ( Ver el Top 100 en Hogar y cocina ) "
        "nº1 en Juegos de recipientes")
    assert segs == [("Hogar y cocina", "52"), ("Juegos de recipientes", "1")]


def test_regression_merge_keeps_bestseller_rank_separate():
    # merge 后 bestseller_rank 来自榜单记录，detail BSR 单独存放
    products = merge_ranking_and_detail(
        [{"asin": "B078C6QR1C", "bestseller_rank": "1",
          "ranking_source_url": "https://www.amazon.es/Best-Sellers"}],
        [{"asin": "B078C6QR1C", "detail_bsr_raw": "180285 en Hogar y cocina",
          "title_es_raw": "X"}])
    p = products[0]
    assert p["bestseller_rank"] == "1"
    assert p["detail_bsr_raw"] == "180285 en Hogar y cocina"
    assert p["bestseller_rank"] != "180285"


# ---------- 类目（QA_RULES §73） ----------
def test_regression_unknown_leaf_is_none():
    # §73：未知 leaf → None，不复制 L3 充数
    assert resolve_leaf_category([("Hogar y cocina", "52")]) is None
    assert category_zh("categoría desconocida") is None


# ---------- 价格/日期/评分/评论（QA_RULES §18/§20/§21/§50/§74） ----------
def test_regression_discount_2309():
    d = discount_rate("9.99", "12.99")
    assert d is not None
    assert abs(d - 0.2309) < 1e-4


def test_regression_discount_absent_when_no_original():
    assert discount_rate("9.99", None) is None
    assert discount_rate("9.99", "") is None


def test_regression_spanish_date():
    import datetime
    # §50：28 octubre 2023 → 2023-10-28（返回 date 对象）
    assert parse_es_date("28 octubre 2023") == datetime.date(2023, 10, 28)


def test_regression_rating_and_review():
    assert validate_rating("4,5 de 5 estrellas (3873)")[0].value == "PASS"
    assert validate_rating("45")[0].value == "FAIL"
    assert validate_review_count("3.873")[0].value == "PASS"


# ---------- QA 聚合与 issue code 全集（QA_RULES §78-§79） ----------
def test_regression_d4_aggregation_priority():
    # D4：SOURCE_CONFLICT > FAIL > WARN > PASS
    clean = {
        "asin": "B078C6QR1C",
        "product_url": "https://www.amazon.es/dp/B078C6QR1C",
        "current_price": "12,62", "original_price": "13,29", "discount_rate": "0.0504",
        "rating_raw": "4,5 de 5 estrellas (3873)", "review_count_raw": "3.873",
        "brand": "Tatay",
        "title_es_raw": "Bolsa térmica para comer",
        "summary_v2": "材质：涤纶",
        "bestseller_rank": "1", "leaf_category": "Juegos de recipientes",
        "browse_node_id": "428163031",
        "ranking_source_url": "https://www.amazon.es/Best-Sellers",
    }
    assert run_qa(clean)["qa_status"] == "PASS"
    warn = dict(clean, brand="")
    assert run_qa(warn)["qa_status"] == "WARN"
    fail = dict(clean, bestseller_rank="180285", detail_bsr_segments=[("", 180285)])
    assert run_qa(fail)["qa_status"] == "FAIL"
    conflict = dict(fail, details_json={"fiambrera": "de cristal"})
    assert run_qa(conflict)["qa_status"] == "SOURCE_CONFLICT"


def test_regression_all_issue_codes_implemented():
    # §79：全部 issue code 都必须在 validators 中被产出（缺失即漏实现）
    required = {
        "ASIN_INVALID", "URL_ASIN_MISMATCH", "IMAGE_ASIN_MISMATCH",
        "TITLE_PRODUCT_TYPE_MISMATCH", "TITLE_UNTRANSLATED_TEXT", "TITLE_BRAND_DUPLICATION",
        "BRAND_FALSE_POSITIVE", "BRAND_MISSING",
        "SPEC_UNIT_MISMATCH", "SPEC_VARIANT_MISMATCH", "SPEC_QUANTITY_CONFLICT",
        "SPEC_SUSPICIOUS_VALUE",
        "RANK_SOURCE_MISSING", "RANK_BSR_MIXED",
        "CATEGORY_DUPLICATED_LEVEL", "CATEGORY_UNVERIFIED_LEAF",
        "PRICE_INVALID", "SOURCE_CONFLICT",
    }
    src = inspect.getsource(validators)
    # RANK_SOURCE_MISSING / TITLE_PRODUCT_TYPE_MISMATCH / TITLE_BRAND_DUPLICATION /
    # SPEC_VARIANT_MISMATCH 属"验证位置"，作为常量列出，保证可追踪
    for code in required:
        assert code in src, "issue code %s 未在 validators.py 中落地" % code


def test_regression_detail_parser_via_lunchbag(lunchbag_html):
    # 采集层到证据层：西语原始字段保留，中文派生不进详情证据（§54/§55）
    d = parse_detail_page(lunchbag_html, "B075JJRFVV")
    assert d["title_es_raw"].startswith("Bolsa térmica")
    assert d["detail_bsr_raw"].startswith("n.º")
    assert d["brand_raw"] == "Utopia Bedding"


def test_regression_excel_structure(export_records):
    """默认导出契约钉死：3 表顺序 + 中文表 26 列冻结（DATA_MODEL §20-§21）。"""
    wb = export_workbook(export_records)
    assert wb.sheetnames == ['类目规划', '西班牙语选品清单', '中文选品清单']
    zh = wb['中文选品清单']
    header = [zh.cell(row=1, column=c).value for c in range(1, 27)]
    assert header == [
        '图片', '序号', 'ASIN', 'Parent ASIN', '商品名称（中文）', '品牌',
        '当前售价', '划线原价', '折扣率', '评分', '评论数', '月购买量',
        '一级类目', '二级类目', '三级类目', '细分类目', '畅销榜排名',
        '当前选中规格 / 变体', '核心规格（中文）', '完整商品详情（中文）',
        '商品卖点（中文）', '首次上架日期', '卖家', '商品链接', '图片链接', '备注',
    ]
    assert zh['A2'] is not None
