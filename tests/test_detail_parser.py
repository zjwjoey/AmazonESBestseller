# -*- coding: utf-8 -*-
"""collection/detail.py 测试：选择器与 extract_details.js 一致，离线。"""
import datetime

from amazon_es_bestseller.collection.detail import parse_detail_page, verify_asin_on_page
from amazon_es_bestseller.normalization.dates import parse_es_date


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


def test_brand_no_title_first_word_fallback():
    # 无 #bylineInfo → brand_raw 为空，绝不取标题首词当品牌（QA_RULES §10）
    html = """
    <html><body>
      <div id="productTitle">Toallas de algodón 100%</div>
      <div id="corePrice_feature_div"><div class="a-price"><span class="a-offscreen">9,99 €</span></div></div>
    </body></html>
    """
    d = parse_detail_page(html, "B078C6QR1C")
    assert d["title_es_raw"] == "Toallas de algodón 100%"
    assert d["brand_raw"] == ""


def test_parse_lunchbag_b4_new_fields(lunchbag_html):
    """B4 低成本扩展：parent ASIN / 首次上架日期 / 商品链接 / 图片链接。"""
    d = parse_detail_page(lunchbag_html, "B075JJRFVV")
    assert d["parent_asin"] == "B075JJRFXW"
    assert d["date_first_available_raw"] == "28 octubre 2023"
    assert d["product_url"] == "https://www.amazon.es/dp/B075JJRFVV"
    assert d["image_url"] == "https://m.media-amazon.com/images/I/81x.jpg"
    # raw 与 parse_es_date 对齐 → 首次上架日期可派生（导出列 22）
    assert parse_es_date(d["date_first_available_raw"]) == datetime.date(2023, 10, 28)


def test_parse_missing_new_fields_empty():
    d = parse_detail_page("<html><body><p>hola</p></body></html>", "B078C6QR1C")
    assert d["parent_asin"] == ""
    assert d["date_first_available_raw"] == ""
    assert d["image_url"] == ""
    # product_url 由合法 ASIN 确定性派生（非业务推断）
    assert d["product_url"] == "https://www.amazon.es/dp/B078C6QR1C"


def test_parent_asin_malformed_not_trusted():
    html = '<input type="hidden" id="parentASIN" value="no-es-asin">'
    d = parse_detail_page(html, "B078C6QR1C")
    assert d["parent_asin"] == ""      # 值不合法 → 空，不臆造


def test_available_date_from_details_table():
    # 经典产品参数表 dt/dd 形式："Fecha de lanzamiento: 28 de octubre de 2023"
    html = """
    <html><body>
      <table id="productDetails_detailBullets_sections1">
        <tr><th>Fecha de lanzamiento</th><td>28 de octubre de 2023</td></tr>
      </table>
    </body></html>
    """
    d = parse_detail_page(html, "B078C6QR1C")
    assert d["date_first_available_raw"] == "28 octubre 2023"


def test_image_url_fallback_to_data_old_hires():
    html = """
    <html><body>
      <div id="imageBlock_feature_div">
        <img id="landingImage" src="data:image/gif;base64,AAA"
             data-old-hires="https://m.media-amazon.com/images/I/99z._SL1500_.jpg">
      </div>
    </body></html>
    """
    d = parse_detail_page(html, "B078C6QR1C")
    assert d["image_url"] == "https://m.media-amazon.com/images/I/99z._SL1500_.jpg"


# ---------- 无损全量详情：Product Attribute 模型（DATA_MODEL §4-§8） ----------

def test_full_detail_overview_attributes(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    overview = [a for a in d["attributes"] if a["section"] == "product_overview"]
    assert len(overview) == 4
    assert overview[0] == {"section": "product_overview", "label_raw": "Marca",
                           "value_raw": "De'Longhi", "position": 0, "source": "productOverview"}
    assert overview[2]["label_raw"] == "Aroma"
    assert overview[2]["value_raw"] == "Limón"


def test_full_detail_technical_details_attributes(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    tech = [a for a in d["attributes"] if a["section"] == "technical_details"]
    assert len(tech) == 3
    assert tech[1] == {"section": "technical_details", "label_raw": "Capacidad",
                       "value_raw": "500 mililitros", "position": 1, "source": "prodDetails"}


def test_full_detail_position_resets_per_section(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    for sec in ("product_overview", "technical_details"):
        sub = [a for a in d["attributes"] if a["section"] == sec]
        assert [a["position"] for a in sub] == list(range(len(sub)))


def test_full_detail_feature_bullets(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    assert len(d["feature_bullets_raw"]) == 3
    assert d["feature_bullets_raw"][0].startswith("SOLUCIÓN SUAVE DE DESCALCIFICACIÓN")


def test_full_detail_product_description(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    assert d["product_description_raw"].startswith("El descalcificador EcoDecalk")


def test_full_detail_detail_bullets_wrapper(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    assert any("n.º 5 en Hogar y cocina" in b for b in d["detail_bullets_raw"])
    assert any("Fecha de primera disponibilidad: 17 mayo 2021" in b for b in d["detail_bullets_raw"])


def test_full_detail_additional_information_dl():
    html = """
    <html><body>
      <div id="detailBullets_feature_div">
        <dl>
          <dt>Fabricante</dt><dd>De'Longhi Appliances</dd>
          <dt>Número de modelo</dt><dd>DLSC500</dd>
        </dl>
      </div>
    </body></html>
    """
    d = parse_detail_page(html, "B008YETL18")
    add = [a for a in d["attributes"] if a["section"] == "additional_information"]
    assert len(add) == 2
    assert add[0]["label_raw"] == "Fabricante"
    assert add[0]["value_raw"] == "De'Longhi Appliances"
    assert add[0]["source"] == "detailBullets"
    assert [a["position"] for a in add] == [0, 1]


def test_full_detail_nested_db_sections_no_double_capture():
    # 旧版布局：#prodDetails 内嵌套 techSpec + detailBullets 两张 section 表。
    # detailBullets 行只应作为 additional_information 出现一次，绝不重复成 technical_details。
    html = """
    <html><body>
      <div id="prodDetails">
        <div id="productDetails_db_sections">
          <table id="productDetails_techSpec_sections1">
            <tr><th>Capacidad</th><td>500 mililitros</td></tr>
          </table>
          <table id="productDetails_detailBullets_sections1">
            <tr><th>Fecha de lanzamiento</th><td>28 de octubre de 2023</td></tr>
            <tr><th>Fabricante</th><td>De'Longhi Appliances</td></tr>
          </table>
        </div>
      </div>
    </body></html>
    """
    d = parse_detail_page(html, "B008YETL18")
    tech = [a for a in d["attributes"] if a["section"] == "technical_details"]
    add = [a for a in d["attributes"] if a["section"] == "additional_information"]
    assert len(tech) == 1
    assert tech[0]["label_raw"] == "Capacidad"
    assert len(add) == 2
    assert add[0]["label_raw"] == "Fecha de lanzamiento"
    assert add[0]["source"] == "detailBulletsSections"
    # 全表唯一：Fecha de lanzamiento 不得出现在 technical_details
    assert not any(a["label_raw"] == "Fecha de lanzamiento" and a["section"] == "technical_details"
                   for a in d["attributes"])


def test_full_detail_missing_sections_empty():
    d = parse_detail_page("<html><body><p>hola</p></body></html>", "B078C6QR1C")
    assert d["attributes"] == []
    assert d["feature_bullets_raw"] == []
    assert d["product_description_raw"] == ""
    assert d["detail_bullets_raw"] == []


def test_verify_asin_on_page_ok():
    assert verify_asin_on_page("https://www.amazon.es/dp/B078C6QR1C", "B078C6QR1C") is True
    assert verify_asin_on_page("https://www.amazon.es/dp/B078C6QR1C", "b078c6qr1c") is True


def test_verify_asin_on_page_mismatch():
    assert verify_asin_on_page("https://www.amazon.es/dp/B075JJRFVV", "B078C6QR1C") is False


def test_verify_asin_on_page_missing():
    assert verify_asin_on_page(None, "B078C6QR1C") is False
    assert verify_asin_on_page("https://www.amazon.es/dp/B078C6QR1C", "") is False
