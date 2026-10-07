import hashlib
import json

from amazon_es_bestseller.production.spanish_source_closure import (
    _hash as production_hash,
    build_spanish_source_candidate,
    candidate_manifest_hash,
    build_current_source_gate_candidate,
    derive_owner_excluded_scope,
    load_builder_unresolved_decision_artifact,
    rank_matrix_diagnostics,
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


def _ready_audit(asins):
    return {
        "check": "source_fields",
        "status": "PASS",
        "summary": {"record_count": len(asins), "issue_count": 0},
        "issues": [], "field_audits": [],
        "sku_status": {asin: "SOURCE_READY" for asin in asins},
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


def _builder_artifact(parents, queue=None):
    parent_hash = _hash(parents)
    if queue is None:
        asin = parents[-1]["asin"]
        queue = [{
            "asin": asin, "field": "parent_asin", "classification": "EVIDENCE_UNAVAILABLE",
            "status": "REVIEW_REQUIRED", "reason": "unconfirmed self-parent cleared; canonical parent blank",
            "evidence_locator": {"asin": asin, "field": "parent_asin", "source": "parent_asin"},
        }]
    return {
        "artifact_domain": "spanish-source-builder-unresolved-decisions-v1",
        "parent_dataset_canonical_hash": parent_hash,
        "queue_canonical_hash": _hash(queue),
        "queue_sha256": "test-only-not-file-backed",
        "manifest_canonical_hash": "test-manifest",
        "decisions": queue,
    }


def _write_builder_files(tmp_path, queue, parent_hash):
    queue_path = tmp_path / "builder_unresolved_decisions.json"
    manifest_path = tmp_path / "builder_unresolved_decisions.manifest.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "artifact_domain": "spanish-source-builder-unresolved-decisions-v1",
        "parent_dataset_canonical_hash": parent_hash,
        "expected_queue_canonical_hash": _hash(queue),
        "expected_queue_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return queue_path, manifest_path


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


def test_iterencode_hash_is_byte_identical_to_legacy_canonical_dumps():
    value = {"z": [1, 1.5, "\u00f1", {"b": True, "a": None}], "a": {"nested": ["\u6d4b\u8bd5", 0.0]}}
    assert production_hash(value) == _hash(value)


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


def test_historical_ready_audit_cannot_promote_when_current_closure_is_review_required():
    asin = "B000000011"
    result = build_spanish_source_candidate(
        [_ranking(asin)], [_detail(asin, title_es_raw="Antes <script>x</script>")], [_ranking(asin)],
        _ready_audit([asin]), expected_candidate_hash=candidate_manifest_hash([_ranking(asin)]),
    )

    assert result["source_gate"]["ready"] is True
    assert result["closure_source_gate"]["ready"] is False
    assert result["status"] == "CANDIDATE_CLOSURE_GATE_BLOCKED"


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


def test_current_gate_rebuilds_only_owner_approved_optional_derived_details():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B08BYLMK7C", "B017WK9SSK", "B015YK51H2"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)], attributes=[], product_details_es="")
               for item in candidates]
    parents[2].update(attributes=[
        {"label_raw": "Tipo de altavoz", "value_raw": "Port\ufffdtil"},
        {"label_raw": "Tipo de altavoces", "value_raw": "Port\ufffdtil"},
        {"label_raw": "Material", "value_raw": "Pl\u00e1stico"},
    ], product_details_es="Tipo de altavoz: Port\ufffdtil\nTipo de altavoces: Port\ufffdtil\nMaterial: Pl\u00e1stico")
    parents[3].update(attributes=[
        {"label_raw": "Fabricante", "value_raw": "Tulip\ufffdn negro"},
        {"label_raw": "Color", "value_raw": "blanco"},
    ], product_details_es="Fabricante: Tulip\ufffdn negro\nColor: blanco")
    parents[4].update(attributes=[
        {"label_raw": "Marca", "value_raw": "Bons\ufffdi"},
        {"label_raw": "Altura", "value_raw": "15 cm"},
    ], product_details_es="Marca: Bons\ufffdi\nAltura: 15 cm")
    before = json.loads(json.dumps(parents, ensure_ascii=False))
    owner_scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, owner_scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        builder_decision_artifact=_builder_artifact(parents),
    )

    records = {record["asin"]: record for record in result["records"]}
    for asin, field in (("B08BYLMK7C", "speaker_type"),
                        ("B017WK9SSK", "manufacturer"),
                        ("B015YK51H2", "brand")):
        record = records[asin]
        original = next(row for row in before if row["asin"] == asin)
        assert record["attributes"] == original["attributes"]
        assert record["rawattributes_raw"] == original["attributes"]
        assert record["owner_optional_exclusion"]["field"] == field
        assert record["owner_optional_exclusion"]["parent_record_hash"] == _hash(original)
        assert record["source_record_hash"]
    assert "Port\ufffdtil" not in records["B08BYLMK7C"]["product_details_es"]
    assert "Tipo de altavoz" not in records["B08BYLMK7C"]["product_details_es"]
    assert "Material: Pl\u00e1stico" in records["B08BYLMK7C"]["product_details_es"]
    assert "Tulip\ufffdn negro" not in records["B017WK9SSK"]["product_details_es"]
    assert "Color: blanco" in records["B017WK9SSK"]["product_details_es"]
    assert "Bons\ufffdi" not in records["B015YK51H2"]["product_details_es"]
    assert "Altura: 15 cm" in records["B015YK51H2"]["product_details_es"]
    assert len(result["owner_optional_exclusion_repair_log"]) == 3
    assert parents == before


def test_current_gate_rejects_changed_raw_inputs_and_uses_current_audit_not_historical_block():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000012"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)]) for item in candidates]
    owner_scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    input_hashes = {"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)}
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, owner_scope,
        expected_input_hashes=input_hashes, historical_source_audit=_audit(asins),
        builder_decision_artifact=_builder_artifact(parents),
    )

    assert result["binding_scope"]["asins"] == ["B000000012"]
    assert result["current_source_gate"]["ready"] is True
    assert result["status"] == "CANDIDATE_CURRENT_GATE_READY"
    assert result["promotion_state"] == {"candidate": True, "reviewed_master": False, "eligible": True}
    assert result["builder_unresolved_decisions"]["reports"][0]["decision"] == "SOURCE_MISSING_P2_REPORT"
    changed = list(details)
    changed[2] = dict(changed[2], title_es_raw="changed raw source")
    try:
        build_current_source_gate_candidate(
            candidates, changed, candidates, parents, owner_scope,
            expected_input_hashes=input_hashes,
            builder_decision_artifact=_builder_artifact(parents),
        )
    except ValueError as exc:
        assert "input hash mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected raw-input binding validation")


def test_current_gate_stays_blocked_for_current_review_even_if_history_is_ready():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000013"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    details[-1] = _detail(asins[-1], title_es_raw="Antes <script>x</script>")
    parents = [dict(item, ranking_contexts=[dict(item)]) for item in candidates]
    parents[-1]["title_es_raw"] = "Antes <script>x</script>"
    owner_scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, owner_scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        historical_source_audit=_ready_audit(asins),
        builder_decision_artifact=_builder_artifact(parents),
    )

    assert result["historical_source_audit"]["status"] == "PASS"
    assert result["current_source_gate"]["ready"] is False
    assert result["status"] == "CANDIDATE_CURRENT_GATE_BLOCKED"
    assert result["promotion_state"]["eligible"] is False


def test_current_gate_inherits_hash_bound_unresolved_cjk_decision_as_review():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000015"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)], attributes=[])
               for item in candidates]
    parents[-1]["attributes"] = [{"label_raw": "Nombre", "value_raw": "日本語"}]
    scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    queue = [{
        "asin": "B000000015", "field": "attributes",
        "classification": "MULTILINGUAL_ATTRIBUTE_REVIEW", "status": "REVIEW_REQUIRED",
        "reason": "CJK attribute retained as raw evidence; source language support was not established",
        "evidence_locator": {"asin": "B000000015", "field": "attributes", "label_raw": "Nombre", "value_raw": "日本語"},
    }]
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        builder_decision_artifact=_builder_artifact(parents, queue),
    )
    assert result["current_source_gate"]["ready"] is False
    assert result["current_source_audit"]["sku_status"]["B000000015"] == "REVIEW_REQUIRED"
    assert any(item["issue_code"] == "MULTILINGUAL_ATTRIBUTE_REVIEW" for item in result["current_source_audit"]["issues"])
    assert result["builder_unresolved_decisions"]["queue_hash"] == _hash(queue)


def test_builder_artifact_rejects_missing_empty_tampered_and_deleted_cjk_queue(tmp_path):
    queue = [{
        "asin": f"B000000{index:03d}", "field": "attributes", "classification": "MULTILINGUAL_ATTRIBUTE_REVIEW",
        "status": "REVIEW_REQUIRED", "reason": "CJK requires review",
        "evidence_locator": {"asin": f"B000000{index:03d}", "field": "attributes", "label_raw": "Nombre", "value_raw": "中文"},
    } for index in range(1, 18)]
    parent_hash = "a" * 64
    queue_path, manifest_path = _write_builder_files(tmp_path, queue, parent_hash)
    artifact = load_builder_unresolved_decision_artifact(queue_path, manifest_path)
    assert artifact["queue_canonical_hash"] == _hash(queue)
    original_manifest = manifest_path.read_text(encoding="utf-8")
    bad_domain = json.loads(original_manifest)
    bad_domain["artifact_domain"] = "untrusted-domain"
    manifest_path.write_text(json.dumps(bad_domain), encoding="utf-8")
    try:
        load_builder_unresolved_decision_artifact(queue_path, manifest_path)
    except ValueError as exc:
        assert "domain" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("wrong builder artifact domain must be rejected")
    manifest_path.write_text(original_manifest, encoding="utf-8")
    empty_path = tmp_path / "empty.json"
    empty_path.write_text("[]", encoding="utf-8")
    try:
        load_builder_unresolved_decision_artifact(empty_path, manifest_path)
    except ValueError as exc:
        assert "non-empty" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty CJK queue must be rejected")
    queue_path.write_text(json.dumps(queue + [{"asin": "B000000016"}]), encoding="utf-8")
    try:
        load_builder_unresolved_decision_artifact(queue_path, manifest_path)
    except ValueError as exc:
        assert "hash" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("tampered CJK queue must be rejected")
    queue_path.unlink()
    try:
        load_builder_unresolved_decision_artifact(queue_path, manifest_path)
    except ValueError as exc:
        assert "required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deleted CJK queue must be rejected")


def test_current_gate_requires_verified_nonempty_builder_artifact():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000016"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)]) for item in candidates]
    scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    try:
        build_current_source_gate_candidate(
            candidates, details, candidates, parents, scope,
            expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        )
    except ValueError as exc:
        assert "artifact is required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("builder artifact must be required")


def test_current_gate_locator_loss_is_p1_evidence_lost_without_approved_chain():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000017"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)], attributes=[]) for item in candidates]
    scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    queue = [{
        "asin": "B000000017", "field": "attributes", "classification": "MULTILINGUAL_ATTRIBUTE_REVIEW",
        "status": "REVIEW_REQUIRED", "reason": "CJK requires review",
        "evidence_locator": {"asin": "B000000017", "field": "attributes", "label_raw": "Nombre", "value_raw": "中文"},
    }]
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        builder_decision_artifact=_builder_artifact(parents, queue),
    )
    issue = next(item for item in result["current_source_audit"]["issues"] if item["issue_code"] == "EVIDENCE_LOST")
    assert issue["severity"] == "P1"
    assert result["current_source_gate"]["ready"] is False
    assert not result["builder_unresolved_decisions"]["resolved"]
    altered_parents = json.loads(json.dumps(parents, ensure_ascii=False))
    altered_parents[-1]["attributes"] = [{"label_raw": "Nombre", "value_raw": "文中"}]
    altered_scope = derive_owner_excluded_scope(altered_parents, parent_dataset_canonical_hash=_hash(altered_parents))
    altered = build_current_source_gate_candidate(
        candidates, details, candidates, altered_parents, altered_scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        builder_decision_artifact=_builder_artifact(altered_parents, queue),
    )
    assert any(item["issue_code"] == "EVIDENCE_LOST" for item in altered["current_source_audit"]["issues"])


def test_current_gate_allows_only_hash_bound_owner_optional_repair_chain():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B08BYLMK7C"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)], attributes=[]) for item in candidates]
    parents[-1]["attributes"] = [{"label_raw": "Tipo de altavoz", "value_raw": "Port�til"}]
    scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    queue = [{
        "asin": "B08BYLMK7C", "field": "speaker_type", "classification": "EVIDENCE_UNAVAILABLE",
        "status": "REVIEW_REQUIRED", "reason": "owner-approved-optional-exclusion",
        "evidence_locator": {"asin": "B08BYLMK7C", "field": "speaker_type", "source": "detail_attributes",
                             "label_raw": "Tipo de altavoz", "value_raw": "Port�til", "position": 0},
    }]
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        builder_decision_artifact=_builder_artifact(parents, queue),
    )
    resolved = result["builder_unresolved_decisions"]["resolved"]
    assert len(resolved) == 1
    assert resolved[0]["repair_chain"]["policy"] == "owner-approved-optional-attribute-exclusion-v1"
    assert not any(item["issue_code"] == "EVIDENCE_UNAVAILABLE" for item in result["current_source_audit"]["issues"])


def test_current_gate_unknown_non_cjk_evidence_unavailable_is_p1_not_nonblocking_report():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000018"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)], attributes=[]) for item in candidates]
    scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    queue = [{
        "asin": "B000000018", "field": "manufacturer", "classification": "EVIDENCE_UNAVAILABLE",
        "status": "REVIEW_REQUIRED", "reason": "manufacturer source was not preserved",
        "evidence_locator": {"asin": "B000000018", "field": "manufacturer", "source": "detail_attributes"},
    }]
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        builder_decision_artifact=_builder_artifact(parents, queue),
    )
    assert any(item["issue_code"] == "EVIDENCE_UNAVAILABLE" and item["severity"] == "P1"
               for item in result["current_source_audit"]["issues"])
    assert not result["builder_unresolved_decisions"]["reports"]


def test_current_gate_accepts_valid_multilingual_only_with_language_and_isbn_source():
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000019"]
    candidates = [_ranking(asin) for asin in asins]
    details = [_detail(asin) for asin in asins]
    parents = [dict(item, ranking_contexts=[dict(item)], attributes=[]) for item in candidates]
    parents[-1]["attributes"] = [
        {"label_raw": "Idioma", "value_raw": "Japonés"},
        {"label_raw": "ISBN", "value_raw": "9780000000000"},
        {"label_raw": "Nombre", "value_raw": "中文"},
    ]
    scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    queue = [{
        "asin": "B000000019", "field": "attributes", "classification": "VALID_MULTILINGUAL_EVIDENCE",
        "status": "PASS", "reason": "Japanese language plus ISBN are explicit source evidence",
        "evidence_locator": {"asin": "B000000019", "field": "attributes", "label_raw": "Nombre", "value_raw": "中文"},
    }]
    result = build_current_source_gate_candidate(
        candidates, details, candidates, parents, scope,
        expected_input_hashes={"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(candidates)},
        builder_decision_artifact=_builder_artifact(parents, queue),
    )
    assert result["builder_unresolved_decisions"]["reports"][0]["decision"] == "VALID_MULTILINGUAL_EVIDENCE"
    assert not result["builder_unresolved_decisions"]["inherited"]


def test_rank_matrix_distinguishes_real_gaps_from_owner_scope_exclusions():
    all_asins = ["B07F6LYVT6", "B077H1MZ35", "B000000014"]
    owner_scope = derive_owner_excluded_scope(
        [{"asin": asin} for asin in all_asins], parent_dataset_canonical_hash=_hash([{"asin": asin} for asin in all_asins]),
    )
    complete_with_exclusion = [_ranking("B000000014", rank=1), _ranking("B07F6LYVT6", rank=2), _ranking("B077H1MZ35", rank=3)]
    diagnostic = rank_matrix_diagnostics(complete_with_exclusion, exact_scope={"B000000014"}, owner_scope=owner_scope)
    assert not diagnostic["issues"]
    assert {item["asin"] for item in diagnostic["out_of_exact_scope"]} == {"B07F6LYVT6", "B077H1MZ35"}
    assert all(item["classification"] == "OUT_OF_EXACT_SCOPE" and item["exclusion"] for item in diagnostic["out_of_exact_scope"])
    real_gap = rank_matrix_diagnostics([_ranking("B000000014", rank=1), _ranking("B07F6LYVT6", rank=3)], exact_scope={"B000000014"}, owner_scope=owner_scope)
    assert real_gap["issues"][0]["issue_code"] == "RANK_GAP"
