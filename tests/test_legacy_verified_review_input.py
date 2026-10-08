import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.translation import legacy_reference as legacy
from amazon_es_bestseller.orchestration.translation_batch import file_hash
from test_translation_parent_authority import _source


def _fixture(tmp_path):
    path, _ = _source(tmp_path, scalar_fields={"title_es_raw": "Producto de prueba"})
    source = json.loads(path.read_text(encoding="utf-8"))
    references = {row["asin"]: {"spanish": {"title_es_raw": row["title_es_raw"]},
        "chinese": {"title_es_raw": "测试商品"}, "provenance": {"source_kind": "legacy_excel_reference", "provider": "unknown"}}
        for row in source["records"] if row.get("title_es_raw")}
    candidates = legacy.build_legacy_reference_candidates(source["records"], references, source["source_gate"])
    kwargs = {"source_candidate_manifest_path": path.parent / "manifest.json",
              "source_candidate_manifest_hash": file_hash(path.parent / "manifest.json"),
              "available_asins": [row["asin"] for row in source["records"]]}
    return source, candidates, kwargs


def test_real_producer_loader_prepares_pending_legacy_review_without_synthetic_gate(tmp_path):
    source, candidates, kwargs = _fixture(tmp_path)
    snapshot = deepcopy(candidates)
    review = legacy.prepare_legacy_review_input(candidates, **kwargs)
    assert review["status"] == "LEGACY_PENDING_SEMANTIC_REVIEW"
    assert review["authority"]["source_gate_audit_hash"] == source["source_gate"]["audit_hash"]
    assert "canonical_hash" not in source["source_gate"]
    assert review["admitted_candidate_hashes"] == []
    assert review["formal_tm_writes"] == review["provider_calls"] == 0
    assert all(row["decision"] == "PENDING_SEMANTIC_REVIEW" for row in review["candidates"])
    assert all(row["provider"] == "unknown" and row["resolution_source"] == "legacy-reviewed-reference" for row in review["candidates"])
    assert candidates == snapshot
    gate = legacy.evaluate_legacy_reviewed_formal_gate(candidates, source["source_gate"],
        {"policy_version": legacy.LEGACY_REVIEWED_POLICY_VERSION,
         "reviewed_candidate_hashes": [row["candidate_hash"] for row in review["candidates"]]}, **kwargs)
    assert gate["status"] == "LEGACY_REVIEW_REQUIRED"
    assert gate["admitted_candidate_hashes"] == []


def test_semantic_keep_requires_exact_current_candidate_context_and_qa(tmp_path):
    source, candidates, kwargs = _fixture(tmp_path)
    review = legacy.prepare_legacy_review_input(candidates, **kwargs)
    row = review["candidates"][0]
    decision = {"candidate_hash": row["candidate_hash"], "source_hash": row["source_hash"],
        "context_hash": row["context_hash"], "reviewed_value": row["candidate_value"],
        "decision": "KEEP", "semantic_review_status": "PASS", "review_model": "CODEX",
        "review_note": "逐字段核对当前西班牙语类目与中文类目，未发现新增事实。"}
    policy = {"policy_version": legacy.LEGACY_REVIEWED_POLICY_VERSION, "semantic_decisions": [decision]}
    gate = legacy.evaluate_legacy_reviewed_formal_gate(candidates, source["source_gate"], policy, **kwargs)
    assert gate["admitted_candidate_hashes"] == [row["candidate_hash"]]
    for key in ("source_hash", "context_hash", "reviewed_value", "semantic_review_status"):
        invalid = deepcopy(policy)
        invalid["semantic_decisions"][0][key] = "stale"
        blocked = legacy.evaluate_legacy_reviewed_formal_gate(candidates, source["source_gate"], invalid, **kwargs)
        assert blocked["admitted_candidate_hashes"] == []


@pytest.mark.parametrize("mutation", ["manifest_hash", "context", "record_context", "source_hash", "empty_source", "qwen_provenance", "scope"])
def test_legacy_review_rejects_unverified_or_mismatched_current_facts(tmp_path, mutation):
    _, candidates, kwargs = _fixture(tmp_path)
    if mutation == "manifest_hash":
        kwargs["source_candidate_manifest_hash"] = "wrong"
    elif mutation == "context":
        candidates["source_binding"]["current_canonical_hash"] = "wrong"
    elif mutation == "source_hash":
        candidates["candidates"][0]["field_hash"] = "wrong"
    elif mutation == "record_context":
        candidates["candidates"][0]["current_canonical_hash"] = "wrong"
    elif mutation == "empty_source":
        candidates["candidates"][0]["legacy_es"] = ""
    elif mutation == "qwen_provenance":
        candidates["candidates"][0]["provider"] = "qwen-mt"
    else:
        candidates["candidates"][0]["asin"] = "B000000099"
    with pytest.raises(ValueError):
        legacy.prepare_legacy_review_input(candidates, **kwargs)


def test_auto_qa_and_self_asserted_canonical_gate_cannot_admit_legacy(tmp_path):
    _, candidates, _ = _fixture(tmp_path)
    gate = legacy.evaluate_legacy_reviewed_formal_gate(candidates,
        {"status": "SOURCE_READY", "ready": True, "canonical_hash": candidates["source_binding"]["current_canonical_hash"]},
        {"policy_version": legacy.LEGACY_REVIEWED_POLICY_VERSION,
         "reviewed_candidate_hashes": [legacy._hash_json(row) for row in candidates["candidates"]]})
    assert gate["status"] == "SOURCE_BINDING_REVERIFY_REQUIRED"
    assert gate["admitted_candidate_hashes"] == []


def test_current_qa_failure_cannot_be_admitted_by_semantic_keep_claim(tmp_path):
    source, candidates, kwargs = _fixture(tmp_path)
    candidates["candidates"][0]["legacy_zh"] = ""
    with pytest.raises(ValueError, match="FIELD_BINDING"):
        legacy.prepare_legacy_review_input(candidates, **kwargs)
    candidates["candidates"][0]["legacy_zh"] = "测试商品 999"
    review = legacy.prepare_legacy_review_input(candidates, **kwargs)
    row = review["candidates"][0]
    assert row["qa"]["qa_status"] == "qa_failed"
    decision = {"candidate_hash": row["candidate_hash"], "source_hash": row["source_hash"],
        "context_hash": row["context_hash"], "reviewed_value": row["candidate_value"], "decision": "KEEP",
        "semantic_review_status": "PASS", "review_model": "CODEX", "review_note": "声明不会替代当前硬QA。"}
    gate = legacy.evaluate_legacy_reviewed_formal_gate(candidates, source["source_gate"],
        {"policy_version": legacy.LEGACY_REVIEWED_POLICY_VERSION, "semantic_decisions": [decision]}, **kwargs)
    assert gate["admitted_candidate_hashes"] == []


def test_existing_legacy_builder_cli_writes_verified_pending_review_input(tmp_path):
    from openpyxl import Workbook

    _, _, kwargs = _fixture(tmp_path)
    references = []
    for language, headers, value in (("es", legacy.LEGACY_ES_HEADERS, "Producto de prueba"),
                                     ("zh", legacy.LEGACY_ZH_HEADERS, "测试商品")):
        workbook = Workbook()
        sheet = workbook.active
        header = next(key for key, field in headers.items() if field == "title_es_raw")
        sheet.append(["ASIN", header])
        sheet.append(["B000000020", value])
        path = tmp_path / f"reference_{language}.xlsx"
        workbook.save(path)
        references.append(path)
    ranking = tmp_path / "ranking.json"
    ranking.write_text(json.dumps([{"asin": asin} for asin in kwargs["available_asins"]]), encoding="utf-8")
    manifest = kwargs["source_candidate_manifest_path"]
    output = tmp_path / "legacy"
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "tools/build_legacy_reference_candidates.py"),
        "--canonical-candidate", str(manifest.parent / "spanish_master_5480.json"),
        "--source-gate-manifest", str(manifest), "--spanish-reference", str(references[0]),
        "--chinese-reference", str(references[1]), "--output-dir", str(output),
        "--prepare-review-input", "--ranking-evidence", str(ranking)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    prepared = json.loads((output / "legacy_review_input.json").read_text(encoding="utf-8"))
    assert prepared["status"] == "LEGACY_PENDING_SEMANTIC_REVIEW"
    assert prepared["admitted_candidate_hashes"] == []
    assert prepared["authority"]["source_candidate_manifest_hash"] == file_hash(manifest)
