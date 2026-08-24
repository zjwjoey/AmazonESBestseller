from pathlib import Path

from amazon_es_bestseller.category_discovery import discover_categories


def test_discovery_preserves_real_category_url_and_node_id():
    html = Path("tests/fixtures/kitchen_sample.html").read_text(encoding="utf-8")
    nodes = discover_categories(html, "https://www.amazon.es/gp/bestsellers/kitchen")
    assert nodes[0].category_name_es == "Baño"
    assert nodes[0].browse_node_id == "12345"
    assert nodes[0].depth == 2


def test_discovery_ignores_self_and_internal_page_links():
    html = Path("tests/fixtures/kitchen_sample.html").read_text(encoding="utf-8")
    html = html.replace(
        "</body>",
        '<a href="/gp/bestsellers/kitchen">Entrega en Madrid</a>'
        '<a href="/gp/bestsellers/kitchen#skippedLink">Contenido principal</a></body>',
    )
    nodes = discover_categories(html, "https://www.amazon.es/gp/bestsellers/kitchen")
    assert [node.category_name_es for node in nodes] == ["Baño", "Almacenamiento"]


def test_discovery_extracts_browse_node_from_kitchen_path():
    html = '<a href="/gp/bestsellers/kitchen/2165211031/ref=zg_bs_nav_kitchen_1">Almacenamiento</a>'
    nodes = discover_categories(html, "https://www.amazon.es/gp/bestsellers/kitchen")
    assert nodes[0].browse_node_id == "2165211031"


def test_discovery_ignores_nested_category_links_from_root():
    html = """
    <a href="/gp/bestsellers/kitchen/2165211031">Direct</a>
    <a href="/gp/bestsellers/kitchen/2165211031/123456">Nested</a>
    """

    nodes = discover_categories(html, "https://www.amazon.es/gp/bestsellers/kitchen")

    assert [node.category_name_es for node in nodes] == ["Direct"]


def test_discovery_records_direct_children_at_depth_three():
    html = '<nav id="category-nav"><a href="/gp/bestsellers/kitchen/999?node=999">Accesorios de baño</a></nav>'

    nodes = discover_categories(
        html,
        "https://www.amazon.es/gp/bestsellers/kitchen/123?node=12345",
        parent_category="Baño",
        depth=3,
    )

    assert nodes[0].category_name_es == "Accesorios de baño"
    assert nodes[0].parent_category == "Baño"
    assert nodes[0].depth == 3


def test_discovery_deduplicates_browse_nodes_across_url_variants():
    html = """
    <nav id="category-nav">
      <a href="/gp/bestsellers/kitchen/101?node=77&ref=first">One</a>
      <a href="/gp/bestsellers/kitchen/102?node=77&ref=second">Two</a>
    </nav>
    """

    nodes = discover_categories(html, "https://www.amazon.es/gp/bestsellers/kitchen")

    assert [(node.category_name_es, node.browse_node_id) for node in nodes] == [("One", "77")]


def test_depth_three_discovery_ignores_category_links_outside_navigation():
    html = """
    <nav id="category-nav">
      <a href="/gp/bestsellers/kitchen/999?node=999">Direct child</a>
    </nav>
    <footer>
      <a href="/gp/bestsellers/kitchen/888?node=888">Footer category</a>
    </footer>
    """

    nodes = discover_categories(
        html,
        "https://www.amazon.es/gp/bestsellers/kitchen/123?node=12345",
        parent_category="Baño",
        depth=3,
    )

    assert [node.category_name_es for node in nodes] == ["Direct child"]


def test_depth_three_discovery_requires_a_confirmed_navigation_container():
    html = '<footer><a href="/gp/bestsellers/kitchen/888?node=888">Footer category</a></footer>'

    nodes = discover_categories(
        html,
        "https://www.amazon.es/gp/bestsellers/kitchen/123?node=12345",
        parent_category="Baño",
        depth=3,
    )

    assert nodes == []
