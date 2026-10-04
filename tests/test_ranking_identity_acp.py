from amazon_es_bestseller.monitoring.ranking_identity.extract import extract_identity_from_html


def test_acp_supplement_completes_server_shortfall():
    html = "".join(
        f"<div id='gridItemRoot' data-asin='B{index:09d}'></div>" for index in range(30)
    )
    acp = [{"asin": f"B{index:09d}", "url": f"/dp/B{index:09d}"} for index in range(30, 50)]
    result = extract_identity_from_html(html, source_url="https://www.amazon.es/gp/bestsellers/tools",
                                        acp_response=acp, expected_count=50)
    assert result["audit"]["server_rendered_count"] == 30
    assert result["audit"]["acp_identity_count"] == 20
    assert result["audit"]["unique_asin_count"] == 50
    assert result["audit"]["identity_complete"] is True


def test_failed_acp_leaves_partial_identity():
    html = "".join(
        f"<div id='gridItemRoot' data-asin='B{index:09d}'></div>" for index in range(30)
    )
    result = extract_identity_from_html(html, source_url="https://www.amazon.es/gp/bestsellers/tools",
                                        expected_count=50)
    assert result["audit"]["identity_ready"] is True
    assert result["audit"]["identity_complete"] is False
    assert result["audit"]["status"] == "IDENTITY_PARTIAL"
