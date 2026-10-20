from copy import deepcopy

from amazon_es_bestseller.handoff import (
    CandidateObservation,
    adapt_ranking_candidates,
)


def test_candidate_handoff_preserves_ranking_evidence_without_confirming_sku():
    record = {
        "asin": "b078c6qr1c",
        "ranking_asin": "b078c6qr1c",
        "ranking_product_url_raw": "/gp/product/B078C6QR1C?ref=榜单",
        "ranking_product_url_normalized": "https://www.amazon.es/dp/B078C6QR1C",
        "ranking_source_type": "BEST_SELLERS",
        "ranking_source_url": "https://www.amazon.es/gp/bestsellers/hogar",
        "ranking_page_number": 2,
        "ranking_rank": 51,
        "ranking_rank_raw": "n.º 51",
        "ranking_link_identity_status": "MATCH",
        "snapshot_id": "snapshot-20261020",
        "observed_at": "2026-10-20T10:00:00Z",
        "evidence_file": "pages/page-2.html",
        "rating_raw": "4,5 de 5 estrellas",
        "review_count_raw": "1.234",
        "future_raw_field": {"kept": True},
    }
    original = deepcopy(record)

    candidate = CandidateObservation.from_ranking_record(record)
    handoff = candidate.to_dict()

    assert record == original
    assert handoff["future_raw_field"] == {"kept": True}
    assert handoff["candidate_original_product_url"] == record["ranking_product_url_raw"]
    assert handoff["candidate_normalized_product_url"] == record["ranking_product_url_normalized"]
    assert handoff["candidate_source_type"] == "BEST_SELLERS"
    assert handoff["candidate_ranking_position"] == 51
    assert handoff["candidate_asin_observed"] == "B078C6QR1C"
    assert handoff["candidate_link_identity_status"] == "MATCH"
    assert handoff["candidate_identity_status"] == "UNCONFIRMED"
    assert handoff["candidate_id"] != handoff["candidate_asin_observed"]


def test_candidate_observation_key_keeps_cross_source_memberships_separate():
    base = {
        "asin": "B078C6QR1C",
        "ranking_product_url_raw": "/dp/B078C6QR1C",
        "ranking_product_url_normalized": "https://www.amazon.es/dp/B078C6QR1C",
        "ranking_page_number": 1,
        "ranking_rank": 4,
        "snapshot_id": "snapshot-1",
    }
    first = {**base, "ranking_source_url": "https://www.amazon.es/gp/bestsellers/a"}
    second = {**base, "ranking_source_url": "https://www.amazon.es/gp/bestsellers/b"}

    adapted = adapt_ranking_candidates([first, second])

    assert len(adapted) == 2
    assert adapted[0]["candidate_id"] != adapted[1]["candidate_id"]
    assert [row["candidate_source_page_url"] for row in adapted] == [
        first["ranking_source_url"], second["ranking_source_url"]
    ]
    assert [row["candidate_asin_observed"] for row in adapted] == [
        "B078C6QR1C", "B078C6QR1C"
    ]


def test_candidate_observation_key_is_repeatable_for_same_snapshot_slot():
    record = {
        "snapshot_id": "snapshot-1",
        "ranking_source_url": "https://www.amazon.es/gp/bestsellers/a",
        "ranking_page_number": 1,
        "index": 3,
        "ranking_product_url_raw": "/dp/B078C6QR1C",
    }

    first = CandidateObservation.from_ranking_record(record)
    second = CandidateObservation.from_ranking_record(dict(record))

    assert first.candidate_id == second.candidate_id
    assert first.candidate_id.startswith("candidate_")
