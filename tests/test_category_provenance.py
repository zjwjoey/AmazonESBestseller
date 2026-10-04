from amazon_es_bestseller.categories.provenance import (
    BSR_NODE, SEARCH_NODE, category_evidence_from_detail,
)


def test_category_sources_do_not_overwrite_each_other():
    result = category_evidence_from_detail({
        "bsr_category_id": "123", "bsr_category_path": ["Hogar", "Cocina"],
        "search_category_path": ["Búsqueda"],
    })
    assert result.bsr_category_id == "123"
    assert result.bsr_category_path == ("Hogar", "Cocina")
    assert result.category_evidence_source == BSR_NODE
    search_only = category_evidence_from_detail({"search_category_path": ["Búsqueda"]})
    assert search_only.category_evidence_source == SEARCH_NODE
