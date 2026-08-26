# -*- coding: utf-8 -*-
"""collection/ranking.py 测试：显式徽章 → 排名；DOM 顺序 → index。"""
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page

SRC = "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs"
T = "2026-08-26T00:00:00Z"


def test_parse_three_rows(bestsellers_grid_html):
    records = parse_bestsellers_page(bestsellers_grid_html, SRC, T)
    assert len(records) == 3
    assert records[0] == {
        "index": 0,
        "asin": "B078C6QR1C",
        "bestseller_rank": 1,
        "ranking_source_url": SRC,
        "collected_at": T,
    }
    assert records[1]["bestseller_rank"] == 2
    assert records[2]["asin"] == "B07RN64P2R"
    assert records[2]["bestseller_rank"] == 3


def test_no_badge_rank_none_but_index_recorded():
    # QA_RULES §11：无徽章 → bestseller_rank=None，index 仍为 DOM 序
    html = """
    <html><body>
      <div id="gridItemRoot">
        <a href="/dp/B078C6QR1C"><span>Producto sin badge</span></a>
      </div>
      <div id="gridItemRoot">
        <span class="a-badge-text">#5</span>
        <a href="/dp/B075JJRFVV"><span>Otro</span></a>
      </div>
    </body></html>
    """
    records = parse_bestsellers_page(html, SRC, T)
    assert records[0]["bestseller_rank"] is None
    assert records[0]["index"] == 0
    assert records[1]["bestseller_rank"] == 5
    assert records[1]["index"] == 1


def test_missing_dp_link_skipped():
    html = """
    <html><body>
      <div id="gridItemRoot"><span class="a-badge-text">#1</span><span>Sin link</span></div>
      <div id="gridItemRoot"><a href="/dp/B078C6QR1C"><span>OK</span></a></div>
    </body></html>
    """
    records = parse_bestsellers_page(html, SRC, T)
    assert len(records) == 1
    assert records[0]["asin"] == "B078C6QR1C"


def test_asin_uppercased():
    html = """
    <html><body>
      <div id="gridItemRoot">
        <span class="a-badge-text">#9</span>
        <a href="/dp/b078c6qr1c"><span>lower</span></a>
      </div>
    </body></html>
    """
    records = parse_bestsellers_page(html, SRC, T)
    assert records[0]["asin"] == "B078C6QR1C"
    assert records[0]["bestseller_rank"] == 9
