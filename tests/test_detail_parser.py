# -*- coding: utf-8 -*-
"""collection/detail.py 测试：选择器与 extract_details.js 一致，离线。"""
from amazon_es_bestseller.collection.detail import parse_detail_page, verify_asin_on_page


def test_parse_lunchbag_full(lunchbag_html):
    d = parse_detail_page(lunchbag_html, "B075JJRFVV")
    assert d["asin"] == "B075JJRFVV"
    assert d["is_captcha"] is False
    assert d["title_es_raw"] == "Bolsa térmica para almuerzo isotérmica con 4 compartimentos"
    assert d["current_price_raw"] == "12,62 €"
    assert d["original_price_raw"] == "13,29 €"
    assert d["rating_raw"] == "4,5 de 5 estrellas"
    assert d["review_count_raw"] == "3.873"
    assert d["availability_raw"] == "En stock"
    # BSR 只取主排名，在 '(' 截断
    assert d["detail_bsr_raw"] == "n.º 233 en Hogar y cocina"
    # 卖家来自 merchantInfo
    assert d["seller_raw"] == "Utopia Brands"
    # 品牌剥 "Visita la tienda de"
    assert d["brand_raw"] == "Utopia Bedding"
    assert d["selected_variation_raw"] == "4 piezas"
    assert d["sold_by_amazon"] is True
    assert d["fulfilled_by_amazon"] is False


def test_parse_captcha_page(captcha_html):
    d = parse_detail_page(captcha_html, "B075JJRFVV")
    assert d["is_captcha"] is True
    # 验证码页不给空解析制造"假字段"
    assert d["title_es_raw"] == ""


def test_parse_marca_brand_prefix():
    html = """
    <html><body>
      <div id="productTitle">Fiambrera</div>
      <div id="bylineInfo">Marca: Tatay</div>
      <div id="corePrice_feature_div"><div class="a-price"><span class="a-offscreen">9,99 €</span></div></div>
    </body></html>
    """
    d = parse_detail_page(html, "B078C6QR1C")
    assert d["brand_raw"] == "Tatay"
    assert d["current_price_raw"] == "9,99 €"


def test_parse_missing_fields_empty():
    d = parse_detail_page("<html><body><p>hola</p></body></html>", "B078C6QR1C")
    assert d["title_es_raw"] == ""
    assert d["current_price_raw"] == ""
    assert d["detail_bsr_raw"] == ""
    assert d["brand_raw"] == ""


def test_verify_asin_on_page_ok():
    assert verify_asin_on_page("https://www.amazon.es/dp/B078C6QR1C", "B078C6QR1C") is True
    assert verify_asin_on_page("https://www.amazon.es/dp/B078C6QR1C", "b078c6qr1c") is True


def test_verify_asin_on_page_mismatch():
    assert verify_asin_on_page("https://www.amazon.es/dp/B075JJRFVV", "B078C6QR1C") is False


def test_verify_asin_on_page_missing():
    assert verify_asin_on_page(None, "B078C6QR1C") is False
    assert verify_asin_on_page("https://www.amazon.es/dp/B078C6QR1C", "") is False
