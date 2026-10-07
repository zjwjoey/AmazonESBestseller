"""Build a verified builder-decision artifact and consume it in one offline run.

The input production evidence and the r3 closure stay read-only.  A new output
root contains the builder artifact, a chained 5,478 owner scope, and the
current-gate result.  If a run stops after the builder checkpoint, rerunning
the exact same command resumes from that immutable checkpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amazon_es_bestseller.production.spanish_source_closure import (  # noqa: E402
    _hash,
    build_current_source_gate_candidate,
    build_spanish_source_candidate,
    derive_owner_excluded_scope_with_parent_chain,
    load_builder_unresolved_decision_artifact,
    write_owner_excluded_scope,
    write_spanish_source_candidate,
)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-root", required=True, type=Path)
    parser.add_argument("--r3-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-candidate-hash", required=True)
    args = parser.parse_args()

    output = args.output_dir
    checkpoint_path = output / "checkpoint.json"
    if output.exists() and not checkpoint_path.is_file():
        raise FileExistsError(f"immutable run output already exists without a resumable checkpoint: {output}")
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / "stage.log"

    def stage(name: str, **extra) -> None:
        payload = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": name, **extra}
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()

    exit_code = 1
    try:
        root, r3 = args.production_root, args.r3_root
        inputs = {
            "candidate_manifest": root / "candidate_manifest.json",
            "details": root / "details.json",
            "rankings": root / "rankings.json",
            "source_audit": root / "source_audit_5480" / "source_audit_5480.json",
            "ranking_run_report": root / "ranking_run_report.json",
            "detail_run_report": root / "detail_run_report.json",
            "ranking_snapshot": root / "ranking_snapshot" / "latest_authoritative_snapshot.json",
            "r3_parent": r3 / "spanish_master_5480.json",
            "r3_manifest": r3 / "manifest.json",
        }
        missing = [str(path) for path in inputs.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError("missing frozen input: " + ", ".join(missing))
        raw_input_sha256 = {name: _sha256(path) for name, path in inputs.items()}
        candidates, details, rankings, source_audit = (_read(inputs[name]) for name in ("candidate_manifest", "details", "rankings", "source_audit"))
        snapshot, ranking_report, detail_report = (_read(inputs[name]) for name in ("ranking_snapshot", "ranking_run_report", "detail_run_report"))
        r3_parent_payload, r3_manifest = _read(inputs["r3_parent"]), _read(inputs["r3_manifest"])
        old_records = r3_parent_payload.get("records") if isinstance(r3_parent_payload, dict) else None
        if not isinstance(old_records, list):
            raise ValueError("frozen r3 parent has no record list")
        code_sha = _git_sha()
        provenance = {
            "production_root": str(root), "raw_input_sha256": raw_input_sha256,
            "ranking_snapshot": {key: snapshot.get(key) for key in ("snapshot_id", "status", "captured_at", "canonical_hash")},
            "ranking_run_report": {key: ranking_report.get(key) for key in ("task_id", "batch_id", "phase", "status", "code_head")},
            "detail_run_report": {key: detail_report.get(key) for key in ("task_id", "batch_id", "phase", "status", "code_head")},
            "r3_parent_manifest_hash": raw_input_sha256["r3_manifest"],
        }
        checkpoint = _read(checkpoint_path) if checkpoint_path.is_file() else {}
        builder_dir = output / "builder"
        if builder_dir.is_dir() and checkpoint.get("stage") in {"BUILDER_WRITTEN", "OWNER_SCOPE_WRITTEN"}:
            stage("BUILDER_RESUME_START")
            builder_payload = _read(builder_dir / "spanish_master_5480.json")
            builder_records = builder_payload.get("records")
            if not isinstance(builder_records, list):
                raise ValueError("builder checkpoint has no records")
            builder_manifest = _read(builder_dir / "manifest.json")
            stage("BUILDER_RESUME_DONE", records=len(builder_records))
        else:
            stage("BUILDER_START", records=5480)
            builder_result = build_spanish_source_candidate(
                candidates, details, rankings, source_audit,
                expected_candidate_hash=args.expected_candidate_hash, cache_root=root, snapshot_provenance=provenance,
            )
            builder_result["code_versions"] = {"git_sha": code_sha, "builder_rules_version": "builder-unresolved-decisions-v1"}
            builder_manifest = write_spanish_source_candidate(
                builder_dir, builder_result,
                builder_artifact_metadata={"raw_input_sha256": raw_input_sha256, "code_sha": code_sha},
            )
            builder_records = builder_result["records"]
            checkpoint_path.write_text(json.dumps({
                "stage": "BUILDER_WRITTEN", "builder_manifest_hash": _hash(builder_manifest),
                "builder_parent_dataset_canonical_hash": builder_manifest["dataset_canonical_hash"],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            stage("BUILDER_DONE", records=len(builder_records), queue_hash=builder_manifest["builder_unresolved_decision_artifact"]["expected_queue_canonical_hash"])

        stage("OWNER_SCOPE_START")
        scope = derive_owner_excluded_scope_with_parent_chain(
            builder_records, frozen_parent_records=old_records, frozen_parent_manifest=r3_manifest,
        )
        scope_dir = output / "owner_scope"
        if scope_dir.exists():
            saved_scope = _read(scope_dir / "owner_exclusions.json")
            if _hash(saved_scope) != _hash(scope):
                raise ValueError("resumed owner scope does not match newly validated r3 parent chain")
            scope_manifest = _read(scope_dir / "manifest.json")
            stage("OWNER_SCOPE_RESUME_DONE", effective_scope=scope["effective_scope"]["record_count"])
        else:
            scope_manifest = write_owner_excluded_scope(scope_dir, scope)
            checkpoint_path.write_text(json.dumps({
                "stage": "OWNER_SCOPE_WRITTEN", "builder_manifest_hash": _hash(builder_manifest),
                "owner_scope_manifest_hash": _hash(scope_manifest),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        stage("OWNER_SCOPE_DONE", effective_scope=scope["effective_scope"]["record_count"], parent_chain=scope["parent_chain"])

        stage("BUILDER_ARTIFACT_VERIFY_START")
        artifact = load_builder_unresolved_decision_artifact(
            builder_dir / "builder_unresolved_decisions.json", builder_dir / "builder_unresolved_decisions.manifest.json",
        )
        stage("BUILDER_ARTIFACT_VERIFY_DONE", queue_hash=artifact["queue_canonical_hash"], queue_sha256=artifact["queue_sha256"])
        stage("CURRENT_GATE_START")
        hashes = {"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(rankings)}
        current = build_current_source_gate_candidate(
            candidates, details, rankings, builder_records, scope, expected_input_hashes=hashes,
            historical_source_audit=source_audit, cache_root=root,
            snapshot_provenance={**provenance, "builder_artifact_manifest_hash": artifact["manifest_canonical_hash"]},
            builder_decision_artifact=artifact,
        )
        current["code_versions"] = {"git_sha": code_sha, "builder_rules_version": "builder-unresolved-decisions-v1"}
        current_manifest = write_spanish_source_candidate(output / "current_gate", current)
        state = {
            "schema_version": "builder-current-gate-run-v1", "status": current["status"],
            "builder_manifest": builder_manifest,
            "owner_scope_manifest": scope_manifest, "current_gate_manifest": current_manifest,
            "current_gate": current["current_source_gate"], "parent_chain": scope["parent_chain"],
            "raw_input_sha256": raw_input_sha256, "code_sha": code_sha,
        }
        (output / "run_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        stage("CURRENT_GATE_DONE", status=current["status"], gate=current["current_source_gate"]["status"])
        exit_code = 0
    except BaseException as exc:
        stage("EXCEPTION", type=type(exc).__name__, message=str(exc))
        traceback.print_exc()
    finally:
        stage("PROCESS_EXIT", exit_code=exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
