from __future__ import annotations

import copy

import pytest

from amazon_es_bestseller.production.spanish_master import MasterPromotionError, build_spanish_master, verify_artifact_hash
from amazon_es_bestseller.quality.source_fields import audit_source_fields
from amazon_es_bestseller.quality.source_gate import evaluate_source_gate


ASIN = "B012345678"


def source(**extra):
    row = {
        "asin": ASIN,
        "requested_asin": ASIN,
        "ranking_asin": ASIN,
        "detail_asin": ASIN,
        "final_asin": ASIN,
        "product_url": f"https://www.amazon.es/dp/{ASIN}",
        "current_price": "12,00",
        "rating": "4,5",
        "title_es_raw": "Botella de acero 500 ml",
        "ranking_contexts": [{"ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/42",
                              "ranking_page_number": 1, "bestseller_rank": 2,
                              "leaf_category": "Botellas", "browse_node_id": "42"}],
        "bestseller_rank": 2,
        "detail_schema_version": 2,
        "ranking_schema_version": "ranking-v1",
        "parser_version": "fixture-v1",
        "collected_at": "2026-10-05T12:00:00Z",
        "observed_at": "2026-10-05T12:01:00Z",
    }
    row.update(extra)
    return row


def promoted(records, **kwargs):
    audit = audit_source_fields(records)
    return build_spanish_master(records, audit, evaluate_source_gate(audit), run_id="fixture-run", **kwargs)


def test_master_is_one_asin_with_independent_ranking_contexts_and_stable_hash():
    second = source(ranking_contexts=[{"ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/99",
                                       "ranking_page_number": 2, "bestseller_rank": 52,
                                       "leaf_category": "Deporte", "browse_node_id": "99"}])
    artifact = promoted([source(), second])
    assert len(artifact["records"]) == 1
    assert len(artifact["records"][0]["ranking_contexts"]) == 2
    assert artifact["records"][0]["run_id"] == "fixture-run"
    assert verify_artifact_hash(artifact)
    assert artifact["artifact_hash"] == promoted([source(), second])["artifact_hash"]


def test_master_preserves_notes_and_raw_source_is_immutable_across_refresh():
    prior = promoted([source(notes="人工备注")])
    refreshed = promoted([source(title_es_raw="Botella de acero 500 ml NUEVA")], existing_master=prior)
    record = refreshed["records"][0]
    assert record["notes"] == "人工备注"
    assert record["raw_source"]["title_es_raw"] == "Botella de acero 500 ml"
    assert record["observed_source"]["title_es_raw"] == "Botella de acero 500 ml NUEVA"


def test_master_rejects_forged_gate_and_blocked_record():
    audit = audit_source_fields([source(product_url="http://www.amazon.es/dp/B012345678")])
    with pytest.raises(MasterPromotionError):
        build_spanish_master([source(product_url="http://www.amazon.es/dp/B012345678")], audit,
                             {"status": "SOURCE_READY", "audit_hash": "forged"})
    with pytest.raises(MasterPromotionError):
        build_spanish_master([source(product_url="http://www.amazon.es/dp/B012345678")], audit,
                             evaluate_source_gate(audit))


def test_master_does_not_mutate_caller_records():
    records = [source()]
    before = copy.deepcopy(records)
    promoted(records)
    assert records == before
