"""Compact DOM excerpts of the genuine P0 landing pages; no live requests."""
from pathlib import Path

import pytest

from amazon_es_bestseller.multisource.adapters import get_adapter
from amazon_es_bestseller.multisource.contracts import SOURCE_ROOTS
from amazon_es_bestseller.multisource.sources import discover_sources, source_id


FIXTURES = Path(__file__).parent / "fixtures/multisource/p0_saved_roots"


def saved(kind):
    return (FIXTURES / (kind.lower() + ".html")).read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["MOVERS_SHAKERS", "MOST_WISHED_FOR", "MOST_GIFTED"])
def test_saved_root_heading_recognized_without_inventing_empty_cards(kind):
    url = SOURCE_ROOTS[kind]
    html = saved(kind)
    result = get_adapter(kind).parse(html,
        source={"source_id": source_id(kind, url), "source_type": kind,
                "actual_source_url": url}, run_id="p0-fixture", snapshot_id="p0-fixture",
        page_url=url, page_number=1, observed_at="2026-10-13T05:50:00Z", evidence_path="saved-root.html")
    assert result["page"]["source_type_verified"] is True
    assert result["page"]["status"] == "FAILED"
    assert "PAGE_CONTENT_MISSING" in result["page"]["flags"]
    assert result["observations"] == []
    assert result["page"]["page_complete"] is False


@pytest.mark.parametrize("kind", ["MOST_WISHED_FOR", "MOST_GIFTED"])
def test_saved_hashed_navigation_keeps_real_child_links_and_rejects_shortcut(kind):
    rows = discover_sources(saved(kind), kind, SOURCE_ROOTS[kind])
    assert len(rows) == 29
    assert any(row["actual_source_url"] == SOURCE_ROOTS[kind] + "/grocery" for row in rows)
    assert all(row["actual_source_url"] != SOURCE_ROOTS[kind] for row in rows)
    assert all(row["research_category"] is None and row["source_status"] == "UNVERIFIED" for row in rows)
    assert all(row["amazon_category"] != "Contenido principal" for row in rows)


def test_saved_movers_root_has_no_category_navigation_evidence():
    assert discover_sources(saved("MOVERS_SHAKERS"), "MOVERS_SHAKERS",
                            SOURCE_ROOTS["MOVERS_SHAKERS"]) == []


def test_unrelated_secondary_heading_cannot_override_visible_list_header():
    html = '<div id="zg"><h1 id="zg_banner_text">Los más vendidos</h1></div><h1>Los más regalados</h1>'
    assert not get_adapter("MOST_GIFTED").recognize(html, SOURCE_ROOTS["MOST_GIFTED"])["recognized"]


def test_scoped_landing_list_header_overrules_stale_banner_and_document_title():
    html = saved("MOST_GIFTED").replace("Los más regalados</h1>", "Los más vendidos</h1>")
    assert not get_adapter("MOST_GIFTED").recognize(html, SOURCE_ROOTS["MOST_GIFTED"])["recognized"]
