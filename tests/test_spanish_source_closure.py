import hashlib
import json

from amazon_es_bestseller.production.spanish_source_closure import (
    build_spanish_source_candidate,
    candidate_manifest_hash,
    derive_owner_excluded_scope,
    write_owner_excluded_scope,
    write_spanish_source_candidate,
)


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _audit(asins):
    return {
        "check": "source_fields",
        "status": "BLOCK",
        "summary": {"record_count": len(asins), "issue_count": 1},
        "issues": [{"asin": asins[0], "issue": "EXISTING_BLOCKER", "severity": "P1"}],
        "field_audits": [],
        "sku_status": {asin: "BLOCKED" for asin in asins},
        "record_bindings": {asin: [{"record_hash": "evidence-" + asin}] for asin in asins},
    }


def _detail(asin, **changes):
    detail = {
        "asin": asin,
        "identity_status": "MATCH",
        "identity_status_code": "IDENTITY_MATCH",
        "title_es_raw": "Producto de prueba",
        "current_price_raw": "10,00 €",
        "original_price_raw": "20,00 €",
        "product_url": f"https://www.amazon.es/dp/{asin}",
        "brand_raw": "Visita la tienda de Bylines No Son Marca",
        "attributes": [],
        "feature_bullets_raw": ["bullet evidence"],
        "detail_bsr_raw": "n.º 1 en Prueba",
        "detail_schema_version": "detail-v1",
        "detail_parser_version": "parser-v1",
    }
    detail.update(changes)
    return detail


def _ranking(asin, rank=1, **changes):
    record = {
        "asin": asin,
        "ranking_asin": asin,
        "bestseller_rank": rank,
        "bestseller_rank_raw": f"#{rank}",
        "ranking_source_url": "https://www.amazon.es/gp/bestsellers/test",
        "ranking_source_category": "Prueba",
        "ranking_page_number": 1,
        "research_category": "Hogar",
        "collection_batch": "batch-1",
        "collection_time": "2026-10-06T00:00:00",
    }
    record.update(changes)
    return record


def test_candidate_keeps_source_gate_blocked_and_ranking_contexts():
    asin = "B000000001"
    candidates = [_ranking(asin)]
    result = build_spanish_source_candidate(
        candidates,
        [_detail(asin, attributes=[{"label_raw": "Marca", "value_raw": "Marca explícita"}])],
        [_ranking(asin), _ranking(asin, rank=2, ranking_page_number=2)],
        _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash(candidates),
    )

    record = result["records"][0]
    assert result["status"] == "CANDIDATE_SOURCE_GATE_BLOCKED"
    assert result["source_gate"]["ready"] is False
    assert record["brand"] == "Marca explícita"
    assert len(record["ranking_contexts"]) == 2
    assert record["bestseller_rank"] == 1
    assert record["detail_bsr_raw"] == "n.º 1 en Prueba"
    assert record["metadata"]["collection_batch"] == "batch-1"


def test_candidate_isolates_damaged_optional_values_and_preserves_raw():
    asin = "B000000002"
    detail = _detail(
        asin,
        attributes=[
            {"label_raw": "Marca", "value_raw": "Bons�i"},
            {"label_raw": "Fabricante", "value_raw": "TulipÃ¡n negro"},
            {"label_raw": "Tipo de altavoz", "value_raw": "Port�til"},
        ],
    )
    result = build_spanish_source_candidate(
        [_ranking(asin)], [detail], [_ranking(asin)], _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash([_ranking(asin)]),
    )

    record = result["records"][0]
    assert record["brand"] == ""
    assert record["manufacturer"] == ""
    assert record["speaker_type"] == ""
    assert record["attributes"] == detail["attributes"]
    assert {item["field"] for item in result["source_review_queue"]} >= {
        "brand", "manufacturer", "speaker_type"
    }


def test_candidate_keeps_plain_brand_byline_but_rejects_editorial_format_byline():
    asin = "B000000006"
    plain = _detail(asin, brand_raw="Reliable Brand")
    result = build_spanish_source_candidate(
        [_ranking(asin)], [plain], [_ranking(asin)], _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash([_ranking(asin)]),
    )
    assert result["records"][0]["brand"] == "Reliable Brand"

    editorial = _detail(asin, brand_raw="de Someone (Autor) Formato: Tapa blanda")
    result = build_spanish_source_candidate(
        [_ranking(asin)], [editorial], [_ranking(asin)], _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash([_ranking(asin)]),
    )
    assert result["records"][0]["brand"] == ""


def test_candidate_preserves_supported_multilingual_evidence_and_clears_unproven_self_parent():
    asin = "B000000003"
    detail = _detail(
        asin,
        parent_asin=asin,
        parent_asin_status="confirmed",
        variation_evidence={"current_asin": asin, "parent_asin": asin, "family_asins": [asin, "B000000004"]},
        attributes=[
            {"label_raw": "Idioma", "value_raw": "Japonés"},
            {"label_raw": "ISBN", "value_raw": "9781234567890"},
            {"label_raw": "Nombre", "value_raw": "日本語"},
        ],
    )
    result = build_spanish_source_candidate(
        [_ranking(asin)], [detail], [_ranking(asin)], _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash([_ranking(asin)]),
    )

    record = result["records"][0]
    assert record["parent_asin"] == ""
    assert record["parent_asin_status"] == "unconfirmed"
    assert record["attributes"][-1]["value_raw"] == "日本語"
    assert any(item["classification"] == "VALID_MULTILINGUAL_EVIDENCE" for item in result["source_review_queue"])


def test_candidate_rejects_wrong_hash_or_identity_scope():
    asin = "B000000005"
    try:
        build_spanish_source_candidate([_ranking(asin)], [_detail(asin)], [_ranking(asin)], _audit([asin]), expected_candidate_hash="wrong")
    except ValueError as exc:
        assert "candidate manifest hash" in str(exc)
    else:  # pragma: no cover - makes failed validation explicit
        raise AssertionError("expected candidate hash validation")


def test_candidate_restores_frozen_ranking_categories_instead_of_detail_breadcrumb():
    asin = "B000000007"
    ranking = _ranking(
        asin,
        category_l1="Hogar", category_l2="Cocina", category_l3=None,
        leaf_category="Cocina", browse_node_id="123",
        ranking_source_category_path="Hogar > Cocina",
    )
    detail = _detail(asin, detail_category_trail=["Hogar", "Cocina", "Botes", "Botes herméticos"])
    result = build_spanish_source_candidate(
        [ranking], [detail], [ranking], _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash([ranking]),
    )

    record = result["records"][0]
    assert record["category_l3"] is None
    assert record["leaf_category"] == "Cocina"
    assert record["detail_category_trail"] == ["Hogar", "Cocina", "Botes", "Botes herméticos"]
    assert record["category_provenance"]["source"] == "ranking_context"
    assert record["category_provenance"]["ranking_source_category_path"] == "Hogar > Cocina"


def test_written_manifest_separates_dataset_and_frozen_manifest_hashes(tmp_path):
    asin = "B000000008"
    candidates = [_ranking(asin)]
    result = build_spanish_source_candidate(
        candidates, [_detail(asin)], candidates, _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash(candidates),
    )
    manifest = write_spanish_source_candidate(tmp_path / "closure", result)

    assert manifest["dataset_canonical_hash"] == _hash(result["records"])
    assert manifest["frozen_candidate_manifest_canonical_hash"] == candidate_manifest_hash(candidates)
    assert (tmp_path / "closure" / "historical_source_audit.json").is_file()
    assert "P2 findings are reported" in (tmp_path / "closure" / "audit.md").read_text(encoding="utf-8")


def test_candidate_review_queue_includes_current_closure_field_audits():
    asin = "B000000009"
    result = build_spanish_source_candidate(
        [_ranking(asin)], [_detail(asin, title_es_raw="Antes <script>x</script>")], [_ranking(asin)], _audit([asin]),
        expected_candidate_hash=candidate_manifest_hash([_ranking(asin)]),
    )
    assert any(item.get("origin") == "closure_field_audit" and item.get("field") == "title_es_raw"
               for item in result["source_review_queue"])


def test_owner_exclusion_scope_is_hash_bound_and_does_not_mutate_parent_records(tmp_path):
    records = [
        {"asin": "B07F6LYVT6", "attributes": [{"label_raw": "Funci\u00f3n especial", "value_raw": "port??til"}]},
        {"asin": "B077H1MZ35", "attributes": [{"label_raw": "Funci\u00f3n especial", "value_raw": "apagado_autom??tico"}]},
        {"asin": "B000000010", "attributes": []},
    ]
    parent_hash = _hash(records)
    scope = derive_owner_excluded_scope(records, parent_dataset_canonical_hash=parent_hash)

    assert scope["parent_scope"] == {"record_count": 3, "dataset_canonical_hash": parent_hash}
    assert scope["effective_scope"]["record_count"] == 1
    assert scope["effective_scope"]["asins"] == ["B000000010"]
    assert {item["asin"] for item in scope["owner_exclusions"]} == {"B07F6LYVT6", "B077H1MZ35"}
    assert all(item["raw_evidence_preserved"] for item in scope["owner_exclusions"])
    assert records[0]["attributes"][0]["value_raw"] == "port??til"

    manifest = write_owner_excluded_scope(tmp_path / "scope", scope)
    assert manifest["parent_scope"]["dataset_canonical_hash"] == parent_hash
    assert (tmp_path / "scope" / "owner_exclusions.json").is_file()


def test_owner_exclusion_scope_rejects_an_unbound_parent_hash():
    records = [
        {"asin": "B07F6LYVT6"}, {"asin": "B077H1MZ35"}, {"asin": "B000000010"},
    ]
    try:
        derive_owner_excluded_scope(records, parent_dataset_canonical_hash="0" * 64)
    except ValueError as exc:
        assert "parent dataset canonical hash" in str(exc)
    else:  # pragma: no cover - makes failed binding validation explicit
        raise AssertionError("expected parent hash validation")
