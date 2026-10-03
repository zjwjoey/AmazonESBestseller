from amazon_es_bestseller.identity import resolve_identity


def test_identity_resolver_exact_match():
    result = resolve_identity(ranking_asin="B000000001", parsed_detail_asin="B000000001")
    assert result["identity_status"] == "IDENTITY_MATCH"


def test_identity_resolver_parent_requires_explicit_family():
    result = resolve_identity(ranking_asin="B000000001", parsed_detail_asin="B000000002",
                              parent_asin="B000000002",
                              variation_family_asins=["B000000001", "B000000002"])
    assert result["identity_status"] == "PARENT_ASIN_MATCH"


def test_identity_resolver_sibling_variation_is_related():
    result = resolve_identity(ranking_asin="B000000001", parsed_detail_asin="B000000002",
                              variation_family_asins=["B000000001", "B000000002", "B000000003"])
    assert result["identity_status"] == "VARIATION_RELATED"


def test_identity_resolver_unrelated_is_mismatch():
    result = resolve_identity(ranking_asin="B000000001", parsed_detail_asin="B000000099")
    assert result["identity_status"] == "IDENTITY_MISMATCH"


def test_final_url_is_evidence_not_an_automatic_quarantine():
    result = resolve_identity(ranking_asin="B000000001", parsed_detail_asin="B000000002",
                              final_url_asin="https://www.amazon.es/dp/B000000002",
                              variation_family_asins=["B000000001", "B000000002"])
    assert result["identity_status"] == "VARIATION_RELATED"
