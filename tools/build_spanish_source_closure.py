"""Build an offline 5,480-ASIN Spanish evidence candidate.

This intentionally does not call ``build_spanish_master`` and cannot create a
``SOURCE_READY`` promotion.  It reads saved production evidence only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# The repository's project environment may be anchored to a read-only
# production worktree.  Make this development worktree's source explicit.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amazon_es_bestseller.production.spanish_source_closure import (
    build_spanish_source_candidate,
    write_spanish_source_candidate,
)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-candidate-hash", required=True)
    args = parser.parse_args()
    root = args.production_root
    audit_path = root / "source_audit_5480" / "source_audit_5480.json"
    snapshot_path = root / "ranking_snapshot" / "latest_authoritative_snapshot.json"
    inputs = {
        "candidate_manifest": root / "candidate_manifest.json",
        "details": root / "details.json",
        "rankings": root / "rankings.json",
        "source_audit": audit_path,
        "ranking_run_report": root / "ranking_run_report.json",
        "detail_run_report": root / "detail_run_report.json",
        "ranking_snapshot": snapshot_path,
    }
    missing = [str(path) for path in inputs.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing saved production evidence: " + ", ".join(missing))
    ranking_report = _read_json(inputs["ranking_run_report"])
    detail_report = _read_json(inputs["detail_run_report"])
    snapshot = _read_json(snapshot_path)
    # Keep complete evidence files immutable at their input paths and bind them
    # by hash.  Avoid copying their full contents into every product record.
    provenance = {
        "production_root": str(root),
        "input_sha256": {name: _sha256(path) for name, path in inputs.items()},
        "ranking_snapshot": {key: snapshot.get(key) for key in ("snapshot_id", "status", "captured_at", "canonical_hash")},
        "ranking_run_report": {key: ranking_report.get(key) for key in ("task_id", "batch_id", "phase", "status", "code_head")},
        "detail_run_report": {key: detail_report.get(key) for key in ("task_id", "batch_id", "phase", "status", "code_head")},
    }
    result = build_spanish_source_candidate(
        _read_json(inputs["candidate_manifest"]),
        _read_json(inputs["details"]),
        _read_json(inputs["rankings"]),
        _read_json(audit_path),
        expected_candidate_hash=args.expected_candidate_hash,
        cache_root=root,
        snapshot_provenance=provenance,
    )
    manifest = write_spanish_source_candidate(args.output_dir, result)
    print(json.dumps({
        "output_dir": str(args.output_dir), "status": result["status"],
        "scope_count": result["binding_scope"]["count"],
        "source_gate": {key: result["source_gate"].get(key) for key in ("status", "ready", "audit_hash")},
        "closure_source_gate": {key: result["closure_source_gate"].get(key) for key in ("status", "ready", "audit_hash")},
        "manifest_artifacts": manifest["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
