"""One-shot offline stage logger for the current-owner-scope gate diagnostic."""
from __future__ import annotations

import argparse
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
    load_builder_unresolved_decision_artifact,
    write_spanish_source_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-root", required=True, type=Path)
    parser.add_argument("--parent-root", required=True, type=Path)
    parser.add_argument("--owner-scope", required=True, type=Path)
    parser.add_argument("--builder-queue", required=True, type=Path)
    parser.add_argument("--builder-manifest", required=True, type=Path)
    parser.add_argument("--owner-attribute-exclusion-manifest", type=Path,
                        help="approved, hash-bound derived-attribute exclusion manifest")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    log_path = args.output_dir / "stage.log"

    def stage(name: str, **extra) -> None:
        payload = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": name, **extra}
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()

    def load(name: str, path: Path):
        stage(f"LOAD_{name}_START", path=str(path), bytes=path.stat().st_size)
        value = json.loads(path.read_text(encoding="utf-8"))
        stage(f"LOAD_{name}_DONE", item_count=len(value) if isinstance(value, list) else None)
        return value

    exit_code = 1
    try:
        stage("PYTHON_READY", version=sys.version.split()[0])
        candidates = load("CANDIDATES", args.production_root / "candidate_manifest.json")
        details = load("DETAILS", args.production_root / "details.json")
        rankings = load("RANKINGS", args.production_root / "rankings.json")
        parent = load("PARENT", args.parent_root / "spanish_master_5480.json")["records"]
        scope = load("OWNER_SCOPE", args.owner_scope / "owner_exclusions.json")
        history = load("HISTORICAL_AUDIT", args.parent_root / "historical_source_audit.json")
        owner_attribute_exclusion_manifest = None
        if args.owner_attribute_exclusion_manifest is not None:
            owner_attribute_exclusion_manifest = load("OWNER_ATTRIBUTE_EXCLUSION_MANIFEST",
                                                       args.owner_attribute_exclusion_manifest)
        stage("BUILDER_ARTIFACT_VERIFY_START", queue=str(args.builder_queue), manifest=str(args.builder_manifest))
        builder_artifact = load_builder_unresolved_decision_artifact(args.builder_queue, args.builder_manifest)
        stage("BUILDER_ARTIFACT_VERIFY_DONE", domain=builder_artifact["artifact_domain"],
              queue_hash=builder_artifact["queue_canonical_hash"], queue_sha256=builder_artifact["queue_sha256"],
              manifest_hash=builder_artifact["manifest_canonical_hash"])
        stage("HASH_START")
        hashes = {"candidate_manifest": _hash(candidates), "details": _hash(details), "rankings": _hash(rankings)}
        stage("HASH_DONE", hashes=hashes)
        stage("CURRENT_GATE_BUILD_START")
        result = build_current_source_gate_candidate(
            candidates, details, rankings, parent, scope, expected_input_hashes=hashes,
            historical_source_audit=history, cache_root=args.production_root, progress=stage,
            builder_decision_artifact=builder_artifact,
            owner_attribute_exclusion_manifest=owner_attribute_exclusion_manifest,
        )
        try:
            git_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            git_sha = "UNKNOWN"
        result["code_versions"] = {
            "git_sha": git_sha,
            "python": sys.version.split()[0],
            "current_gate_schema_version": result.get("schema_version"),
            "source_field_audit": "ranking-matrix-v1",
        }
        stage("CURRENT_GATE_BUILD_DONE", status=result["status"], gate=result["current_source_gate"]["status"])
        stage("WRITE_START")
        write_spanish_source_candidate(args.output_dir / "candidate", result)
        stage("WRITE_DONE")
        exit_code = 0
    except BaseException as exc:
        stage("EXCEPTION", type=type(exc).__name__, message=str(exc))
        traceback.print_exc()
    finally:
        stage("PROCESS_EXIT", exit_code=exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
