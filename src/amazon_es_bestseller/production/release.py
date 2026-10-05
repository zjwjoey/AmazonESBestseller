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
from ..quality.source_gate import verify_source_gate
from .spanish_master import verify_artifact_hash


RELEASE_GATE_VERSION = "production-release-gate-v1"
READY = "READY"
BLOCKED = "BLOCKED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
DRAFT = "DRAFT"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


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


def _report_pass(report: Mapping[str, Any], *, check: str, expected_asins: set[str],
                 require_scope: bool = True) -> tuple[bool, str]:
    if report.get("check") != check or str(report.get("status") or "").upper() != "PASS":
        return False, "REPORT_NOT_PASS"
    for row in report.get("issues") or ():
        if isinstance(row, Mapping) and str(row.get("severity") or "").upper() in {"P0", "P1"}:
            return False, "REPORT_HAS_P0_P1"
    if require_scope:
        count = (report.get("summary") or {}).get("records_checked")
        if count is not None and int(count) != len(expected_asins):
            return False, "EVIDENCE_SCOPE_MISMATCH"
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


def _translation_valid(payload: Mapping[str, Any], expected_asins: set[str], findings: list[dict[str, str]]) -> None:
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
    provenance = payload.get("provider_provenance") or {}
    provider = str(provenance.get("provider") or "").strip().lower()
    if (not provider or provider in {"fake", "test", "deterministic", "mock"}
            or not provenance.get("model") or int(provenance.get("request_count") or 0) <= 0
            or not provenance.get("verified")):
        _fail(findings, "translation", "REAL_PROVIDER_PROVENANCE_MISSING")


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
    if not verify_source_gate(source_audit, source_gate) or source_gate.get("status") != "SOURCE_READY":
        _fail(findings, "source_gate", "SOURCE_GATE_NOT_BOUND_READY")
    for key, check in (("ranking_authority", "ranking_authority"), ("detail_identity", "detail_identity"),
                       ("offline_replay", "offline_replay")):
        payload = sealed.get(key, {})
        ok, code = _report_pass(payload, check=check, expected_asins=expected_asins)
        if not ok:
            _fail(findings, key, code)
    closure = sealed.get("field_closure", {})
    if closure.get("check") != "field_closure" or not _field_closure_valid(closure):
        _fail(findings, "field_closure", "FIELD_CLOSURE_BLOCKED")
    translation = sealed.get("translation", {})
    _translation_valid(translation, expected_asins, findings)
    _dictionary_valid(sealed.get("dictionary_sync", {}), translation, expected_asins, findings)
    chinese_qa = sealed.get("chinese_qa", {})
    qa_rows = _rows(chinese_qa.get("fields"))
    qa_asins = {normalize_asin(row.get("asin")) for row in qa_rows}
    if not qa_rows or qa_asins != expected_asins or any(str(row.get("status") or "") != "PASS" for row in qa_rows):
        _fail(findings, "chinese_qa", "CHINESE_QA_NOT_READY")
    gate = sealed.get("chinese_gate", {})
    if gate.get("status") != "SKU_ZH_READY":
        _fail(findings, "chinese_gate", "CHINESE_GATE_NOT_READY")
    spanish_rows, _ = _asin_rows(_rows(sealed.get("spanish_output")), stage="spanish_output", findings=findings)
    chinese_rows, _ = _asin_rows(_rows(sealed.get("chinese_output")), stage="chinese_output", findings=findings)
    canonical = _alignment(master_rows, spanish_rows, chinese_rows, findings)
    status = (REVIEW_REQUIRED if any(item["status"] == REVIEW_REQUIRED for item in findings)
              else BLOCKED if findings else READY)
    return {"gate_version": RELEASE_GATE_VERSION, "status": status, "ready": status == READY,
            "formal": True, "findings": findings, "canonical_records": canonical,
            "input_hash": _hash({key: artifacts.get(key) for key in sorted(artifacts)})}


def export_ready(artifacts: Mapping[str, Any], exporter: Callable[..., Any], output_path: str, *,
                 formal: bool = True, debug: bool = False, force: bool = False) -> dict[str, Any]:
    """Run the gate internally before handing canonical records to an exporter."""
    decision = evaluate_release_gate(artifacts, formal=formal, debug=debug, force=force)
    if not decision["ready"]:
        raise ValueError("PRODUCTION_RELEASE_NOT_READY:%s" % decision["status"])
    result = exporter(copy.deepcopy(decision["canonical_records"]), output_path=output_path)
    return {"decision": decision, "export_result": result}


__all__ = ["RELEASE_GATE_VERSION", "READY", "BLOCKED", "REVIEW_REQUIRED", "DRAFT",
           "seal_artifact", "verify_sealed_artifact", "evaluate_release_gate", "export_ready"]
