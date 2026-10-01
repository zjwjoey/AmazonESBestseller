from amazon_es_bestseller.translation.terminology import postprocess


def test_v2_reuses_deterministic_terms_without_touching_source():
    source = "Acero inoxidable 500 ml"
    assert postprocess("title_es_raw", source, source) == "不锈钢 500 毫升"


def test_brand_postprocess_is_identity_preserving():
    assert postprocess("brand", "翻译后的品牌", "HOVVIDA") == "HOVVIDA"
