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
            kind="source_repair_copy", inputs={"audit_manifest_sha256": sha, **manifest["inputs"]})
    source.verify()
    return summary
