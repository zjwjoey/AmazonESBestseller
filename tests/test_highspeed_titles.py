"""Saved real-card titles must never be confused with prices or star labels."""
from pathlib import Path
import pytest

from amazon_es_bestseller.highspeed.adapters import parse_page


def parse(html, **kwargs):
    url = "https://www.amazon.es/gp/bestsellers"
    return parse_page(html, source={"source_id": "s", "source_type": "BEST_SELLERS", "actual_source_url": url},
        run_id="r", page_url=url, page_number=1, observed_at="2026-10-14T00:00:00Z", evidence_path="saved.html", **kwargs)


def test_real_card_visible_full_title_beats_price_and_truncated_image_alt():
    fixture = Path(__file__).parent / "fixtures" / "highspeed" / "real_price_title_collision.html"
    rows = parse(fixture.read_text(encoding="utf-8"))["observations"]
    assert len(rows) == 5
    assert rows[1]["product_title_raw"] == "GUJOIN Mochila Tática 50 Litros con Sistema Molle - Gran Capacidad Militar Impermeable para Emergencias de 3 Días Caza Senderismo y Acampadas - Mochila para Actividades al Aire Libre (Negro)"
    assert rows[1]["ranking_position"] == 2
    assert "ref=zg_bs_g_2928471031_d_sccl_2/524-6514838-9761305" in rows[1]["raw_product_url"]
    assert all("€" not in r["product_title_raw"] for r in rows)


def test_acp_fragment_uses_same_visible_title_priority():
    card = '<div class="ranking-card"><span class="zg-bdg-text">#2</span><a class="a-link-normal" href="/product/token"><span class="a-size-base a-color-price">27,19 €</span><div class="_cDEzb_p13n-sc-css-line-clamp-3_g3dy1">Full visible Spanish title</div><img alt="Truncated title"></a></div>'
    result = parse('<h1>Los más vendidos</h1><div id="zg"></div>', acp_responses=[{"body": card, "evidence_path": "actual-acp.html"}])
    assert result["observations"][0]["product_title_raw"] == "Full visible Spanish title"


@pytest.mark.parametrize("label", ["27,19 €", "€ 10.00", "4,6 de 5 estrellas", "4.5 out of 5 stars", "1 oferta desde 30,81 €", "2.021 calificaciones"])
def test_price_or_rating_only_cards_have_null_title(label):
    card = '<h1>Los más vendidos</h1><div id="zg"><div class="ranking-card"><span class="zg-bdg-text">#1</span><a class="a-link-normal" href="/product/token"><span class="a-size-base">' + label + '</span></a></div></div>'
    row = parse(card)["observations"][0]
    assert row["product_title_raw"] is None
    assert row["candidate_id"] and row["ranking_position"] == 1


def test_price_dom_ignored_then_image_alt_fallback_used():
    card = '<h1>Los más vendidos</h1><div id="zg"><div class="ranking-card"><span class="zg-bdg-text">#1</span><a class="a-link-normal" href="/product/token"><span class="a-size-base a-color-price">27,19 €</span><img alt="Literal image title"></a></div></div>'
    assert parse(card)["observations"][0]["product_title_raw"] == "Literal image title"


def test_product_title_containing_currency_or_number_is_not_price_label():
    card = '<h1>Los más vendidos</h1><div id="zg"><div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/product/token"><span data-product-title>Moneda de 2 € con caja</span></a></div></div>'
    assert parse(card)["observations"][0]["product_title_raw"] == "Moneda de 2 € con caja"
