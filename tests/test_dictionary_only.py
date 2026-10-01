from amazon_es_bestseller.translation.dictionary_only import run_dictionary_only


def test_dictionary_only_is_offline_and_preserves_unresolved_source():
    result = run_dictionary_only([
        {
            "asin": "B000000001",
            "title_es_raw": "Producto desconocido 5200 mAh",
            "brand": "Metal",
            "category_l1": "Coche y moto",
            "selected_variant_es": "Unidad",
            "specification_es": "Peso: 4,25 Kilogramos",
            "product_details_es": "Marca: Metal\nReferencia OEM: 20002\nRequiere montaje: No",
            "feature_bullets_es": "Texto no cubierto",
        }
    ])
    summary = result["summary"]
    assert summary["qwen_api_calls"] == 0
    fields = result["records"]["B000000001"]["fields"]
    assert fields["category_l1_zh"]["resolved_text"] == "汽车与摩托车用品"
    assert fields["selected_variant_zh"]["resolved_text"] == "单件"
    assert fields["product_details_zh"]["status"] == "resolved"
    assert "OEM参考号：20002" in fields["product_details_zh"]["resolved_text"]
    assert fields["title_zh"]["status"] == "unresolved"
    assert fields["title_zh"]["resolved_text"] == fields["title_zh"]["source_text"]
