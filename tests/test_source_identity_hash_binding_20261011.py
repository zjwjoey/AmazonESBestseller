"""A normalized-text digest must not silently discard strong page identity."""
from copy import deepcopy

import pytest

from amazon_es_bestseller.quality.source_fields import audit_source_fields


def source_row():
    return {"asin": "B000000001", "product_url": "https://www.amazon.es/dp/B000000001",
            "title_es_raw": "Producto de prueba", "attributes": [{"label_raw": "ASIN", "value_raw": "B000000001"}],
            "offline_replay_evidence": {"html_sha256": "a" * 64},
            "detail_identity_signals": {"producer": "saved_html_identity_review", "html_sha256": "a" * 64,
                "input_asins": ["B000000001"], "current_asins": ["B000000001"],
                "canonical_asins": ["B000000001"], "parent_asins": []}}


@pytest.mark.parametrize("change", ["different_digest", "no_archived_digest"])
def test_unbound_identity_signals_are_not_counted_as_source_ready(change):
    row = source_row()
    if change == "different_digest":
        row["detail_identity_signals"]["html_sha256"] = "b" * 64
    else:
        row.pop("offline_replay_evidence")
    before = deepcopy(row)
    audit = audit_source_fields([row])
    assert any(i["issue_code"] == "IDENTITY_EVIDENCE_HASH_MISMATCH" for i in audit["issues"])
    assert audit["sku_status"][row["asin"]] != "SOURCE_READY"
    assert row == before


def test_exact_byte_bound_identity_retains_existing_behavior():
    row = source_row()
    audit = audit_source_fields([row])
    assert audit["sku_status"][row["asin"]] == "SOURCE_READY"
    assert not any(i["issue_code"] == "IDENTITY_EVIDENCE_HASH_MISMATCH" for i in audit["issues"])
