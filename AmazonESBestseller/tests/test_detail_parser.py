from amazon_es_bestseller.detail_parser import parse_detail_page


def test_detail_parser_keeps_structured_and_readable_details():
    html = """
    <span id="productTitle">Protector de colchón</span>
    <a id="bylineInfo">Marca</a>
    <div data-parent-asin="B099999999"></div>
    <div id="variation_size_name"><span class="selection">90 x 190 cm</span></div>
    <div id="detailBullets_feature_div">
      <ul>
        <li><span>Fecha de disponibilidad</span><span>31 de enero de 2024</span></li>
        <li><span>Material</span><span>Poliéster</span></li>
        <li><span>País de origen</span><span>España</span></li>
      </ul>
    </div>
    <div id="feature-bullets"><ul><li>Impermeable</li><li>Lavable a máquina</li></ul></div>
    """

    detail = parse_detail_page(html, asin="B012345678")

    assert detail.asin == "B012345678"
    assert detail.parent_asin == "B099999999"
    assert detail.details_json["brand"] == "Marca"
    assert detail.details_json["material"] == "Poliéster"
    assert detail.details_json["features"] == ["Impermeable", "Lavable a máquina"]
    assert "brand: Marca" in detail.details
    assert detail.specification == "90 x 190 cm"
    assert detail.date_first_available == "2024-01-31"
    assert detail.date_first_available_raw == "31 de enero de 2024"


def test_detail_parser_extracts_parent_asin_from_embedded_json():
    html = '<script>var data = {"parentAsin":"B012345678","asin":"B099999999"};</script>'

    detail = parse_detail_page(html, asin="B099999999")

    assert detail.parent_asin == "B012345678"


def test_detail_parser_uses_explicit_labels_without_concatenating_values_into_keys():
    html = """
    <div id="detailBullets_feature_div"><ul>
      <li><span class="a-text-bold">Material :</span><span> Acero inoxidable</span></li>
    </ul></div>
    <table id="productDetails_techSpec_section_1">
      <tr><th>Producto en Amazon.es desde</th><td>28 de octubre de 2023</td></tr>
      <tr><th>País de origen</th><td>España</td></tr>
    </table>
    """

    detail = parse_detail_page(html, asin="B012345678")

    assert detail.details_json["material"] == "Acero inoxidable"
    assert detail.details_json["country_of_origin"] == "España"
    assert "producto_en_amazon_es_desde" not in detail.details_json
    assert detail.date_first_available == "2023-10-28"
    assert detail.date_first_available_raw == "28 de octubre de 2023"
