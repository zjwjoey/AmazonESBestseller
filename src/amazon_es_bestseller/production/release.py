"""Fail-closed Production V1 release gate.

The gate consumes immutable artifacts and recomputes every decision that can
be derived locally.  It intentionally does not export, collect, translate or
repair facts.  ``READY`` is therefore a verified release decision, not a
caller supplied boolean.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..models import normalize_asin
from ..export.excel import HEAD_ES, HEAD_ZH, export_workbook
from ..quality.source_fields import audit_source_fields
from ..quality.source_gate import canonical_audit_hash, verify_source_gate
from .spanish_master import verify_artifact_hash


RELEASE_GATE_VERSION = "production-release-gate-v1"
READY = "READY"
BLOCKED = "BLOCKED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
DRAFT = "DRAFT"
STAGE_EVIDENCE_VERSION = "production-stage-evidence-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _master_record_hash(row: Mapping[str, Any], stage: str) -> str:
    """Hash only the evidence a concrete stage is allowed to claim."""
    common = {"asin": normalize_asin(row.get("asin")), "source_hash": row.get("source_hash"),
              "detail_schema_version": row.get("detail_schema_version"),
              "ranking_schema_version": row.get("ranking_schema_version"),
              "parser_version": row.get("parser_version")}
    if stage == "ranking_authority":
        common.update({"ranking_contexts": row.get("ranking_contexts"), "product_url": row.get("product_url")})
    elif stage == "detail_identity":
        common.update({"requested_asin": row.get("requested_asin"), "detail_asin": row.get("detail_asin"),
                       "final_asin": row.get("final_asin"), "parent_asin": row.get("parent_asin"),
                       "variation_family_asins": row.get("variation_family_asins"), "raw_source": row.get("raw_source")})
    elif stage == "offline_replay":
        common.update({"raw_source": row.get("raw_source"), "observed_source": row.get("observed_source"),
                       "ranking_contexts": row.get("ranking_contexts")})
    else:
        raise ValueError("unknown stage evidence: %s" % stage)
    return _hash(common)


def build_stage_evidence(master: Mapping[str, Any], stage: str) -> dict[str, Any]:
    """Create the non-secret references a real stage must carry forward.

    This is deliberately reproducible, not a signature scheme: release still
    recalculates it from the native Spanish Master and rejects an empty or
    ASIN-only PASS report.
    """
    if not verify_artifact_hash(master or {}):
        raise ValueError("stage evidence requires a valid Spanish Master")
    rows = _rows(master.get("records"))
    record_hashes = {normalize_asin(row.get("asin")): _master_record_hash(row, stage) for row in rows}
    if not record_hashes or "" in record_hashes:
        raise ValueError("stage evidence requires non-empty canonical ASIN rows")
    return {"evidence_version": STAGE_EVIDENCE_VERSION, "stage": stage,
            "master_artifact_hash": master.get("artifact_hash"), "record_count": len(record_hashes),
            "record_hashes": record_hashes,
            "schema_versions": {asin: {"detail_schema_version": row.get("detail_schema_version"),
                                         "ranking_schema_version": row.get("ranking_schema_version"),
                                         "parser_version": row.get("parser_version")}
                                for asin, row in ((normalize_asin(item.get("asin")), item) for item in rows)}}


def seal_artifact(kind: str, payload: Mapping[str, Any], *, version: str = "v1",
                  evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create the compact, hash-bound envelope expected by this gate.

    This is useful for adapters around existing reports which predate a common
    artifact wrapper.  Formal release refuses an unsealed report.
    """
    body = copy.deepcopy(dict(payload))
    evidence = copy.deepcopy(dict(evidence or {}))
    payload_hash = _hash(body)
    header = {"artifact_type": str(kind), "artifact_version": str(version),
              "payload_hash": payload_hash, "evidence": evidence}
    return {**header, "artifact_hash": _hash(header), "payload": body}


def verify_sealed_artifact(value: Any, kind: str) -> tuple[bool, Mapping[str, Any] | None, str]:
    if not isinstance(value, Mapping):
        return False, None, "MISSING_ARTIFACT"
    if value.get("artifact_type") != kind or not value.get("artifact_version"):
        return False, None, "ARTIFACT_TYPE_OR_VERSION_INVALID"
    payload = value.get("payload")
    if not isinstance(payload, Mapping) or not value.get("payload_hash") or not value.get("artifact_hash"):
        return False, None, "ARTIFACT_HASH_MISSING"
    header = {key: value.get(key) for key in ("artifact_type", "artifact_version", "payload_hash", "evidence")}
    if value.get("payload_hash") != _hash(payload) or value.get("artifact_hash") != _hash(header):
        return False, None, "ARTIFACT_HASH_INVALID"
    return True, payload, ""


def _fail(findings: list[dict[str, str]], stage: str, code: str, *, review: bool = False) -> None:
    findings.append({"stage": stage, "code": code,
                     "status": REVIEW_REQUIRED if review else BLOCKED})


def _report_pass(report: Mapping[str, Any], *, check: str, expected_evidence: Mapping[str, Any]) -> tuple[bool, str]:
    """Require a material report, not a seal wrapped around ``PASS``."""
    if report.get("check") != check or str(report.get("status") or "").upper() != "PASS":
        return False, "REPORT_NOT_PASS"
    if report.get("produced_stage") != check or report.get("report_schema_version") != STAGE_EVIDENCE_VERSION:
        return False, "REPORT_PROVENANCE_MISSING"
    if report.get("evidence_ref") != expected_evidence:
        return False, "REPORT_EVIDENCE_REF_MISMATCH"
    expected_asins = set(expected_evidence.get("record_hashes") or {})
    count = (report.get("summary") or {}).get("records_checked")
    if count is None or int(count) != len(expected_asins):
        return False, "EVIDENCE_SCOPE_MISMATCH"
    rows = _rows(report.get("records"))
    bound = {}
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if not asin or asin in bound or str(row.get("status") or "").upper() != "PASS":
            return False, "REPORT_RECORDS_INVALID"
        bound[asin] = row.get("record_hash")
    if bound != dict(expected_evidence.get("record_hashes") or {}):
        return False, "REPORT_RECORD_BINDING_MISMATCH"
    for row in report.get("issues") or ():
        if isinstance(row, Mapping) and str(row.get("severity") or "").upper() in {"P0", "P1"}:
            return False, "REPORT_HAS_P0_P1"
    return True, ""


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        value = value.get("records") or value.get("rows") or []
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, Iterable) and not isinstance(value, (str, bytes)) else []


def _asin_rows(rows: Iterable[Mapping[str, Any]], *, stage: str, findings: list[dict[str, str]]) -> tuple[list[dict[str, Any]], set[str]]:
    output, seen = [], set()
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if not asin:
            _fail(findings, stage, "EMPTY_ASIN")
            continue
        if asin in seen:
            _fail(findings, stage, "DUPLICATE_ASIN")
            continue
        copied = dict(row); copied["asin"] = asin
        output.append(copied); seen.add(asin)
    if not output:
        _fail(findings, stage, "EMPTY_ASIN_SET")
    return output, seen


def _source_gate_valid(master: Mapping[str, Any], rows: list[dict[str, Any]], audit: Mapping[str, Any],
                       gate: Mapping[str, Any]) -> bool:
    """Replay the source audit from Spanish Master's immutable raw evidence."""
    source_rows = []
    for row in rows:
        raw = row.get("raw_source")
        asin = row.get("asin")
        if not isinstance(raw, Mapping) or row.get("source_hash") != _hash(raw):
            return False
        source = copy.deepcopy(dict(raw))
        source["asin"] = asin
        source["ranking_contexts"] = copy.deepcopy(row.get("ranking_contexts") or [])
        source_rows.append(source)
    rebuilt = audit_source_fields(source_rows)
    return (canonical_audit_hash(rebuilt) == canonical_audit_hash(audit)
            and rebuilt.get("record_bindings") == audit.get("record_bindings")
            and master.get("source_audit_hash") == canonical_audit_hash(audit)
            and master.get("source_record_bindings_hash") == _hash(audit.get("record_bindings"))
            and master.get("source_gate_status") == gate.get("status")
            and verify_source_gate(audit, gate) and gate.get("status") == "SOURCE_READY")


def _candidate_hash(field: Mapping[str, Any]) -> str:
    return _hash({key: field.get(key) for key in ("asin", "field_type", "source_hash", "target_value", "context",
                                                   "dictionary_version", "translation_schema_version")})


def _translation_valid(payload: Mapping[str, Any], master_rows: list[dict[str, Any]],
                       chinese_rows: list[dict[str, Any]], findings: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    expected_asins = {row["asin"] for row in master_rows}
    master_by = {row["asin"]: row for row in master_rows}
    chinese_by = {row["asin"]: row for row in chinese_rows}
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    state = payload.get("state") if isinstance(payload.get("state"), Mapping) else payload
    candidate = state.get("release_candidate") or {}
    if candidate.get("release_status") != READY:
        _fail(findings, "translation", "TRANSLATION_NOT_COMPLETE")
    manifest = state.get("input_manifest") or {}
    if not manifest.get("dataset_hash") or int(manifest.get("record_count") or 0) != len(expected_asins):
        _fail(findings, "translation", "TRANSLATION_MANIFEST_INVALID")
    field_asins = set()
    for record in state.get("records") or []:
        if not isinstance(record, Mapping):
            continue
        asin = normalize_asin(record.get("asin")); field_asins.add(asin)
        if record.get("release_status") != READY:
            _fail(findings, "translation", "TRANSLATION_RECORD_NOT_READY")
        for field in record.get("fields") or []:
            if not isinstance(field, Mapping) or field.get("promotion_status") != "PROMOTED" or field.get("source_hash") in (None, ""):
                _fail(findings, "translation", "TRANSLATION_FIELD_INCOMPLETE")
    if field_asins != expected_asins:
        _fail(findings, "translation", "TRANSLATION_ASIN_SET_MISMATCH")
    for record in state.get("records") or []:
        if not isinstance(record, Mapping):
            continue
        asin = normalize_asin(record.get("asin"))
        for field in record.get("fields") or []:
            if not isinstance(field, Mapping):
                continue
            field_type = str(field.get("field_type") or "")
            candidate = dict(field); candidate["asin"] = asin
            if (not asin or not field_type or candidate.get("source_hash") != master_by.get(asin, {}).get("source_hash")
                    or not isinstance(candidate.get("context"), Mapping)
                    or candidate.get("target_value") in (None, "")
                    or not candidate.get("dictionary_version") or not candidate.get("translation_schema_version")
                    or candidate.get("candidate_hash") != _candidate_hash(candidate)
                    or chinese_by.get(asin, {}).get(field_type) != candidate.get("target_value")
                    or (asin, field_type) in candidates):
                _fail(findings, "translation", "TRANSLATION_CANDIDATE_BINDING_INVALID")
                continue
            candidates[(asin, field_type)] = candidate
    if not candidates:
        _fail(findings, "translation", "TRANSLATION_CANDIDATE_BINDING_INVALID")
    provenance = payload.get("provider_provenance") or {}
    provider = str(provenance.get("provider") or "").strip().lower()
    if (not provider or provider in {"fake", "test", "deterministic", "mock"}
            or not provenance.get("model") or int(provenance.get("request_count") or 0) <= 0
            or not provenance.get("verified")):
        _fail(findings, "translation", "REAL_PROVIDER_PROVENANCE_MISSING")
    return candidates


def _dictionary_valid(payload: Mapping[str, Any], translation: Mapping[str, Any],
                      expected_asins: set[str], findings: list[dict[str, str]]) -> None:
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), Mapping) else payload
    version = str(manifest.get("dictionary_version") or "")
    if not version or not manifest.get("dictionary_hash") or not manifest.get("translation_schema_version"):
        _fail(findings, "dictionary", "DICTIONARY_MANIFEST_INVALID")
        return
    promoted = manifest.get("promoted_dictionary") if "promoted_dictionary" in manifest else manifest.get("promoted_map")
    if not isinstance(promoted, Mapping):
        _fail(findings, "dictionary", "DICTIONARY_SYNC_INCOMPLETE")
    if payload.get("completed") is not True:
        _fail(findings, "dictionary", "DICTIONARY_SYNC_INCOMPLETE")
    state = translation.get("state") if isinstance(translation.get("state"), Mapping) else translation
    execution = translation.get("execution") or {}
    records = execution.get("records") or []
    if isinstance(records, Mapping):
        records = records.values()
    observed_asins = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        observed_asins.add(normalize_asin(record.get("asin")))
        for envelope in (record.get("fields") or {}).values():
            if isinstance(envelope, Mapping) and str(envelope.get("dictionary_version") or "") != version:
                _fail(findings, "dictionary", "DICTIONARY_VERSION_DRIFT")
    if records and observed_asins != expected_asins:
        _fail(findings, "dictionary", "DICTIONARY_EXECUTION_SCOPE_MISMATCH")
    rerender = payload.get("rerender") or {}
    if rerender.get("dictionary_version") not in (None, "", int(version) if version.isdigit() else version, version):
        _fail(findings, "dictionary", "RERENDER_VERSION_DRIFT")
    if rerender.get("selective_repair") or rerender.get("ready") is False:
        _fail(findings, "dictionary", "RERENDER_QA_INCOMPLETE", review=True)
    # State is read to ensure callers cannot submit a foreign execution blob.
    if not state.get("input_manifest"):
        _fail(findings, "dictionary", "TRANSLATION_STATE_MISSING")


def _chinese_qa_valid(payload: Mapping[str, Any], candidates: Mapping[tuple[str, str], Mapping[str, Any]],
                      dictionary: Mapping[str, Any], findings: list[dict[str, str]]) -> None:
    manifest = dictionary.get("manifest") if isinstance(dictionary.get("manifest"), Mapping) else dictionary
    dictionary_version = str(manifest.get("dictionary_version") or "")
    translation_schema_version = str(manifest.get("translation_schema_version") or "")
    qa_rows = _rows(payload.get("fields"))
    seen: set[tuple[str, str]] = set()
    for row in qa_rows:
        asin, field_type = normalize_asin(row.get("asin")), str(row.get("field_type") or "")
        key = (asin, field_type)
        candidate = candidates.get(key)
        if (not candidate or key in seen or str(row.get("status") or "").upper() != "PASS"
                or row.get("source_hash") != candidate.get("source_hash")
                or row.get("target_value") != candidate.get("target_value")
                or row.get("context") != candidate.get("context")
                or str(row.get("dictionary_version") or "") != dictionary_version
                or row.get("translation_schema_version") != candidate.get("translation_schema_version")
                or str(candidate.get("translation_schema_version") or "") != translation_schema_version
                or row.get("candidate_hash") != candidate.get("candidate_hash")):
            _fail(findings, "chinese_qa", "CHINESE_QA_FIELD_BINDING_INVALID")
        seen.add(key)
    if seen != set(candidates):
        _fail(findings, "chinese_qa", "CHINESE_QA_FIELD_COVERAGE_INCOMPLETE")


def _chinese_gate_valid(gate: Mapping[str, Any], qa: Mapping[str, Any], candidate_count: int) -> bool:
    return (gate.get("status") == "SKU_ZH_READY" and gate.get("produced_stage") == "chinese_gate"
            and gate.get("qa_payload_hash") == _hash(qa) and int(gate.get("fields_checked") or 0) == candidate_count)


def _field_closure_valid(report: Mapping[str, Any]) -> bool:
    for row in report.get("records") or report.get("issues") or ():
        if isinstance(row, Mapping) and str(row.get("severity") or "").upper() in {"P0", "P1"}:
            return False
    return bool(report.get("summary") or report.get("records"))


def _alignment(master: list[dict[str, Any]], spanish: list[dict[str, Any]], chinese: list[dict[str, Any]],
               findings: list[dict[str, str]]) -> list[dict[str, Any]]:
    master_asins = [row["asin"] for row in master]
    for stage, rows in (("spanish_output", spanish), ("chinese_output", chinese)):
        values = [row["asin"] for row in rows]
        if values != master_asins:
            _fail(findings, stage, "ASIN_SET_OR_ORDER_MISMATCH")
    by_master = {row["asin"]: row for row in master}
    chinese_by = {row["asin"]: row for row in chinese}
    canonical = []
    for asin in master_asins:
        raw = by_master[asin]; es = next((row for row in spanish if row["asin"] == asin), {})
        zh = chinese_by.get(asin, {})
        for field in ("product_url", "image_url"):
            if str(es.get(field) or "") != str(raw.get(field) or "") or str(zh.get(field) or "") != str(raw.get(field) or ""):
                _fail(findings, "bilingual_alignment", "URL_OR_IMAGE_MISMATCH")
        for note_key in ("notes", "remark", "remarks", "��ע"):
            if note_key in raw and (es.get(note_key) != raw.get(note_key) or zh.get(note_key) != raw.get(note_key)):
                _fail(findings, "bilingual_alignment", "HUMAN_NOTES_CHANGED")
        merged = copy.deepcopy(raw)
        for key, value in zh.items():
            if key.endswith("_zh"):
                merged[key] = copy.deepcopy(value)
        canonical.append(merged)
    return canonical


def evaluate_release_gate(artifacts: Mapping[str, Any], *, formal: bool = True,
                          debug: bool = False, force: bool = False) -> dict[str, Any]:
    """Verify all formal-release prerequisites without trusting caller flags."""
    findings: list[dict[str, str]] = []
    if not formal:
        return {"gate_version": RELEASE_GATE_VERSION, "status": DRAFT, "ready": False,
                "formal": False, "findings": [{"stage": "release", "code": "NON_FORMAL_DRAFT", "status": DRAFT}],
                "canonical_records": []}
    if debug:
        _fail(findings, "release", "DEBUG_FORMAL_FORBIDDEN")
    if force:
        _fail(findings, "release", "FORCE_BYPASS_FORBIDDEN")
    master = artifacts.get("spanish_master")
    if not verify_artifact_hash(master or {}):
        _fail(findings, "spanish_master", "MASTER_ARTIFACT_HASH_INVALID")
        master_rows = []
    else:
        master_rows = _rows(master.get("records"))
    master_rows, expected_asins = _asin_rows(master_rows, stage="spanish_master", findings=findings)

    sealed: dict[str, Mapping[str, Any]] = {}
    for key in ("ranking_authority", "source_audit", "source_gate", "detail_identity", "offline_replay",
                "field_closure", "translation", "dictionary_sync", "chinese_qa", "chinese_gate",
                "spanish_output", "chinese_output"):
        ok, payload, code = verify_sealed_artifact(artifacts.get(key), key)
        if not ok:
            _fail(findings, key, code)
        else:
            sealed[key] = payload
    source_audit = sealed.get("source_audit", {})
    source_gate = sealed.get("source_gate", {})
    if not _source_gate_valid(master or {}, master_rows, source_audit, source_gate):
        _fail(findings, "source_gate", "SOURCE_GATE_NOT_BOUND_READY")
    for key, check in (("ranking_authority", "ranking_authority"), ("detail_identity", "detail_identity"),
                       ("offline_replay", "offline_replay")):
        payload = sealed.get(key, {})
        try:
            expected_evidence = build_stage_evidence(master or {}, check)
        except (TypeError, ValueError):
            expected_evidence = {}
        ok, code = _report_pass(payload, check=check, expected_evidence=expected_evidence)
        if not ok:
            _fail(findings, key, code)
    closure = sealed.get("field_closure", {})
    if closure.get("check") != "field_closure" or not _field_closure_valid(closure):
        _fail(findings, "field_closure", "FIELD_CLOSURE_BLOCKED")
    spanish_rows, _ = _asin_rows(_rows(sealed.get("spanish_output")), stage="spanish_output", findings=findings)
    chinese_rows, _ = _asin_rows(_rows(sealed.get("chinese_output")), stage="chinese_output", findings=findings)
    translation = sealed.get("translation", {})
    candidates = _translation_valid(translation, master_rows, chinese_rows, findings)
    _dictionary_valid(sealed.get("dictionary_sync", {}), translation, expected_asins, findings)
    chinese_qa = sealed.get("chinese_qa", {})
    _chinese_qa_valid(chinese_qa, candidates, sealed.get("dictionary_sync", {}), findings)
    gate = sealed.get("chinese_gate", {})
    if not _chinese_gate_valid(gate, chinese_qa, len(candidates)):
        _fail(findings, "chinese_gate", "CHINESE_GATE_NOT_READY")
    canonical = _alignment(master_rows, spanish_rows, chinese_rows, findings)
    status = (REVIEW_REQUIRED if any(item["status"] == REVIEW_REQUIRED for item in findings)
              else BLOCKED if findings else READY)
    return {"gate_version": RELEASE_GATE_VERSION, "status": status, "ready": status == READY,
            "formal": True, "findings": findings, "canonical_records": canonical,
            "input_hash": _hash({key: artifacts.get(key) for key in sorted(artifacts)})}


def _validate_frozen_workbook(workbook: Any) -> None:
    """Fail closed unless the actual workbook retains the default 3-sheet contract."""
    expected_names = ["类目规划", "西班牙语选品清单", "中文选品清单"]
    if list(getattr(workbook, "sheetnames", ())) != expected_names:
        raise ValueError("FROZEN_EXPORT_SHEETS_INVALID")
    for name, headers in ((expected_names[1], HEAD_ES), (expected_names[2], HEAD_ZH)):
        sheet = workbook[name]
        actual = [sheet.cell(1, column).value for column in range(1, len(headers) + 1)]
        if sheet.max_column != len(headers) or actual != list(headers):
            raise ValueError("FROZEN_EXPORT_COLUMNS_INVALID:%s" % name)


def export_ready(artifacts: Mapping[str, Any], exporter: Callable[..., Any] | None, output_path: str, *,
                 formal: bool = True, debug: bool = False, force: bool = False) -> dict[str, Any]:
    """Run the formal gate and the real frozen workbook exporter.

    Compatibility exporters are allowed only when they return an in-memory
    workbook that passes the same three-sheet/26-column check.  A CLI
    ``--force`` flag is never accepted as a formal export bypass.
    """
    decision = evaluate_release_gate(artifacts, formal=formal, debug=debug, force=force)
    if not decision["ready"]:
        raise ValueError("PRODUCTION_RELEASE_NOT_READY:%s" % decision["status"])
    records = copy.deepcopy(decision["canonical_records"])
    result = (export_workbook(records, out_path=output_path, profile="research") if exporter is None
              else exporter(records, output_path=output_path))
    _validate_frozen_workbook(result)
    return {"decision": decision, "export_result": result}


__all__ = ["RELEASE_GATE_VERSION", "READY", "BLOCKED", "REVIEW_REQUIRED", "DRAFT",
           "seal_artifact", "verify_sealed_artifact", "build_stage_evidence", "evaluate_release_gate", "export_ready"]
