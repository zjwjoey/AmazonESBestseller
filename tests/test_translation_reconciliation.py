"""Sanitized synthetic runs exercising the real offline reconciliation boundary."""
from __future__ import annotations

import csv
import hashlib
import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.reconciliation.accounting import origin, state
from amazon_es_bestseller.translation.reconciliation.diagnostics import benchmark
from amazon_es_bestseller.translation.reconciliation.evidence import EvidenceReader
from amazon_es_bestseller.translation.reconciliation.report import ReadOnlyKeys, OfflineIdentity, reconcile, current_qa_rules_hash
from amazon_es_bestseller.translation.schemas import TRANSLATION_SCHEMA_VERSION
from amazon_es_bestseller.translation.service import TranslationService, source_hash


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def service():
    return TranslationService(OfflineIdentity({"name": "qwen-mt", "model": "qwen-mt-flash"}), ReadOnlyKeys())


def batch(tmp_path, name, asins, *, text="Objeto", field="title_es_raw", saved=True, http=True, items=None):
    directory = tmp_path / name
    directory.mkdir()
    records = [{"asin": asin, "raw_fields": {field: {"source_present": True, "source_hash": source_hash(text)}},
                "fields": {field: {"source_text": text, "clean_text": text, "translate_allowed": True}}} for asin in asins]
    product = write(directory / "products.json", {"records": records})
    manifest = {"schema_version": "translation-run-context-v1", "products": product, "selected_asins": asins,
                "fields": [field], "provider_identity": {"name": "qwen-mt", "model": "qwen-mt-flash"}, "service_contract": {}}
    binding = write(directory / "run_manifest.json", manifest)
    write(directory / "run_config.json", {"run_context": binding})
    key = service()._memory_key(text, field)
    events = [{"event": "AUTHORIZED_RUN_START", "time": "2026-10-09T00:00:00Z"}]
    if http:
        events += [{"event": "DURABLE_CLAIM_SAVED", "canonical_dispatch_key": key},
            {"event": "PROVIDER_ENTRY", "canonical_dispatch_key": key, "alias": "A", "asin": asins[0], "field": field},
            {"event": "HTTP_ATTEMPT_BEFORE_POST", "canonical_dispatch_key": key, "alias": "A", "attempt": 1, "total_attempts": 1},
            {"event": "HTTP_RESULT", "canonical_dispatch_key": key, "alias": "A", "status_code": 200}]
    if saved:
        events.append({"event": "CANARY_PROCESS_FINISHED", "exit_code": 0})
        write(directory / "final_manifest.json", {"exit_code": 0})
        values = {}
        target = "product_details_zh" if field == "product_details" else "title_zh"
        for asin in asins:
            envelope = {"asin": asin, "field": field, "source_hash": source_hash(text), "source_text": text,
                        "translated_text": "对象", "qa_status": "pass", "translation_status": "success",
                        "provider": "qwen-mt", "schema_version": TRANSLATION_SCHEMA_VERSION}
            if items is not None:
                envelope["items"] = items
            values[asin] = {"asin": asin, "fields": {target: envelope}}
        write(directory / "result.json", values)
    (directory / "runtime_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    write(directory / "dryrun_plan.json", {"dispatch_keys": [key]})
    return {"id": name, "directory": str(directory)}


def config(tmp_path, *batches):
    return {"allowed_roots": [str(tmp_path)], "batches": list(batches)}


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("artifact", ["cache", "manifest", "run_config", "final_manifest"])
def test_wrong_shaped_json_is_conflict_not_crash(tmp_path, artifact):
    spec = batch(tmp_path, "shape", ["B000000001"])
    cfg = config(tmp_path, spec)
    if artifact == "cache":
        path = tmp_path / "cache.json"
        cfg["cache"] = str(path)
    else:
        filename = "run_manifest.json" if artifact == "manifest" else artifact + ".json"
        path = Path(spec["directory"]) / filename
    binding = write(path, ["not-an-object"])
    if artifact == "manifest":
        write(Path(spec["directory"]) / "run_config.json", {"run_context": binding})
    summary = reconcile(cfg, tmp_path / "report")
    assert summary["conflict_counts"]["EVIDENCE_SHAPE_INVALID"] >= 1
    assert summary["sku_state_counts"].get("COMPLETE", 0) == 0


def test_40_resume_deduplicates_selected_skus(tmp_path):
    asins = [f"B{i:09d}" for i in range(40)]
    initial = batch(tmp_path, "initial40", asins)
    resumed = batch(tmp_path, "resume736", asins, http=False)
    report = reconcile(config(tmp_path, initial, resumed), tmp_path / "report")
    assert report["selected_unique_skus"] == report["saved_result_unique_skus"] == 40
    assert report["http_attempts"] == 1


def test_150_plus_850_is_1000_without_counting_result_rows_twice(tmp_path):
    first = batch(tmp_path, "saved150", [f"B{i:09d}" for i in range(150)], http=False)
    second = batch(tmp_path, "pending850", [f"B{i:09d}" for i in range(150, 1000)], saved=False, http=False)
    report = reconcile(config(tmp_path, first, second), tmp_path / "report")
    assert report["selected_unique_skus"] == 1000
    assert report["saved_result_unique_skus"] == 150
    assert len(rows(tmp_path / "report" / "asin_completion.csv")) == 1000


def test_semantic_request_can_benefit_multiple_asins(tmp_path):
    spec = batch(tmp_path, "two", ["B000000001", "B000000002"])
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["http_attempts"] == 1
    http = rows(tmp_path / "report" / "provider_http_ledger.csv")[0]
    assert json.loads(http["beneficiary_asins"]) == ["B000000001", "B000000002"]
    assert http["request_owner"] == "B000000001"


def test_http_attempts_and_semantic_dispatches_are_separate(tmp_path):
    spec = batch(tmp_path, "repeat", ["B000000001"])
    path = Path(spec["directory"]) / "runtime_events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events.insert(-1, {**events[3], "attempt": 2, "total_attempts": 2})
    path.write_text("".join(json.dumps(row) + "\n" for row in events))
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["http_attempts"] == 2
    assert report["unique_sent_semantic_keys"] == 1
    assert report["duplicate_sent_semantic_keys"] == 1
    assert report["unknown_http_outcomes"] == 2


def test_original_derived_latest_qa_do_not_replace_each_other(tmp_path):
    spec = batch(tmp_path, "qa", ["B000000001"])
    key = service()._memory_key("Objeto", "title_es_raw")
    stable = "|".join(key.split("|")[:6])
    cache = write(tmp_path / "cache.json", {"memory_results": {stable: {"qa_status": "qa_failed"}}})
    derived = write(tmp_path / "derived.json", {"summary": {"rule_version": "old-review"},
        "units": [{"canonical_key": key, "saved_render": "对象", "after": {"qa_status": "pass"}}]})
    cfg = config(tmp_path, spec)
    cfg.update(cache=cache["path"], derived_qa=[derived], latest_qa_rule_version="new-rule")
    report = reconcile(cfg, tmp_path / "report")
    assert report["original_http200_qa_counts"] == {"qa_failed": 1}
    row = rows(tmp_path / "report" / "parent_field_ledger.csv")[0]
    assert row["saved_qa_status"] == "pass"
    assert row["latest_qa_status"] == "UNKNOWN"
    assert row["completion_state"] == "QA_PASS"


def test_parent_text_with_missing_structured_child_never_completes(tmp_path):
    spec = batch(tmp_path, "detail", ["B000000001"], field="product_details", text="Color: Rojo\nMaterial: Algodón", items=[])
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["parent_field_state_counts"] == {"PARTIAL": 1}
    assert len(rows(tmp_path / "report" / "structured_item_ledger.csv")) == 2
    assert report["saved_all_required_structurally_complete_skus"] == 0


def test_owner_excluded_is_not_qa_pass_even_if_chinese_exists():
    assert state(text=True, qa="pass", excluded=True, complete=True) == "SOURCE_REVIEW_BLOCKED"


def test_47_legacy_reference_fields_do_not_create_http(tmp_path):
    spec = batch(tmp_path, "legacy", [f"B{i:09d}" for i in range(47)], http=False)
    directory = Path(spec["directory"])
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    for record in result.values():
        record["fields"]["title_zh"].update(provider="unknown", model="legacy-reviewed-reference", human_gold=False)
    write(directory / "result.json", result)
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["unit_provenance_counts"] == {"LEGACY_REVIEWED_REFERENCE": 47}
    assert report["http_attempts"] == 0


def test_changed_cache_is_active_provisional(tmp_path):
    spec = batch(tmp_path, "live", ["B000000001"], saved=False)
    path = tmp_path / "cache.json"
    write(path, {"entries": {}})
    original = EvidenceReader.finish
    def changed(reader):
        write(path, {"entries": {}, "memory": {}})
        return original(reader)
    cfg = config(tmp_path, spec)
    cfg["cache"] = str(path)
    with patch.object(EvidenceReader, "finish", changed):
        report = reconcile(cfg, tmp_path / "report")
    assert report["observation_state"] == "ACTIVE_PROVISIONAL"
    assert report["atomic_cross_file_snapshot"] is False


def test_alive_process_makes_even_stable_reads_provisional(tmp_path):
    spec = batch(tmp_path, "live", ["B000000001"])
    cfg = config(tmp_path, spec)
    cfg["runtime_observation"] = {"matching_process_alive": True}
    assert reconcile(cfg, tmp_path / "report")["observation_state"] == "ACTIVE_PROVISIONAL"


def test_claim_entry_send_unknown_are_distinct():
    assert state(text=False, evidence={"claims": [1]}) == "CLAIMED_NOT_ENTERED"
    assert state(text=False, evidence={"claims": [1], "entries": [1]}) == "ENTERED_NOT_SENT"
    assert state(text=False, evidence={"claims": [1], "sends": [1]}) == "SENT_PENDING_OR_UNKNOWN"
    assert state(text=False, saved_status="pending") == "UNKNOWN"


def test_manual_halt_is_not_429(tmp_path):
    spec = batch(tmp_path, "manual", ["B000000001"])
    write(Path(spec["directory"]) / "HTTP429_HALT.json", {"reason": "manual_rate_adjustment", "http429_observed": False})
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["http_status_counts"] == {"200": 1}
    assert report["batches"][0]["stop_marker"]["reason"] == "manual_rate_adjustment"


def test_corrupt_cache_and_truncated_jsonl_are_explicit_and_preserved(tmp_path):
    spec = batch(tmp_path, "bad", ["B000000001"])
    cache = tmp_path / "cache.json"
    cache.write_bytes(b'{"entries":')
    events = Path(spec["directory"]) / "runtime_events.jsonl"
    with events.open("ab") as handle:
        handle.write(b'{"event":')
    before = {p: p.read_bytes() for p in (cache, events)}
    cfg = config(tmp_path, spec)
    cfg["cache"] = str(cache)
    report = reconcile(cfg, tmp_path / "report")
    assert report["observation_state"] == "ACTIVE_PROVISIONAL"
    assert report["conflict_counts"]["EVIDENCE_INVALID_JSON"] == 1
    assert report["conflict_counts"]["JSONL_INCOMPLETE_TAIL"] == 1
    assert {p: p.read_bytes() for p in before} == before
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_hash_mismatch_missing_file_and_duplicate_json_key_reported(tmp_path):
    reader = EvidenceReader([str(tmp_path)])
    path = tmp_path / "data.json"
    path.write_bytes(b'{"x":1,"x":2}')
    reader.read(path, expected_hash="wrong")
    reader.read(tmp_path / "missing.json")
    assert {row["code"] for row in reader.conflicts} >= {"DUPLICATE_JSON_KEY", "BINDING_HASH_MISMATCH", "EVIDENCE_MISSING_OR_UNREADABLE"}


def test_dry_run_5000_not_counted_as_real_translation(tmp_path):
    spec = batch(tmp_path, "dry5000", ["B000000001"])
    spec["dry_run"] = True
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["selected_unique_skus"] == 0
    assert report["http_attempts"] == 0


def test_input_bytes_unchanged_and_no_network_or_live_cache_constructor(tmp_path):
    spec = batch(tmp_path, "readonly", ["B000000001"])
    before = {p: p.read_bytes() for p in Path(spec["directory"]).rglob("*") if p.is_file()}
    with patch.object(TranslationCache, "__init__", side_effect=AssertionError("mutable cache")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
         patch("urllib.request.urlopen", side_effect=AssertionError("provider")):
        reconcile(config(tmp_path, spec), tmp_path / "report")
    assert {p: p.read_bytes() for p in before} == before


def test_output_cannot_overlap_or_rewrite_originals(tmp_path):
    spec = batch(tmp_path, "original", ["B000000001"])
    with pytest.raises(ValueError, match="OUTPUT_OVERLAPS"):
        reconcile(config(tmp_path, spec), Path(spec["directory"]) / "report")
    out = tmp_path / "report"
    reconcile(config(tmp_path, spec), out)
    with pytest.raises(FileExistsError):
        reconcile(config(tmp_path, spec), out)


def test_current_review_must_bind_same_text_to_allow_complete(tmp_path):
    spec = batch(tmp_path, "current", ["B000000001"])
    key = service()._memory_key("Objeto", "title_es_raw")
    derived = write(tmp_path / "latest.json", {"summary": {"rule_version": "latest"},
        "units": [{"canonical_key": key, "saved_render": "other text", "after": {"qa_status": "pass"}}]})
    cfg = config(tmp_path, spec)
    cfg.update(latest_qa_rule_version="latest", derived_qa=[derived])
    assert reconcile(cfg, tmp_path / "report")["parent_field_state_counts"] == {"QA_PASS": 1}


def test_provenance_is_not_guessed_from_provider_name():
    assert origin({"provider": "qwen-mt"}) == "UNKNOWN"
    assert origin({"provider": "qwen-mt"}, direct_proven=True) == "QWEN_DIRECT"
    assert origin({"model": "identity-v1"}) == "IDENTITY_PRESERVED"
    assert origin({"model": "dictionary-v1"}) == "DICTIONARY"


def test_real_preclean_string_issue_codes_are_supported(tmp_path):
    spec = batch(tmp_path, "held", ["B000000001"], saved=False, http=False)
    directory = Path(spec["directory"])
    product_path = directory / "products.json"
    products = json.loads(product_path.read_text(encoding="utf-8"))
    products["records"][0]["fields"]["title_es_raw"].update(issues=["PRECLEAN_REVIEW_REQUIRED"], translate_allowed=False)
    binding = write(product_path, products)
    manifest = json.loads((directory / "run_manifest.json").read_text())
    manifest["products"] = binding
    write(directory / "run_config.json", {"run_context": write(directory / "run_manifest.json", manifest)})
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["parent_field_state_counts"] == {"PRECLEAN_BLOCKED": 1}


def test_legacy_source_missing_envelope_has_empty_hash_not_conflict(tmp_path):
    spec = batch(tmp_path, "missing_source", ["B000000001"], text="", http=False)
    directory = Path(spec["directory"])
    products = json.loads((directory / "products.json").read_text())
    products["records"][0]["raw_fields"]["title_es_raw"]["source_present"] = False
    manifest = json.loads((directory / "run_manifest.json").read_text())
    manifest["products"] = write(directory / "products.json", products)
    write(directory / "run_config.json", {"run_context": write(directory / "run_manifest.json", manifest)})
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    result["B000000001"]["fields"]["title_zh"].update(source_hash="", translated_text="", translation_status="source_missing")
    write(directory / "result.json", result)
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["parent_field_state_counts"] == {"SOURCE_MISSING": 1}
    assert "FIELD_SOURCE_BINDING_MISMATCH" not in report["conflict_counts"]


def test_cohort_selection_supports_stratified_asin_objects(tmp_path):
    spec = batch(tmp_path, "cohort", ["B000000001"])
    cfg = config(tmp_path, spec)
    cfg["cohorts"] = {"provisional": [{"asin": "B000000001", "stratum": "new_l3"}]}
    reconcile(cfg, tmp_path / "report")
    assert len(rows(tmp_path / "report" / "provisional" / "asin_completion.csv")) == 1


def test_complete_requires_closed_result_full_same_render_current_qa_and_asin_binding(tmp_path):
    spec = batch(tmp_path, "complete", ["B000000001", "B000000002"])
    key = service()._memory_key("Objeto", "title_es_raw")
    rules = current_qa_rules_hash()
    review = write(tmp_path / "qa_current.json", {"summary": {"rule_version": "current", "qa_rules_hash": rules},
        "units": [{"canonical_key": key, "owner": {"asin": "B000000001"}, "saved_render": "对象", "after": {"qa_status": "pass"}}]})
    cfg = config(tmp_path, spec)
    cfg.update(latest_qa_rule_version="current", current_qa_rules_hash=rules, derived_qa=[review])
    report = reconcile(cfg, tmp_path / "report")
    assert report["parent_field_state_counts"] == {"COMPLETE": 1, "QA_PASS": 1}
    assert report["sku_state_counts"] == {"COMPLETE": 1, "PARTIAL": 1}


def test_owner_exclusion_is_separate_raw_trace_not_translated_item(tmp_path):
    spec = batch(tmp_path, "owner", ["B000000001"], http=False)
    directory = Path(spec["directory"])
    source = {"records": [{"asin": "B000000001", "owner_attribute_exclusions": {
        "excluded_items": [{"locator": {"position": 2, "label_raw": "Material", "value_raw": "ambiguous"}}]}}]}
    source_binding = write(directory / "source" / "spanish_master_5480.json", source)
    source_manifest = write(directory / "source" / "manifest.json", {"artifacts": {
        "spanish_master_5480.json": source_binding["sha256"]}, "source_gate": {"status": "SOURCE_READY", "ready": True}})
    manifest = json.loads((directory / "run_manifest.json").read_text())
    manifest["source_manifest"] = source_manifest
    write(directory / "run_config.json", {"run_context": write(directory / "run_manifest.json", manifest)})
    report = reconcile(config(tmp_path, spec), tmp_path / "report")
    assert report["owner_excluded_items"] == 1
    child = rows(tmp_path / "report" / "structured_item_ledger.csv")[0]
    assert child["completion_state"] == "SOURCE_REVIEW_BLOCKED"
    assert child["saved_qa_status"] == "EXCLUDED_BY_OWNER"
    assert child["required"] == "False"


def test_serialized_reports_redact_credentials_and_headers(tmp_path):
    spec = batch(tmp_path, "redaction", ["B000000001"])
    directory = Path(spec["directory"])
    write(directory / "HTTP429_HALT.json", {"reason": "error sk-synthetic.secret", "Authorization": "Bearer private"})
    reconcile(config(tmp_path, spec), tmp_path / "report")
    for path in (tmp_path / "report").rglob("*"):
        if path.is_file():
            contents = path.read_text(encoding="utf-8-sig")
            assert "sk-synthetic.secret" not in contents
            assert "Bearer private" not in contents


def test_synthetic_performance_diagnostics_have_no_network(tmp_path):
    with patch.object(socket, "create_connection", side_effect=AssertionError("network")):
        report = benchmark(tmp_path / "diagnostic", sizes=(5,), operations=1, stopped_records=2)
    claim = report["rows"][0]
    assert claim["loads"] == claim["saves"] == 2
    assert claim["written_bytes"] > claim["starting_cache_bytes"]
    assert report["rows"][1]["http_attempts"] == 0
    assert report["rows"][1]["provider_entries"] == 2
