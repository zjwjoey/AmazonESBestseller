from amazon_es_bestseller.collection.ranking_v2 import parse_ranking_snapshot_v2


def _card(asin, rank):
    return (f'<div id="gridItemRoot"><span class="a-badge-text">#{rank}</span>'
            f'<a href="/dp/{asin}"><span>Item {rank}</span></a></div>')


def test_server_30_acp_20_reaches_expected_50():
    server = ''.join(_card(f"B{rank:09d}", rank) for rank in range(1, 31))
    entries = ''.join(f'{{"id":"B{rank:09d}"}},' for rank in range(1, 51)).rstrip(',')
    html = (f"<div data-client-recs-list='[{entries}]' data-acp-path=\"/acp/\">"
            '</div>' + server)

    def hydrate(_metadata, offset, count):
        assert (offset, count) == (30, 20)
        return [{"asin": f"B{rank:09d}", "bestseller_rank": rank}
                for rank in range(31, 51)]

    result = parse_ranking_snapshot_v2(html, "https://www.amazon.es/test", "2026-10-04T00:00:00Z",
                                       acp_hydrator=hydrate)
    assert result["audit"]["server_rendered_count"] == 30
    assert result["audit"]["expected_count"] == 50
    assert result["audit"]["acp_hydrated_count"] == 20
    assert result["audit"]["unique_asin_count"] == 50
    assert result["audit"]["page_authoritative"] is True


def test_server_30_acp_failure_stays_incomplete():
    server = ''.join(_card(f"B{rank:09d}", rank) for rank in range(1, 31))
    entries = ''.join(f'{{"id":"B{rank:09d}"}},' for rank in range(1, 51)).rstrip(',')
    html = f"<div data-client-recs-list='[{entries}]' data-acp-path=\"/acp/\"></div>{server}"
    result = parse_ranking_snapshot_v2(html, "https://www.amazon.es/test", "2026-10-04T00:00:00Z",
                                       acp_hydrator=lambda *_: [])
    assert result["audit"]["expected_count"] == 50
    assert result["audit"]["unique_asin_count"] == 30
    assert result["audit"]["page_authoritative"] is False
