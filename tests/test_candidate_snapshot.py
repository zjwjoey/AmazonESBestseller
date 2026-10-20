import hashlib
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.candidate_snapshot import (
    build_candidate_snapshot,
    parse_saved_candidate_pages,
)
from amazon_es_bestseller.cli import main


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "multisource_candidates"


def _page(source_type, filename, url, **extra):
    path = FIXTURE_ROOT / filename
    return {
        "source_type": source_type,
        "source_url": url,
        "page_url": url,
        "page_number": 1,
        "html_path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        **extra,
    }


def _pages():
    return [
        _page("BEST_SELLERS", "F01_best_root_complete.html",
              "https://www.amazon.es/gp/bestsellers"),
        _page("NEW_RELEASES", "F02_new_root_complete.html",
              "https://www.amazon.es/gp/new-releases"),
        _page("CUSTOMER_FAVORITES", "F07_favorites_global_complete.html",
              "https://www.amazon.es/gp/browse.html?node=205103589031&ref_=nav_em_nav_CML_0_2_24_1",
              source_subtype="CUSTOMERS_MOST_LOVED_CURATED_ZONE"),
    ]


def test_saved_multisource_pages_produce_deterministic_unverified_candidate_snapshot(tmp_path):
    pages = _pages()
    first = build_candidate_snapshot(pages, tmp_path / "one", snapshot_id="offline-v4",
                                    observed_at="2026-10-20T10:00:00Z", target_candidates=30000)
    second = parse_saved_candidate_pages(pages, snapshot_id="offline-v4",
                                         observed_at="2026-10-20T10:00:00Z")

    manifest = first["manifest"]
    assert manifest["snapshot_status"] == "UNVERIFIED"
    assert manifest["source_complete"] is False
    assert manifest["ready_for_detail_plan"] is False
    assert manifest["target_candidates"] == 30000
    assert manifest["target_shortfall"] == 30000 - manifest["candidate_count"]
    assert [row["candidate_id"] for row in first["candidates"]] == [
        row["candidate_id"] for row in second["candidates"]
    ]
    assert {row["source_type"] for row in manifest["source_statuses"]} == {
        "BEST_SELLERS", "NEW_RELEASES", "CUSTOMER_FAVORITES"
    }
    assert all(row["source_completeness_status"] == "UNVERIFIED"
               for row in manifest["source_statuses"])
    assert all(candidate["strict_buyable_confirmed"] is False
               and candidate["candidate_identity_status"] == "UNCONFIRMED"
               for candidate in first["candidates"])

    candidate_file = first["path"] / "candidates.json"
    saved = json.loads(candidate_file.read_text(encoding="utf-8"))
    assert saved == first["candidates"]
    assert manifest["candidate_file_sha256"] == hashlib.sha256(candidate_file.read_bytes()).hexdigest()
    assert all((first["path"] / row["name"]).is_file() for row in manifest["files"])


def test_new_release_rank_is_not_written_as_bestseller_rank():
    parsed = parse_saved_candidate_pages(
        [_pages()[1]], snapshot_id="new-release", observed_at="2026-10-20T10:00:00Z"
    )

    observations = [row for candidate in parsed["candidates"] for row in candidate["observations"]]
    assert observations
    assert all(row["ranking_semantics"] == "NEW_RELEASE_LIST_POSITION" for row in observations)
    assert all(row["bestseller_rank"] is None for row in observations)


def test_same_normalized_link_groups_observations_and_keeps_both_sources(tmp_path):
    card = (
        '<div id="gridItemRoot" data-asin="B000000001">'
        '<span class="zg-bdg-text">#1</span>'
        '<a href="/dp/B000000001?ref=source-card"><span>Product title</span></a>'
        '<span class="a-icon-alt">4,5 de 5 estrellas</span>'
        '</div>'
    )
    pages = []
    for source_type, heading, url in (
        ("BEST_SELLERS", "Los más vendidos", "https://www.amazon.es/gp/bestsellers"),
        ("NEW_RELEASES", "Últimas novedades", "https://www.amazon.es/gp/new-releases"),
    ):
        source_file = tmp_path / (source_type + ".html")
        source_file.write_text("<h1>%s</h1>%s" % (heading, card), encoding="utf-8")
        body = source_file.read_bytes()
        pages.append({
            "source_type": source_type,
            "source_url": url,
            "page_url": url,
            "html_path": str(source_file),
            "sha256": hashlib.sha256(body).hexdigest(),
        })

    result = parse_saved_candidate_pages(
        pages, snapshot_id="cross-source", observed_at="2026-10-20T10:00:00Z"
    )

    assert result["candidate_count"] == 1
    assert result["observation_count"] == 2
    candidate = result["candidates"][0]
    assert candidate["strict_buyable_confirmed"] is False
    assert {member["candidate_source_type"] for member in candidate["source_memberships"]} == {
        "BEST_SELLERS", "NEW_RELEASES"
    }
    assert {row["ranking_semantics"] for row in candidate["observations"]} == {
        "BEST_SELLER_LIST_POSITION", "NEW_RELEASE_LIST_POSITION"
    }


def test_restricted_saved_page_is_blocked_and_never_complete(tmp_path):
    challenge = Path(__file__).parent / "fixtures" / "html" / "product_captcha.html"
    page = {
        "source_type": "BEST_SELLERS",
        "source_url": "https://www.amazon.es/gp/bestsellers",
        "page_url": "https://www.amazon.es/gp/bestsellers",
        "html_path": str(challenge),
        "sha256": hashlib.sha256(challenge.read_bytes()).hexdigest(),
    }

    parsed = parse_saved_candidate_pages(
        [page], snapshot_id="challenge", observed_at="2026-10-20T10:00:00Z"
    )

    assert parsed["snapshot_status"] == "BLOCKED"
    assert parsed["observation_count"] == 0
    assert parsed["source_statuses"][0]["source_completeness_status"] == "BLOCKED"


def test_source_type_cannot_be_attached_to_an_unrelated_or_foreign_url():
    page = _pages()[0]
    page["page_url"] = "https://example.com/gp/bestsellers"
    with pytest.raises(ValueError, match="AMAZON_ES_SOURCE_URL_REQUIRED"):
        parse_saved_candidate_pages(
            [page], snapshot_id="foreign", observed_at="2026-10-20T10:00:00Z"
        )

    page["page_url"] = "https://www.amazon.es/gp/new-releases"
    with pytest.raises(ValueError, match="SOURCE_URL_TYPE_MISMATCH"):
        parse_saved_candidate_pages(
            [page], snapshot_id="wrong-source", observed_at="2026-10-20T10:00:00Z"
        )


def test_candidate_snapshot_cli_is_saved_html_only(tmp_path):
    manifest = FIXTURE_ROOT / "manifest.json"

    result = main([
        "--offline", "candidate-snapshot", "--manifest", str(manifest),
        "--out-dir", str(tmp_path / "cli-output"),
    ])

    saved_manifest = tmp_path / "cli-output" / "candidate_snapshots" / \
        "offline-v4-core-sources" / "manifest.json"
    assert result == 0
    assert saved_manifest.is_file()
    data = json.loads(saved_manifest.read_text(encoding="utf-8"))
    assert data["snapshot_mode"] == "SAVED_HTML_REPLAY"
    assert data["candidate_count"] == 235
    assert data["ready_for_detail_plan"] is False
