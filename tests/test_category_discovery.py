from pathlib import Path

from amazon_es_bestseller.category_discovery import discover_categories


def test_discovery_preserves_real_category_url_and_node_id():
    html = Path("tests/fixtures/kitchen_sample.html").read_text(encoding="utf-8")
    nodes = discover_categories(html, "https://www.amazon.es/gp/bestsellers/kitchen")
    assert nodes[0].category_name_es == "Baño"
    assert nodes[0].browse_node_id == "12345"
    assert nodes[0].depth == 2
