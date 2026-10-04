from amazon_es_bestseller.monitoring.ranking_identity.completeness import audit_identity_records


def _row(asin, url=True):
    return {"asin": asin, "product_url": f"https://www.amazon.es/dp/{asin}" if url else None,
            "identity_status": "CONFIRMED" if url else "IDENTITY_CONFLICT",
            "product_url_source": "RAW_HREF_CONFIRMED" if url else None}


def test_duplicate_candidates_are_counted_separately_from_unique_identity():
    records = [_row(f"B{index:09d}") for index in range(50)]
    raw = [{"asin": row["asin"]} for row in records] + [{"asin": "B000000000"}, {"asin": "B000000001"}]
    audit = audit_identity_records(records, raw, expected_count=50)
    assert audit["raw_candidate_count"] == 52
    assert audit["unique_asin_count"] == 50
    assert audit["duplicate_asin_count"] == 2
    assert audit["identity_complete"] is True


def test_conflict_blocks_ready_gate():
    audit = audit_identity_records([_row("B078C6QR1C", url=False)], [{"asin": "B078C6QR1C"}])
    assert audit["identity_ready"] is False
    assert audit["status"] == "IDENTITY_BLOCKED"
