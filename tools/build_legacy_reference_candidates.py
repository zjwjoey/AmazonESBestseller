"""Create a diagnostic-only legacy Excel reference candidate artifact.

The historical workbooks are read-only inputs.  This tool deliberately does
not call a provider and never promotes historical Chinese into a release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amazon_es_bestseller.translation.legacy_reference import (  # noqa: E402
    build_legacy_reference_candidates,
    load_legacy_excel_reference,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-candidate", required=True, type=Path)
    parser.add_argument("--source-gate-manifest", required=True, type=Path)
    parser.add_argument("--spanish-reference", required=True, type=Path)
    parser.add_argument("--chinese-reference", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    inputs = (args.canonical_candidate, args.source_gate_manifest,
              args.spanish_reference, args.chinese_reference)
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing immutable input: " + ", ".join(missing))
    if args.output_dir.exists():
        raise FileExistsError("immutable output directory already exists: %s" % args.output_dir)

    candidate_payload = _read_json(args.canonical_candidate)
    records = candidate_payload.get("records") if isinstance(candidate_payload, dict) else candidate_payload
    if not isinstance(records, list):
        raise ValueError("CANONICAL_CANDIDATE_RECORDS_MISSING")
    source_gate_manifest = _read_json(args.source_gate_manifest)
    source_gate = source_gate_manifest.get("source_gate") or source_gate_manifest.get("current_source_gate") or {
        "status": source_gate_manifest.get("status", "UNKNOWN"),
    }
    references = load_legacy_excel_reference(args.spanish_reference, args.chinese_reference)
    artifact = build_legacy_reference_candidates(records, references, source_gate)
    artifact["source_binding"].update({
        "canonical_candidate_path": str(args.canonical_candidate),
        "canonical_candidate_file_hash": _sha256(args.canonical_candidate),
        "source_gate_manifest_path": str(args.source_gate_manifest),
        "source_gate_manifest_file_hash": _sha256(args.source_gate_manifest),
        "source_gate_dataset_canonical_hash": source_gate_manifest.get("dataset_canonical_hash", ""),
    })
    artifact["status"] = "DIAGNOSTIC_ONLY_SOURCE_GATE_BLOCKED" if str(source_gate.get("status", "")).upper() != "SOURCE_READY" else "DIAGNOSTIC_ONLY_LEGACY_REVIEW_REQUIRED"

    args.output_dir.mkdir(parents=True)
    output = args.output_dir / "legacy_reference_candidates.json"
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    qa_output = args.output_dir / "legacy_reference_qa_report.json"
    qa_output.write_text(json.dumps(artifact["qa_report"], ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": "legacy-reference-candidates-artifact-v1",
        "status": artifact["status"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_sha": _git_sha(),
        "artifact_file": output.name,
        "artifact_sha256": _sha256(output),
        "qa_report_file": qa_output.name,
        "qa_report_sha256": _sha256(qa_output),
        "candidate_count": len(artifact["candidates"]),
        "source_binding": artifact["source_binding"],
        "formal_release": "NOT_EXECUTED",
        "provider_calls": 0,
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "candidate_count": manifest["candidate_count"],
                      "output": str(args.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
