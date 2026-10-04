from amazon_es_bestseller.monitoring.ranking_identity.adapters import extract_server_candidates


def test_multi_layout_selectors_and_href_fallbacks():
    html = """
    <main><div class='zg-grid-general-faceout'><a href='/x/dp/B078C6QR1C'>one</a></div>
    <div id='p13n-asin-index-1' data-asin='B075JJRFVV'><span class='zg-bdg-text'>#2</span></div>
    </main>
    """
    rows = extract_server_candidates(html, source_url="https://www.amazon.es/gp/bestsellers/kitchen")
    assert [row["asin"] for row in rows] == ["B078C6QR1C", "B075JJRFVV"]
    assert rows[0]["asin_source"] == "PRODUCT_HREF_ASIN"


def test_data_asin_fallback_is_limited_to_ranking_context():
    html = "<main><div class='bestsellers'><div class='card' data-asin='B078C6QR1C'><span class='zg-bdg-text'>#1</span></div></div></main>"
    assert extract_server_candidates(html, source_url="https://www.amazon.es/gp/bestsellers/kitchen")[0]["asin"] == "B078C6QR1C"


def test_generic_main_data_asin_recommendation_is_not_a_ranking_identity():
    html = """
    <main>
      <div class='bestsellers'><div class='card' data-asin='B078C6QR1C'><span class='zg-bdg-text'>#1</span>rank</div></div>
      <div class='recommendation' data-asin='B000000001'>recommendation</div>
      <div class='sponsored' data-asin='B000000002'>sponsored</div>
    </main>
    """
    rows = extract_server_candidates(html, source_url="https://www.amazon.es/gp/bestsellers/kitchen")
    assert [row["asin"] for row in rows] == ["B078C6QR1C"]
