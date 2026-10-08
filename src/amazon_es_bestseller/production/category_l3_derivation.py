"""Hash-bound, offline L3 derivation from reviewed exact detail breadcrumbs."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .spanish_source_closure import (
    _hash, _audit_eligible_attribute_view, _validated_owner_attribute_exclusion_manifest,
    _append_owner_exclusion_trail, _validated_builder_decision_artifact, _merge_builder_unresolved_decisions,
)
from ..orchestration.translation_batch import file_hash
from ..quality.source_fields import audit_source_fields
from ..quality.source_gate import canonical_audit_hash, evaluate_source_gate, verify_source_gate
from ..translation.structured_contract import build_structured_translation_draft
from ..translation.production_contract import source_text
from ..translation.service import source_hash


def _record_map(records):
    rows = list(records)
    result = {str(row.get("asin") or ""): row for row in rows if isinstance(row, Mapping)}
    if not result or len(result) != len(rows) or any(len(asin) != 10 or not asin.isalnum() for asin in result):
        raise ValueError("L3_RECORD_ASIN_SCOPE_INVALID")
    return result


def load_reviewed_l3_artifact(path, *, expected_file_hash):
    """Read pinned review and independently hash-check all of its saved sources."""
    source = Path(path)
    if file_hash(source) != expected_file_hash:
        raise ValueError("L3_DECISION_ARTIFACT_HASH_MISMATCH")
    artifact = json.loads(source.read_text(encoding="utf-8"))
    assets, hashes = artifact.get("source_assets") or {}, artifact.get("source_hashes") or {}
    required = {"parent_master", "parent_manifest", "saved_details", "proposals", "legacy_support_only"}
    if not required <= assets.keys() or not required <= hashes.keys():
        raise ValueError("L3_SOURCE_ASSETS_MISSING")
    for key in required:
        if file_hash(assets[key]) != hashes[key]:
            raise ValueError(f"L3_SOURCE_ASSET_HASH_MISMATCH:{key}")
    old = json.loads(Path(assets["parent_master"]).read_text(encoding="utf-8"))["records"]
    manifest = json.loads(Path(assets["parent_manifest"]).read_text(encoding="utf-8"))
    if (manifest.get("dataset_canonical_hash") != _hash(old)
            or (manifest.get("artifacts") or {}).get("spanish_master_5480.json") != hashes["parent_master"]
            or artifact.get("scope", {}).get("parent_master_file_sha256") != hashes["parent_master"]):
        raise ValueError("L3_ORIGINAL_PARENT_MANIFEST_MISMATCH")
    details = json.loads(Path(assets["saved_details"]).read_text(encoding="utf-8"))
    proposals = json.loads(Path(assets["proposals"]).read_text(encoding="utf-8"))
    proposal_map = {row["asin"]: row for row in proposals}
    for item in artifact.get("decisions") or []:
        proposal = proposal_map.get(item.get("asin")) or {}
        if (proposal.get("detail_category_trail") != item.get("evidence", {}).get("proposal_trail")
                or proposal.get("proposed_category_l3") != item.get("derived_change", {}).get("to")):
            raise ValueError("L3_PROPOSAL_EVIDENCE_MISMATCH")
    return artifact, old, details


def rebind_l3_decisions(artifact, original_records, current_records, details, *, reviewed_artifact_file_hash=""):
    """Recheck original facts and exact current context before producing new bindings."""
    if artifact.get("schema_version") != "l3-backfill-decision-artifact-v1":
        raise ValueError("L3_DECISION_SCHEMA_INVALID")
    old, current, detail_map = _record_map(original_records), _record_map(current_records), _record_map(details)
    scope = artifact.get("scope") or {}
    if (set(old) != set(current) or scope.get("count") != len(current)
            or scope.get("asins_canonical_hash") != _hash(sorted(current))
            or scope.get("parent_master_dataset_canonical_hash") != _hash(list(original_records))):
        raise ValueError("L3_PARENT_SCOPE_HASH_MISMATCH")
    reviews = artifact.get("retained_review") or []
    review_map = {row["asin"]: row for row in reviews}
    if len(review_map) != len(reviews) or not set(review_map) <= set(current):
        raise ValueError("L3_RETAINED_REVIEW_SCOPE_INVALID")
    rows, skipped, seen = [], [], set()
    for item in artifact.get("decisions") or []:
        asin = item.get("asin")
        if asin not in current or asin in seen:
            raise ValueError("L3_DECISION_ASIN_INVALID")
        if asin in review_map:
            raise ValueError("L3_DECISION_REVIEW_OVERLAP")
        seen.add(asin)
        binding, evidence = item.get("field_binding") or {}, item.get("evidence") or {}
        original, record, detail = old[asin], current[asin], detail_map.get(asin) or {}
        proposed = item.get("derived_change", {}).get("to")
        trail = detail.get("detail_category_trail")
        if (item.get("decision") != "CANDIDATE_ONLY_NOT_APPLIED"
                or _hash(binding) != item.get("field_binding_hash") or _hash(evidence) != item.get("evidence_hash")
                or binding.get("asin") != asin or binding.get("field") != "category_l3"
                or binding.get("record_canonical_hash") != _hash(original)
                or binding.get("current_value") != original.get("category_l3")
                or binding.get("current_l1") != original.get("category_l1")
                or binding.get("current_l2") != original.get("category_l2")
                or original.get("category_l3") not in (None, "")
                or item.get("derived_change", {}).get("field") != "category_l3"
                or item.get("derived_change", {}).get("from") != original.get("category_l3")):
            raise ValueError(f"L3_ORIGINAL_DECISION_BINDING_INVALID:{asin}")
        if any(record.get(key) != original.get(key) for key in ("title_es_raw", "category_l1", "category_l2")):
            raise ValueError(f"L3_CURRENT_CONTEXT_CHANGED:{asin}")
        if any(not isinstance(record.get(key), str) or not record[key].strip()
               for key in ("title_es_raw", "category_l1", "category_l2")):
            raise ValueError(f"L3_CURRENT_CONTEXT_MISSING:{asin}")
        if (detail.get("asin") != asin or detail.get("identity_status_code") != "IDENTITY_MATCH"
                or detail.get("title_es_raw") != record.get("title_es_raw")
                or not isinstance(trail, list) or len(trail) < 3
                or trail[:2] != [record.get("category_l1"), record.get("category_l2")]
                or not isinstance(proposed, str) or not proposed.strip() or proposed != trail[2]
                or evidence.get("saved_detail_trail") != trail or evidence.get("proposal_trail") != trail
                or evidence.get("l3_position_zero_based") != 2 or evidence.get("proposed_l3") != proposed
                or binding.get("detail_trail_hash") != _hash(trail)
                or item.get("legacy_support_only", {}).get("supports_or_absent", True) is not True):
            raise ValueError(f"L3_DETAIL_EVIDENCE_CONFLICT:{asin}")
        if record.get("category_l3") not in (None, ""):
            skipped.append({"asin": asin, "reason": "CURRENT_L3_NONEMPTY", "old": record.get("category_l3"),
                            "proposed": proposed, "evidence": deepcopy(evidence), "decision": "SKIP_DERIVED_L3"})
            continue
        rows.append({"asin": asin, "parent_record_hash": _hash(record), "old": record.get("category_l3"),
                     "proposed": proposed, "decision": "APPLY_DERIVED_L3", "evidence": deepcopy(evidence),
                     "evidence_hash": _hash(evidence), "detail_record_hash": _hash(detail),
                     "original_binding": deepcopy(binding), "current_binding": {
                         "asin": asin, "field": "category_l3", "record_canonical_hash": _hash(record),
                         "title_es_raw": record.get("title_es_raw"), "current_l1": record.get("category_l1"),
                         "current_l2": record.get("category_l2"), "current_value": record.get("category_l3"),
                         "detail_trail_hash": _hash(trail)}})
    for asin, record in current.items():
        if asin not in seen:
            review = review_map.get(asin)
            if review and _hash(review.get("evidence") or {}) != review.get("evidence_hash"):
                raise ValueError("L3_RETAINED_REVIEW_EVIDENCE_HASH_INVALID")
            skipped.append({**deepcopy(review or {}), "asin": asin, "reason": review.get("reason") if review else
                            "EXISTING_L3_PRESERVED" if record.get("category_l3") not in (None, "") else
                            "NO_APPROVED_DECISION", "old": record.get("category_l3"), "decision": "SKIP_DERIVED_L3"})
    result = {"schema_version": "rebound-derived-l3-decisions-v1", "parent_dataset_hash": _hash(list(current_records)),
              "original_parent_dataset_hash": _hash(list(original_records)), "decisions": rows, "skipped": skipped,
              "reviewed_artifact_sha256": reviewed_artifact_file_hash}
    return {**result, "rebinding_hash": _hash(result)}


def apply_l3_decisions(records, rebound):
    """Modify exactly category_l3; hash all changes separately from immutable source facts."""
    parents = list(records)
    if _hash(parents) != rebound.get("parent_dataset_hash"):
        raise ValueError("L3_REBOUND_PARENT_HASH_MISMATCH")
    if _hash({key: value for key, value in rebound.items() if key != "rebinding_hash"}) != rebound.get("rebinding_hash"):
        raise ValueError("L3_REBOUND_MANIFEST_HASH_MISMATCH")
    decisions = {item["asin"]: item for item in rebound["decisions"]}
    output, changes = [], []
    for parent in parents:
        record = deepcopy(parent)
        decision = decisions.get(record["asin"])
        if decision:
            if record.get("category_l3") not in (None, "") or _hash(record) != decision["parent_record_hash"]:
                raise ValueError("L3_REBOUND_RECORD_INVALID")
            record["category_l3"] = decision["proposed"]
            changes.append({**deepcopy(decision), "new_record_hash": _hash(record),
                            "old_field_hash": source_hash(source_text(parent.get("category_l3"))),
                            "new_field_hash": source_hash(source_text(record["category_l3"]))})
        output.append(record)
    return {"records": output, "changes": changes, "skipped": deepcopy(rebound["skipped"]),
            "applied_count": len(changes), "parent_dataset_hash": _hash(parents), "derived_dataset_hash": _hash(output),
            "rebinding_hash": rebound["rebinding_hash"], "reviewed_artifact_sha256": rebound["reviewed_artifact_sha256"],
            "original_parent_dataset_hash": rebound["original_parent_dataset_hash"]}


def build_derived_l3_candidate(current, rebound, *, parent_authority_records=(), rankings=(),
                               owner_attribute_exclusion_manifest=None, builder_decision_artifact=None, progress=None):
    """Re-audit derived facts, retaining the approved raw-parent and exclusion chain."""
    if not verify_source_gate(current["source_audit"], current["source_gate"]) or not current["source_gate"].get("ready"):
        raise ValueError("L3_CURRENT_SOURCE_GATE_NOT_VERIFIED_READY")
    applied = apply_l3_decisions(current["records"], rebound)
    records = applied["records"]
    build_structured_translation_draft(records, review_item_ids=set(), authority_records=parent_authority_records)
    policy = current.get("source_exclusion_policy")
    exclusion = None
    if policy is not None:
        if _hash(list(parent_authority_records)) != policy.get("raw_dataset_canonical_hash"):
            raise ValueError("L3_RAW_PARENT_AUTHORITY_HASH_MISMATCH")
        exclusion = _validated_owner_attribute_exclusion_manifest(
            parent_authority_records, current["raw_source_audit"], owner_attribute_exclusion_manifest)
        if exclusion is None:
            raise ValueError("L3_APPROVED_ATTRIBUTE_EXCLUSION_REQUIRED")
    audit = audit_source_fields(_audit_eligible_attribute_view(records), ranking_matrix=list(rankings) or None,
                                progress=progress)
    builder_state = current.get("builder_unresolved_decisions")
    if isinstance(builder_state, Mapping):
        parent_hash = builder_state["parent_canonical_hash"]
        queue, state = _validated_builder_decision_artifact(builder_decision_artifact, parent_hash=parent_hash)
        locators = {(item["asin"], item["issue_code"], _hash(item["locator"])) for item in exclusion["entries"]} if exclusion else set()
        audit, builder_state = _merge_builder_unresolved_decisions(audit, _audit_eligible_attribute_view(records), queue,
            parent_hash=parent_hash, artifact_state=state, excluded_locators=locators)
    if exclusion:
        audit = _append_owner_exclusion_trail(audit, current["raw_source_audit"], exclusion)
    gate = evaluate_source_gate(audit)
    result = deepcopy(dict(current))
    result.update(records=records, source_audit=audit, current_source_audit=audit, closure_audit=audit,
                  source_gate=gate, current_source_gate=gate, source_audit_hash=canonical_audit_hash(audit),
                  status="CANDIDATE_CURRENT_GATE_READY" if gate["ready"] else "CANDIDATE_CURRENT_GATE_BLOCKED",
                  parent_authority_records=list(parent_authority_records), builder_unresolved_decisions=builder_state,
                  l3_derivation_manifest={key: value for key, value in applied.items() if key != "records"})
    if policy is not None:
        result["source_exclusion_policy"] = {**policy, "derived_dataset_canonical_hash": _hash(records),
                                             "effective_audit_hash": canonical_audit_hash(audit)}
    result["promotion_state"] = {"candidate": True, "reviewed_master": False, "eligible": gate["ready"]}
    result["source_review_queue"] = [dict(item, origin="current_field_audit") for item in audit.get("field_audits") or []
                                      if item.get("classification") not in {"PASS", "WARN"}]
    return result
