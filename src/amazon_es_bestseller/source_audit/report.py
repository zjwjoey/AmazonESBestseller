"""Immutable source-audit delivery bundles and independently checked reads."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import RULE_VERSION
from .audit import audit_snapshot
from .contracts import csv_bytes, file_sha, jsonl, publish, read_json, separate, verify_bundle
from .loader import Snapshot
from .translation_prep import prepare, validate_preparation

ISSUE_FIELDS = ("candidate_id", "selection_id", "asin", "field_name", "issue_code", "severity", "audit_status",
                "current_value", "source_evidence_reference", "reason", "suggested_action", "offline_repair_allowed",
                "before", "after", "review_status")


def run_audit(source, out, *, expected_count=None):
    separate(source, out)
    snapshot = Snapshot(source)
    result = audit_snapshot(snapshot, expected_count=expected_count)
    rows = result["issues"]
    identity_queue = [row for row in rows if row["audit_status"] == "IDENTITY_REVIEW" or row["severity"] == "P0"]
    coverage_fields = ("field_name", "input_products", "valid", "missing", "source_not_displayed", "blocked_or_review")
    publish(out, {
        "source_audit_summary.json": result["summary"], "audited_products.jsonl": jsonl(result["products"]),
        "source_audit_issues.jsonl": jsonl(rows), "source_audit_issues.csv": csv_bytes(rows, ISSUE_FIELDS),
        "source_field_coverage.csv": csv_bytes(result["coverage"], coverage_fields),
        "identity_review_queue.csv": csv_bytes(identity_queue, ISSUE_FIELDS),
        "source_repair_candidates.jsonl": jsonl(result["repairs"]),
    }, kind="source_audit", inputs={"snapshot_manifest_sha256": snapshot.sha256,
                                     "snapshot_directory": str(snapshot.root)})
    snapshot.verify()
    return result["summary"]


def _audit_bundle(directory):
    directory = Path(directory)
    manifest, sha = verify_bundle(directory, kind="source_audit")
    return directory, manifest, sha


def bound_snapshot(manifest, out):
    if manifest.get("rule_version") != RULE_VERSION:
        raise ValueError("AUDIT_RULE_VERSION_REAUDIT_REQUIRED")
    source = Snapshot(manifest["inputs"]["snapshot_directory"])
    if source.sha256 != manifest["inputs"]["snapshot_manifest_sha256"]:
        raise ValueError("AUDIT_SNAPSHOT_BINDING_MISMATCH")
    separate(source.root, out)
    return source


def _read_lines(path):
    import json
    return [json.loads(line) for line in Path(path).read_text(encoding="utf8").splitlines() if line.strip()]


def repair_plan(audit, out):
    separate(audit, out)
    directory, manifest, sha = _audit_bundle(audit)
    source = bound_snapshot(manifest, out)
    candidates = _read_lines(directory / "source_repair_candidates.jsonl")
    issues = _read_lines(directory / "source_audit_issues.jsonl")
    plan = {
        "audit_manifest_sha256": sha, "automatic_offline_candidates": len(candidates),
        "automatic_means_proven_proposal_not_production_apply": True,
        "manual_review_issues": sum(not i["offline_repair_allowed"] and i["audit_status"] != "SOURCE_NOT_DISPLAYED" for i in issues),
        "issue_status_counts": dict(Counter(i["audit_status"] for i in issues)),
        "needs_followup_collection_proposals": read_json(directory / "source_audit_summary.json")["followup_collection_proposal_products"],
        "source_writes": 0, "production_apply": False, "requires_reaudit_before_translation": True,
    }
    publish(out, {"source_repair_plan.json": plan, "source_repair_candidates.jsonl": jsonl(candidates)},
            kind="source_repair_plan", inputs={"audit_manifest_sha256": sha, **manifest["inputs"]})
    source.verify()
    return plan


def prepare_translation(audit, out, *, cache=None):
    separate(audit, out)
    directory, manifest, sha = _audit_bundle(audit)
    # Revalidate underlying frozen evidence, not just a self-consistent report.
    source = bound_snapshot(manifest, out)
    cache_memory, cache_evidence = {}, None
    if cache:
        # Explicit optional *copied* cache only; never instantiate the historical
        # TranslationCache loader (its corruption recovery renames source files).
        cache = Path(cache).resolve()
        if cache.is_relative_to(source.root):
            raise ValueError("CACHE_MUST_BE_SEPARATE_COPIED_REVIEW_ARTIFACT")
        before = file_sha(cache)
        value = read_json(cache)
        if not isinstance(value, dict) or value.get("contract_version") != "source-audit-verified-memory-v1":
            raise ValueError("CACHE_PROTOCOL_NOT_VERIFIABLE_NO_AUTOMATIC_MIGRATION")
        cache_memory = value.get("entries", {})
        if not isinstance(cache_memory, dict):
            raise ValueError("INVALID_CACHE_ENTRIES")
        if file_sha(cache) != before:
            raise ValueError("CACHE_CHANGED_DURING_READ")
        cache_evidence = {"path": str(cache), "sha256": before}
    products = _read_lines(directory / "audited_products.jsonl")
    result = prepare(products, input_manifest_sha256=sha, cache_memory=cache_memory)
    validation = validate_preparation(result)
    contract = Path(__file__).resolve().parents[3] / "docs/source_audit/translation_field_contract.md"
    publish(out, {"translation_ready_products.jsonl": jsonl(result["products"]),
                  "translation_units.jsonl": jsonl(result["units"]),
                  "translation_preparation_summary.json": result["summary"],
                  "glossary_validation.json": result["glossary"],
                  "translation_contract_validation.json": validation,
                  "translation_field_contract.md": contract.read_bytes()},
            kind="translation_preparation", inputs={"audit_manifest_sha256": sha, **manifest["inputs"],
                                                     "optional_verified_cache_copy": cache_evidence})
    source.verify()
    return result["summary"]
