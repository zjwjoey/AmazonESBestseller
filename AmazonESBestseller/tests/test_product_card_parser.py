from pathlib import Path

from amazon_es_bestseller.models import RankingRecord
from amazon_es_bestseller.detail_parser import ProductDetail
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


def test_parser_fallback_accepts_anchor_only_product_cards():
    html = '<a href="/sample/dp/B012345678">Anchor-only product</a>'

    records = parse_product_cards(html, "https://www.amazon.es/gp/bestsellers/kitchen")

    assert len(records) == 1
    assert records[0].asin == "B012345678"


def test_parser_does_not_convert_unhandled_spanish_magnitude():
    html = """
    <div data-asin="B012345678">
      <span class="rank">#1</span>
      <a href="/sample/dp/B012345678">Producto</a>
      <span>1 mil+ comprados el mes pasado</span>
    </div>
    """

    record = parse_product_cards(html, "https://www.amazon.es/gp/bestsellers/kitchen")[0]

    assert record.monthly_bought_text == "1 mil+ comprados el mes pasado"
    assert record.monthly_bought_value == 1000


def test_parser_reports_presence_of_additional_sales_hints():
    html = """
    <div data-asin="B012345678" data-component-type="s-sponsored-label">
      <span class="a-icon-prime">Prime</span>
      <span class="savingsPercentage">-20%</span>
      <span class="a-text-price">25,00 €</span>
      <span class="coupon">Cupón 5%</span>
      <span class="deal">Oferta del día</span>
      <span class="availability">En stock</span>
      <span class="a-badge-text">Más vendido</span>
      <a href="/sample/dp/B012345678">Producto</a>
    </div>
    """

    record = parse_product_cards(html, "https://www.amazon.es/gp/bestsellers/kitchen")[0]

    assert record.prime == "Prime"
    assert record.discount == "-20%"
    assert record.original_price == 25.0
    assert record.coupon == "Cupón 5%"
    assert record.deal == "Oferta del día"
    assert record.availability == "En stock"
    assert record.sponsored == "true"
    assert record.badge == "Más vendido"


def test_parser_deduplicates_nested_card_candidates():
    html = """
    <div data-testid="product-card">
      <div data-asin="B012345678">
        <span class="rank">#1</span>
        <a href="/dp/B012345678">Producto</a>
      </div>
    </div>
    """

    records = parse_product_cards(html, "https://www.amazon.es/gp/bestsellers/kitchen")

    assert len(records) == 1


def test_parser_deduplicates_non_nested_candidates_with_same_ranking_identity():
    html = """
    <div data-asin="B012345678"><span class="rank">#1</span><a href="/dp/B012345678">Producto</a></div>
    <div data-testid="product-card"><span class="rank">#1</span><a href="/dp/B012345678">Producto</a></div>
    """

    records = parse_product_cards(html, "https://www.amazon.es/gp/bestsellers/kitchen")

    assert len(records) == 1


def test_parser_emits_canonical_prices_discount_monthly_lower_bound_and_leaf_index():
    html = """
    <div data-asin="B012345678">
      <span class="rank">#1</span>
      <a href="/sample/dp/B012345678?ref=tracking">Producto</a>
      <span class="a-text-price">19,99 €</span>
      <span class="a-price"><span class="a-offscreen">14,99 €</span></span>
      <span>1 mil+ comprados el mes pasado</span>
    </div>
    """

    record = parse_product_cards(
        html,
        "https://www.amazon.es/gp/bestsellers/kitchen/123",
        {"leaf_category": "Baño", "ranking_source_url": "https://www.amazon.es/gp/bestsellers/kitchen/123"},
    )[0]

    assert record.index == 1
    assert record.category_rank == 1
    assert record.category_l1 == "Hogar y cocina"
    assert record.leaf_category == "Baño"
    assert record.product_url == "https://www.amazon.es/dp/B012345678"
    assert record.original_price == 19.99
    assert record.current_price == 14.99
    assert record.discount_rate == 25.01
    assert record.monthly_bought_raw == "1 mil+ comprados el mes pasado"
    assert record.monthly_bought_min == 1000


def test_build_products_merges_saved_detail_fields_by_asin():
    records = [
        RankingRecord(
            asin="B012345678",
            title="Ranking title",
            current_price=12.99,
            original_price=19.99,
            discount_rate=35.02,
            product_url="https://www.amazon.es/dp/B012345678",
        )
    ]
    details = {
        "B012345678": ProductDetail(
            asin="B012345678",
            parent_asin="B099999999",
            details_json={"brand": "Casa"},
            details="brand: Casa",
            specification="Color: Azul",
            date_first_available="2024-01-02",
            date_first_available_raw="2 de enero de 2024",
            candidate_fields={"seller": "Amazon"},
        )
    }

    products = build_products(records, details)

    assert products[0].parent_asin == "B099999999"
    assert products[0].current_price == 12.99
    assert products[0].details_json == '{"brand": "Casa"}'
    assert products[0].date_first_available == "2024-01-02"


def test_build_products_backfills_brand_and_marks_self_reported_parent_asin():
    record = RankingRecord(asin="B012345678", title="Ranking title")
    detail = ProductDetail(
        asin="B012345678",
        parent_asin="B012345678",
        details_json={"brand": "Casa"},
        details="brand: Casa",
        specification=None,
        date_first_available=None,
        date_first_available_raw=None,
        candidate_fields={},
    )

    product = build_products([record], {record.asin: detail})[0]

    assert product.brand == "Casa"
    assert product.parent_asin == "B012345678"
    assert product.parent_asin_status == "self_reported_unconfirmed"
