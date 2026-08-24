from pathlib import Path

from amazon_es_bestseller.models import RankingRecord
from amazon_es_bestseller.product_card_parser import build_products, parse_product_cards


def kitchen_html() -> str:
    return Path("tests/fixtures/kitchen_sample.html").read_text(encoding="utf-8")


def test_parser_uses_product_url_as_asin_source():
    record = parse_product_cards(kitchen_html(), "https://www.amazon.es/gp/bestsellers/kitchen")[0]
    assert record.asin == "B012345678"
    assert record.asin_source == "product_url"
    assert record.rank == 1
    assert record.rank_source == "visible_text"


def test_product_aggregation_keeps_multiple_ranking_records():
    records = [
        RankingRecord(asin="B012345678", rank=8, collected_at="2026-08-24T10:00:00Z"),
        RankingRecord(asin="B012345678", rank=2, collected_at="2026-08-24T11:00:00Z"),
    ]
    products = build_products(records)
    assert products[0].ranking_count == 2
    assert products[0].best_rank == 2


def test_parser_handles_observed_amazon_card_semantic_prefixes():
    html = """
    <div data-asin="B012345678">
      <span class="zg-bdg-text">#1</span>
      <a href="/sample/dp/B012345678"><div class="_cDEzb_p13n-sc-css-line-clamp-3_g3dy1">Título real</div></a>
      <span class="_cDEzb_p13n-sc-price_3mJ9Z">10,95&nbsp;€</span>
      <div class="a-icon-row"><span class="a-icon-alt">4,6 de 5 estrellas</span><span>8.170</span></div>
    </div>
    """
    record = parse_product_cards(html, "https://www.amazon.es/gp/bestsellers/kitchen")[0]
    assert record.title == "Título real"
    assert record.price == "10,95 €"
    assert record.review_count == "8.170"
    assert record.rank_source == "visible_text"
