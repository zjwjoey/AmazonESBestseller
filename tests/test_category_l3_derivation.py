from copy import deepcopy

import pytest

from amazon_es_bestseller.production.spanish_source_closure import _hash
from amazon_es_bestseller.production.category_l3_derivation import (
    rebind_l3_decisions, apply_l3_decisions, build_derived_l3_candidate, load_reviewed_l3_artifact,
)
from amazon_es_bestseller.translation.production import build_production_input


def _fixture(record=None):
    record = deepcopy(record) if record is not None else {"asin": "B000000001", "title_es_raw": "Producto", "category_l1": "Hogar",
              "category_l2": "Cocina", "category_l3": None, "rawattributes_raw": [{"value_raw": "raw"}],
              "detail_category_trail": ["Hogar", "Cocina", "Vasos"], "human_notes": "keep"}
    detail = {"asin": record["asin"], "identity_status_code": "IDENTITY_MATCH",
              "title_es_raw": record["title_es_raw"], "detail_category_trail": record["detail_category_trail"]}
    binding = {"asin": record["asin"], "field": "category_l3", "current_value": None,
               "record_canonical_hash": _hash(record), "current_l1": "Hogar", "current_l2": "Cocina",
               "detail_trail_hash": _hash(detail["detail_category_trail"])}
    evidence = {"saved_detail_trail": detail["detail_category_trail"],
                "proposal_trail": detail["detail_category_trail"], "l3_position_zero_based": 2,
                "proposed_l3": "Vasos", "hierarchy_prefix_matches_current_l1_l2": True,
                "proposed_l3_is_exact_trail_position_2": True,
                "proposal_trail_exactly_matches_saved_detail": True}
    artifact = {"schema_version": "l3-backfill-decision-artifact-v1",
                "scope": {"count": 1, "asins_canonical_hash": _hash([record["asin"]]),
                          "parent_master_dataset_canonical_hash": _hash([record])},
                "decisions": [{"asin": record["asin"], "decision": "CANDIDATE_ONLY_NOT_APPLIED",
                               "derived_change": {"field": "category_l3", "from": None, "to": "Vasos"},
                               "field_binding": binding, "field_binding_hash": _hash(binding),
                               "evidence": evidence, "evidence_hash": _hash(evidence)}],
                "retained_review": []}
    return artifact, [record], [detail]


def test_rebind_apply_changes_only_l3_and_invalidates_real_translation_hashes():
    artifact, old, details = _fixture()
    current = deepcopy(old)
    current[0]["owner_evidence_metadata"] = {"new_binding": True}
    rebound = rebind_l3_decisions(artifact, old, current, details)
    result = apply_l3_decisions(current, rebound)
    assert result["records"][0] == {**current[0], "category_l3": "Vasos"}
    assert old[0]["category_l3"] is None and current[0]["category_l3"] is None
    change = result["changes"][0]
    assert change["old"] is None and change["proposed"] == "Vasos"
    assert change["parent_record_hash"] == _hash(current[0])
    before = build_production_input(current)["records"][0]
    after = build_production_input(result["records"])["records"][0]
    assert before["source_record_hash"] != after["source_record_hash"]
    assert before["fields"]["category_l3"]["source_hash"] != after["fields"]["category_l3"]["source_hash"]
    assert change["old_field_hash"] == before["fields"]["category_l3"]["source_hash"]
    assert change["new_field_hash"] == after["fields"]["category_l3"]["source_hash"]
    for field in before["fields"]:
        if field != "category_l3":
            assert before["fields"][field] == after["fields"][field]


@pytest.mark.parametrize("damage", ["asin", "record_hash", "binding_hash", "evidence_hash", "title", "prefix", "trail", "conflict", "identity"])
def test_rebind_rejects_wrong_identity_hash_or_evidence(damage):
    artifact, old, details = _fixture()
    current = deepcopy(old)
    if damage == "asin":
        artifact["decisions"][0]["asin"] = "B000000099"
    elif damage == "record_hash":
        artifact["decisions"][0]["field_binding"]["record_canonical_hash"] = "bad"
        artifact["decisions"][0]["field_binding_hash"] = _hash(artifact["decisions"][0]["field_binding"])
    elif damage in {"binding_hash", "evidence_hash"}:
        artifact["decisions"][0]["field_binding_hash" if damage == "binding_hash" else damage] = "bad"
    elif damage == "title":
        current[0]["title_es_raw"] = "Other product"
    elif damage == "prefix":
        details[0]["detail_category_trail"][0] = "Other category"
    elif damage == "conflict":
        artifact["decisions"][0]["legacy_support_only"] = {"supports_or_absent": False}
    elif damage == "identity":
        details[0]["identity_status_code"] = "MISMATCH"
    else:
        details[0]["detail_category_trail"][2] = "Other L3"
    with pytest.raises(ValueError, match="L3_"):
        rebind_l3_decisions(artifact, old, current, details)


def test_existing_l3_and_review_conflicts_are_preserved():
    artifact, old, details = _fixture()
    current = deepcopy(old)
    current[0]["category_l3"] = "Existing"
    rebound = rebind_l3_decisions(artifact, old, current, details)
    result = apply_l3_decisions(current, rebound)
    assert result["records"] == current and result["applied_count"] == 0
    assert result["skipped"][0]["reason"] == "CURRENT_L3_NONEMPTY"
    artifact["retained_review"] = [{"asin": old[0]["asin"], "reason": "LEGACY_L3_CONFLICT"}]
    with pytest.raises(ValueError, match="L3_DECISION_REVIEW_OVERLAP"):
        rebind_l3_decisions(artifact, old, old, details)


def test_apply_rejects_changed_parent_after_rebind():
    artifact, old, details = _fixture()
    rebound = rebind_l3_decisions(artifact, old, old, details)
    changed = deepcopy(old)
    changed[0]["title_es_raw"] = "Changed after binding"
    with pytest.raises(ValueError, match="L3_REBOUND_PARENT_HASH_MISMATCH"):
        apply_l3_decisions(changed, rebound)


def test_derived_candidate_reaudits_and_roundtrips_existing_source_loader(tmp_path):
    from amazon_es_bestseller.production.spanish_source_closure import (
        CURRENT_SOURCE_GATE_SCHEMA_VERSION, write_spanish_source_candidate,
    )
    from amazon_es_bestseller.quality.source_fields import audit_source_fields
    from amazon_es_bestseller.quality.source_gate import evaluate_source_gate
    from amazon_es_bestseller.orchestration.translation_batch import file_hash, load_source_candidate
    artifact, old, details = _fixture()
    current = deepcopy(old)
    current[0].update(product_url="https://www.amazon.es/dp/B000000001", raw_source={"fixture": True},
                      category_provenance={"source": "ranking_context"}, leaf_category="Cocina")
    audit = audit_source_fields(current)
    gate = evaluate_source_gate(audit)
    assert gate["ready"]
    parent = {"schema_version": CURRENT_SOURCE_GATE_SCHEMA_VERSION, "records": current,
              "binding_scope": {"count": 1, "asins": [current[0]["asin"]], "exact_match": True},
              "source_audit": audit, "source_gate": gate}
    rebound = rebind_l3_decisions(artifact, old, current, details)
    candidate = build_derived_l3_candidate(parent, rebound)
    assert candidate["source_gate"]["ready"]
    assert candidate["source_gate"]["audit_hash"] != gate["audit_hash"]
    write_spanish_source_candidate(tmp_path / "derived", candidate)
    path = tmp_path / "derived" / "manifest.json"
    loaded = load_source_candidate(path, manifest_hash=file_hash(path), available_asins=[current[0]["asin"]])
    assert loaded["records"][0] == {**current[0], "category_l3": "Vasos"}


def test_reviewed_artifact_file_hash_is_required(tmp_path):
    path = tmp_path / "review.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="L3_DECISION_ARTIFACT_HASH_MISMATCH"):
        load_reviewed_l3_artifact(path, expected_file_hash="0" * 64)


def test_l3_application_keeps_producer_exclusion_and_parent_authority_loader_contract(tmp_path):
    from amazon_es_bestseller.production.spanish_source_closure import (
        CURRENT_SOURCE_GATE_SCHEMA_VERSION, _apply_owner_attribute_exclusions,
        _audit_eligible_attribute_view, _append_owner_exclusion_trail,
        build_owner_attribute_exclusion_manifest, write_spanish_source_candidate,
    )
    from amazon_es_bestseller.quality.source_fields import audit_source_fields
    from amazon_es_bestseller.quality.source_gate import evaluate_source_gate
    from amazon_es_bestseller.orchestration.translation_batch import file_hash, load_source_candidate
    _, parents, _ = _fixture()
    parents[0].pop("rawattributes_raw")
    parents[0].update(category_provenance={"source": "ranking_context"}, leaf_category="Cocina", attributes=[
        {"section": "Info", "label_raw": "Capacidad", "value_raw": "2,3 kg", "position": 0},
        {"section": "Info", "label_raw": "Material", "value_raw": "Acero", "position": 1}])
    raw_audit = audit_source_fields(parents)
    exclusion = build_owner_attribute_exclusion_manifest(parents, raw_audit,
        owner_decision="owner-approved-current-source-attribute-exclusion-v1")
    records, _ = _apply_owner_attribute_exclusions(parents, parents, exclusion)
    audit = _append_owner_exclusion_trail(audit_source_fields(_audit_eligible_attribute_view(records)), raw_audit, exclusion)
    gate = evaluate_source_gate(audit)
    assert gate["ready"]
    current = {"schema_version": CURRENT_SOURCE_GATE_SCHEMA_VERSION, "records": records,
        "source_audit": audit, "raw_source_audit": raw_audit, "source_gate": gate,
        "binding_scope": {"count": 1, "asins": [records[0]["asin"]], "exact_match": True},
        "source_exclusion_policy": {"raw_dataset_canonical_hash": _hash(parents),
                                    "derived_dataset_canonical_hash": _hash(records)}}
    artifact, old, details = _fixture(records[0])
    rebound = rebind_l3_decisions(artifact, old, records, details)
    derived = build_derived_l3_candidate(current, rebound, parent_authority_records=parents,
                                         owner_attribute_exclusion_manifest=exclusion)
    assert derived["source_gate"]["ready"]
    assert derived["source_gate"]["audit_hash"] != gate["audit_hash"]
    assert derived["records"][0]["owner_attribute_exclusions"] == records[0]["owner_attribute_exclusions"]
    write_spanish_source_candidate(tmp_path / "derived", derived)
    manifest = tmp_path / "derived" / "manifest.json"
    loaded = load_source_candidate(manifest, manifest_hash=file_hash(manifest), available_asins=[records[0]["asin"]])
    assert loaded["records"][0]["category_l3"] == "Vasos"
