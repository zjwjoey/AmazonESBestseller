"""Apply deterministic reparse candidates to a new derived copy, never raw."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ..multisource.storage import digest
from .contracts import jsonl, publish, separate, verify_bundle
from .loader import Snapshot
from .report import _read_lines, bound_snapshot


def repair_copy(audit, out):
    separate(audit, out)
    audit = Path(audit)
    manifest, sha = verify_bundle(audit, kind="source_audit")
    if manifest['inputs'].get('repair_directory'):
        raise ValueError('NESTED_REPAIR_REQUIRES_SEPARATE_REVIEW')
    source = bound_snapshot(manifest, out)
    products = _read_lines(audit / "audited_products.jsonl")
    candidates = _read_lines(audit / "source_repair_candidates.jsonl")
    by_candidate = {p["candidate_id"]: p for p in products}
    if len(by_candidate) != len(products):
        raise ValueError("REPAIR_CANDIDATE_ID_CONFLICT")
    derived = {cid: deepcopy(p["raw_record"]) for cid, p in by_candidate.items()}
    ledger = []
    for item in candidates:
        if not item.get("safe_copy_eligible"):
            continue
        cid, field = item["candidate_id"], item["field_name"]
        if cid not in derived or not by_candidate[cid]["identity_passed"]:
            raise ValueError("REPAIR_IDENTITY_NOT_VERIFIED")
        if item["proof"] != "EXACT_HTML_CURRENT_EXISTING_PARSER" or digest(derived[cid].get(field)) != item["source_value_sha256"]:
            raise ValueError("REPAIR_PREIMAGE_MISMATCH")
        if digest(item["after"]) != item["repaired_value_sha256"]:
            raise ValueError("REPAIR_POSTIMAGE_MISMATCH")
        derived[cid][field] = item["after"]
        ledger.append({**item, "application_status": "DERIVED_COPY_ONLY_REAUDIT_REQUIRED", "production_apply": False})
    output = [{"candidate_id": cid, "selection_id": p["selection_id"], "asin": p["asin"],
               "source_record": p["raw_record"], "derived_record": derived[cid],
               "preparation_status": "NOT_REAUDITED", "source_evidence_reference": p["source_evidence_reference"]}
              for cid, p in by_candidate.items()]
    summary = {"input_products": len(products), "repaired_fields_in_new_copy": len(ledger),
               "repaired_products": len({i["candidate_id"] for i in ledger}),
               "reaudit_required": True, "translation_authorized": False, "production_apply": False}
    publish(out, {"source_repaired_products.jsonl": jsonl(output),
                  "source_repair_application_ledger.jsonl": jsonl(ledger), "repair_summary.json": summary},
            kind="source_repair_copy", inputs={"audit_manifest_sha256": sha, **manifest["inputs"],
                                              "audit_directory": str(audit.resolve())})
    source.verify()
    return summary


def verify_repair(repair):
    """Rebuild the exact permitted patch set from the immutable original audit."""
    from .audit import audit_snapshot
    repair = Path(repair).resolve()
    manifest, sha = verify_bundle(repair, kind='source_repair_copy')
    audit = Path(manifest['inputs']['audit_directory'])
    original, audit_sha = verify_bundle(audit, kind='source_audit')
    if original['inputs'].get('repair_directory'):
        raise ValueError('NESTED_REPAIR_REQUIRES_SEPARATE_REVIEW')
    source = bound_snapshot(original, repair)
    if audit_sha != manifest['inputs']['audit_manifest_sha256'] or any(
            manifest['inputs'].get(k) != original['inputs'][k] for k in ('snapshot_directory', 'snapshot_manifest_sha256')):
        raise ValueError('REPAIR_AUDIT_BINDING_MISMATCH')
    from . import RULE_VERSION
    if manifest.get('rule_version') != RULE_VERSION:
        raise ValueError('REPAIR_RULE_VERSION_REAUDIT_REQUIRED')
    fresh = audit_snapshot(source)
    if fresh['products'] != _read_lines(audit/'audited_products.jsonl') or fresh['repairs'] != _read_lines(audit/'source_repair_candidates.jsonl'):
        raise ValueError('REPAIR_ORIGINAL_AUDIT_NOT_REPRODUCIBLE')
    expected = {p['candidate_id']: deepcopy(p['raw_record']) for p in fresh['products']}
    ledger = []
    for candidate in fresh['repairs']:
        if candidate.get('safe_copy_eligible'):
            cid, field = candidate['candidate_id'], candidate['field_name']
            if digest(expected[cid].get(field)) != candidate['source_value_sha256']:
                raise ValueError('REPAIR_PREIMAGE_MISMATCH')
            expected[cid][field] = candidate['after']
            ledger.append({**candidate, 'application_status': 'DERIVED_COPY_ONLY_REAUDIT_REQUIRED', 'production_apply': False})
    if ledger != _read_lines(repair/'source_repair_application_ledger.jsonl'):
        raise ValueError('REPAIR_LEDGER_MISMATCH')
    rows = _read_lines(repair/'source_repaired_products.jsonl')
    wanted = [{'candidate_id': p['candidate_id'], 'selection_id': p['selection_id'], 'asin': p['asin'],
               'source_record': p['raw_record'], 'derived_record': expected[p['candidate_id']],
               'preparation_status': 'NOT_REAUDITED', 'source_evidence_reference': p['source_evidence_reference']}
              for p in fresh['products']]
    if rows != wanted:
        raise ValueError('REPAIR_DERIVED_RECORD_MISMATCH')
    return source, expected, sha


def reaudit_repair(repair, out):
    """Re-audit derived values against original HTML without forging checkpoints."""
    from .audit import audit_snapshot
    from .report import publish_audit_result
    separate(repair, out)
    source, overrides, repair_sha = verify_repair(repair)
    separate(source.root, out)
    separate(source.manifest['source_root'], out)
    manifest, _ = verify_bundle(repair, kind='source_repair_copy')
    separate(manifest['inputs']['audit_directory'], out)
    result = audit_snapshot(source, _record_overrides=overrides)
    for product in result['products']:
        product['source_evidence_reference']['repair_manifest_sha256'] = repair_sha
    result['summary']['derived_reaudit'] = True
    result['summary']['repair_manifest_sha256'] = repair_sha
    source.verify()
    publish_audit_result(result, out, inputs={
        'snapshot_manifest_sha256': source.sha256, 'snapshot_directory': str(source.root),
        'repair_directory': str(Path(repair).resolve()), 'repair_manifest_sha256': repair_sha,
        'original_audit_manifest_sha256': manifest['inputs']['audit_manifest_sha256']})
    return result['summary']
