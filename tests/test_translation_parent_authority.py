import json
import hashlib
from copy import deepcopy

import pytest

from amazon_es_bestseller.orchestration.workflow import ProductionWorkflow, ProductionWorkflowError
from amazon_es_bestseller.orchestration.task_config import TaskConfig, TaskConfigError
from amazon_es_bestseller.production.spanish_source_closure import (
    _audit_eligible_attribute_view,
    build_current_source_gate_candidate, build_owner_attribute_exclusion_manifest,
    derive_owner_excluded_scope, write_spanish_source_candidate,
)
from amazon_es_bestseller.translation.structured_contract import build_structured_translation_draft
from test_spanish_source_closure import _builder_artifact, _detail, _hash, _ranking
from test_translation_structured_contract import _CaptureProvider
from test_production_workflow import _write_fixture


def _source(tmp_path, *, no_policy=False, blocked=False, scalar_fields=None):
    asins = ["B07F6LYVT6", "B077H1MZ35", "B000000020"]
    candidates = [_ranking(asin, leaf_category="Prueba", **(scalar_fields or {})) for asin in asins]
    parents = [dict(row, ranking_contexts=[dict(row)], attributes=[], raw_source={"fixture": True},
                    detail_schema_version="2", ranking_schema_version="ranking-v1", parser_version="fixture-v1")
               for row in candidates]
    parents[-1]["attributes"] = [
        {"section": "Info", "label_raw": "Capacidad", "value_raw": "2,3 kg", "position": 0},
        {"section": "Info", "label_raw": "Material", "value_raw": "Acero", "position": 1},
    ]
    if no_policy:
        parents[-1]["attributes"] = parents[-1]["attributes"][1:]
    scope = derive_owner_excluded_scope(parents, parent_dataset_canonical_hash=_hash(parents))
    kwargs = dict(expected_input_hashes={"candidate_manifest": _hash(candidates),
                  "details": _hash([_detail(a) for a in asins]), "rankings": _hash(candidates)},
                  builder_decision_artifact=_builder_artifact(parents))
    baseline = build_current_source_gate_candidate(candidates, [_detail(a) for a in asins], candidates,
                                                   parents, scope, **kwargs)
    if no_policy or blocked:
        result = baseline
    else:
        exclusions = build_owner_attribute_exclusion_manifest(parents[2:], baseline["raw_source_audit"],
            owner_decision="owner-approved-current-source-attribute-exclusion-v1")
        result = build_current_source_gate_candidate(candidates, [_detail(a) for a in asins], candidates,
            parents, scope, owner_attribute_exclusion_manifest=exclusions, **kwargs)
    directory = tmp_path / "source"
    write_spanish_source_candidate(directory, result)
    return directory / "spanish_master_5480.json", parents[2:]


def _workflow(tmp_path, source_path, *, audit_source=True, provider=None, before_normalize=None):
    provider = provider or _CaptureProvider()
    config_path = _write_fixture(tmp_path, asins=("B000000020",))
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["source"]["source_candidate_manifest"] = str(source_path.parent / "manifest.json")
    raw["translation"] = {"provider_mode": "qwen-mt-fixture", "structured_profile": "formal"}
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    task = TaskConfig.from_mapping(json.loads(config_path.read_text(encoding="utf-8")), base_dir=tmp_path)
    workflow = ProductionWorkflow(task, tmp_path / "run", run_id="parent-test", translation_provider=provider)
    context = {"manifest": {"stages": {}}}
    for stage in ("preflight", "ranking-authority", "detail-evidence", "offline-reparse", "normalize"):
        if stage == "normalize" and before_normalize is not None:
            before_normalize()
        context["manifest"]["stages"][stage] = {"payload": getattr(
            workflow, "stage_" + stage.replace("-", "_"))(context)}
    if audit_source:
        context["manifest"]["stages"]["source-audit"] = {"payload": workflow.stage_source_audit(context)}
        context["manifest"]["stages"]["spanish-master"] = {"payload": workflow.stage_spanish_master(context)}
    return workflow, context, provider


@pytest.mark.parametrize("no_policy", [False, True])
def test_real_source_audit_master_translation_input_translation_parent_chain(tmp_path, no_policy):
    path, parents = _source(tmp_path, no_policy=no_policy)
    workflow, context, provider = _workflow(tmp_path, path)
    batch = workflow._translation_batch(context)
    assert batch["translation_batch_parent_authority_records"] == ([] if no_policy else parents)
    draft = build_structured_translation_draft(batch["translation_batch_master"]["records"],
        review_item_ids=set(), authority_records=batch["translation_batch_parent_authority_records"])
    decisions = [{"item_id": f"{row['asin']}:{item['item_id']}", "source_hash": item["source_hash"],
                  "status": "APPROVED"} for row in draft["records"]
                 for field in row["fields"].values() for item in field["items"]]
    review = {"schema_version": "structured-review-snapshot-v1",
        "source_audit_hash": batch["translation_batch_source_gate"]["audit_hash"],
        "item_ids": [d["item_id"] for d in decisions], "item_decisions": decisions}
    workflow.task.translation["structured_review_snapshot"] = review
    for stage in ("translation-input", "preclean", "dictionary"):
        context["manifest"]["stages"][stage] = {"payload": getattr(
            workflow, "stage_" + stage.replace("-", "_"))(context)}
    translated = workflow.stage_translation(context)
    assert translated["state"]["formal_release"] is False
    if not no_policy:
        assert translated["execution"]["records"][parents[0]["asin"]]["fields"]["product_details"]["excluded_raw_trace"]
    assert all("2,3 kg" not in call[2] for call in provider.calls)


@pytest.mark.parametrize("damage", ["missing", "deleted", "hash", "content_hash", "scope", "derived"])
def test_parent_artifact_fail_closed_before_provider(tmp_path, damage):
    path, _ = _source(tmp_path)
    source = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ref = source.get("parent_authority_artifact")
    assert ref, "source producer must persist an independent parent reference"
    if damage == "missing":
        source.pop("parent_authority_artifact")
    elif damage == "deleted":
        (path.parent / ref["artifact_path"]).unlink()
    elif damage == "hash":
        ref["artifact_file_hash"] = "0" * 64
    elif damage == "content_hash":
        ref["dataset_canonical_hash"] = "0" * 64
    elif damage == "scope":
        ref["asins"] = ["B000000099"]
    else:
        parent_path = path.parent / ref["artifact_path"]
        parent_path.write_text(json.dumps({"records": deepcopy(source["records"])}), encoding="utf-8")
        ref["artifact_file_hash"] = hashlib.sha256(parent_path.read_bytes()).hexdigest()
        ref["dataset_canonical_hash"] = _hash(source["records"])
    path.write_text(json.dumps(source), encoding="utf-8")
    manifest["artifacts"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if source.get("parent_authority_artifact") is None:
        manifest.pop("parent_authority_artifact", None)
    else:
        manifest["parent_authority_artifact"] = source["parent_authority_artifact"]
        manifest["artifacts"]["parent_authority.json"] = ref["artifact_file_hash"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _CaptureProvider()
    with pytest.raises(ProductionWorkflowError, match="PARENT_AUTHORITY"):
        _workflow(tmp_path, path, provider=provider)
    assert provider.calls == []


@pytest.mark.parametrize("damage", ["manifest", "master_file", "canonical", "scope", "schema", "policy"])
def test_config_candidate_manifest_master_scope_and_policy_fail_closed(tmp_path, damage):
    path, _ = _source(tmp_path)
    manifest_path = path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = json.loads(path.read_text(encoding="utf-8"))
    callback = None
    if damage == "manifest":
        def callback():
            manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif damage == "master_file":
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif damage == "canonical":
        manifest["dataset_canonical_hash"] = "0" * 64
    elif damage == "scope":
        manifest["binding_scope"]["asins"] = ["B000000099"]
    elif damage == "schema":
        manifest["schema_version"] = "invented"
    else:
        candidate["records"][0]["owner_attribute_exclusions"]["raw_record_hash"] = "0" * 64
        manifest["dataset_canonical_hash"] = _hash(candidate["records"])
        candidate["source_exclusion_policy"]["derived_dataset_canonical_hash"] = _hash(candidate["records"])
        path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest["artifacts"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _CaptureProvider()
    with pytest.raises(ProductionWorkflowError, match="SOURCE_CANDIDATE"):
        _workflow(tmp_path, path, provider=provider, before_normalize=callback)
    assert provider.calls == []


def test_config_candidate_preserves_blocked_source_gate(tmp_path):
    path, _ = _source(tmp_path, blocked=True)
    provider = _CaptureProvider()
    with pytest.raises(ProductionWorkflowError, match="SOURCE_CANDIDATE_SOURCE_GATE_NOT_READY"):
        _workflow(tmp_path, path, provider=provider)
    assert provider.calls == []


def test_source_candidate_config_is_offline_only(tmp_path):
    path, _ = _source(tmp_path)
    with pytest.raises(TaskConfigError, match="SOURCE_CANDIDATE_OFFLINE_ONLY"):
        TaskConfig.from_mapping({"task_id": "candidate", "network_mode": "live",
            "source": {"source_candidate_manifest": str(path.parent / "manifest.json")}}, base_dir=tmp_path)


@pytest.mark.parametrize("damage", ["raw_record_hash", "raw_attributes_hash", "eligible_attributes_hash",
                                   "section", "source_position"])
def test_loaded_parent_keeps_strong_attribute_binding(tmp_path, damage):
    path, _ = _source(tmp_path)
    workflow, context, provider = _workflow(tmp_path, path)
    batch = workflow._translation_batch(context)
    records = deepcopy(batch["translation_batch_master"]["records"])
    policy = records[0]["owner_attribute_exclusions"]
    if damage in {"section", "source_position"}:
        policy["excluded_items"][0]["locator"][damage] = "wrong" if damage == "section" else 999
    else:
        policy[damage] = "0" * 64
    with pytest.raises(ValueError, match="OWNER_ATTRIBUTE_EXCLUSION"):
        build_structured_translation_draft(records, review_item_ids=set(),
            authority_records=batch["translation_batch_parent_authority_records"])
    assert provider.calls == []


def test_real_source_audit_recomputes_gate_and_blocks_unexcluded_bad_source(tmp_path):
    path, parents = _source(tmp_path)
    workflow, context, provider = _workflow(tmp_path, path, audit_source=False)
    normalized = context["manifest"]["stages"]["normalize"]["payload"]
    products = workflow.run_dir / normalized["products_path"]
    rows = json.loads(products.read_text(encoding="utf-8"))
    rows[0]["attributes"] = parents[0]["attributes"]
    products.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ProductionWorkflowError, match="SOURCE_AUDIT_NOT_READY"):
        workflow.stage_source_audit(context)
    assert not (workflow.artifacts / "source-audit.json").exists()
    assert provider.calls == []
