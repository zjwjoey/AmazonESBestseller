from amazon_es_bestseller.translation.dictionary_only import profile_records, run_dictionary_only


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
    assert fields["selected_variation_zh"]["resolved_text"] == "单件"
    assert "selected_variant_zh" not in fields
    assert fields["product_details_zh"]["status"] == "resolved"
    assert "OEM参考号：20002" in fields["product_details_zh"]["resolved_text"]
    assert fields["title_zh"]["status"] == "unresolved"
    assert fields["title_zh"]["resolved_text"] == fields["title_zh"]["source_text"]
    assert result["summary"]["fields"]["product_details_label"]["dictionary"] >= 3
    assert result["summary"]["identity_attributes"][0]["source_preserved"] >= 1


def test_profile_includes_raw_detail_and_bullet_fields():
    profile = profile_records([{
        "asin": "B000000002",
        "product_details_es": "Marca: Metal\nPeso: 1 kg",
        "feature_bullets_es": ["Resistente", "Compacto"],
    }])
    assert profile["profiles"]["product_details_es"]["nonempty"] == 1
    assert profile["profiles"]["feature_bullets_es"]["nonempty"] == 1
