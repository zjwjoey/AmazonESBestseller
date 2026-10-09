"""Reviewed four-run input recipe; never starts, resumes or locks a live run.

Only the explicit output root and its manifest-bound project evidence are read.
All generated configuration and process receipts go to a fresh NEW directory.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amazon_es_bestseller.translation.reconciliation.evidence import EvidenceReader, digest, utc


RUNS = (
    "runtime_actual_canary_live_20261009_no_failover_da64ff2",
    "runtime_resume_A_20261009",
    "runtime_increment110_A_20261009",
    "runtime_increment850_ABC_plan_20261009",
)


def processes() -> dict:
    if os.name != "nt":
        return {"observed_at": utc(), "matching_process_alive": False, "verification": "UNKNOWN_NON_WINDOWS"}
    # No command line, credential, header or process environment is output.
    command = """$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Where-Object {
      $_.ProcessId -in @(4268,30552) -or ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'run_authorized_increment850.py')
    } | ForEach-Object { [pscustomobject]@{pid=$_.ProcessId; parent_pid=$_.ParentProcessId; name=$_.Name;
      created=$_.CreationDate.ToUniversalTime().ToString('o'); matches_task=($_.CommandLine -match 'run_authorized_increment850.py')}
    }) | ConvertTo-Json -Compress"""
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return {"observed_at": utc(), "matching_process_alive": True,
                "verification": "UNKNOWN_PROBE_FAILED", "probe_exit": proc.returncode}
    found = json.loads(proc.stdout or "[]")
    if isinstance(found, dict):
        found = [found]
    return {"observed_at": utc(), "processes": found,
            "matching_process_alive": any(row["matches_task"] for row in found), "verification": "READ_ONLY_CIM"}


def prepare(root: Path, out: Path) -> dict:
    root, out = root.resolve(), out.resolve()
    if out == root or root in out.parents or out in root.parents:
        raise ValueError("PREPARATION_OUTPUT_OVERLAPS_ORIGINAL")
    if out.exists():
        raise FileExistsError("PREPARATION_OUTPUT_ALREADY_EXISTS")
    repo = root.parents[2]
    roots = [str(root), str(repo / "runtime"), str(repo / "outputs")]
    reader = EvidenceReader(roots)
    before = processes()
    specs = [{"id": name, "directory": str(root / name)} for name in RUNS]
    cache = root / "runtime_actual_canary_binding" / "live" / "translation_cache.json"
    reader.read(cache)
    reader.read(root / RUNS[-1] / "runtime_events.jsonl", jsonl=True)
    for name in ("HTTP429_HALT.json", "progress.json", "final_manifest.json", "result.json"):
        reader.read(root / RUNS[-1] / name, optional=True)
    locks = []
    for path in (Path(str(cache) + ".claim.lock"), root / RUNS[-1] / "process_startup.lock"):
        if path.exists():
            reader.raw(path)
        locks.append({"path": str(path), "exists": path.exists(), "owner": "UNKNOWN_NOT_PROBED",
                      "meaning": "persistent file presence does not prove OS lock ownership"})
    selection_path = root / RUNS[-1] / "selection_1000.json"
    selection = reader.read(selection_path)
    review_dir = root / RUNS[2] / "offline_review_v1"
    derivatives = []
    supporting = []
    for path, dest in [(review_dir / "old40_reqa_v2.json", derivatives),
                       (review_dir / "new110_reqa_v2.json", derivatives),
                       (review_dir / "merged150_derivative_blockers.json", supporting),
                       (root / RUNS[2] / "issue_inventory_150_saved_state.json", supporting)]:
        if path.exists():
            reader.read(path)
            dest.append({"path": str(path), "sha256": reader.receipts[str(path)]["sha256"]})
    stable = reader.finish()
    after = processes()
    config = {"schema_version": "translation-reconciliation-input-v1", "allowed_roots": roots,
        "protected_roots": [str(root)], "batches": specs, "cache": str(cache),
        "selection": {"path": str(selection_path), "sha256": reader.receipts[str(selection_path)].get("sha256")},
        "derived_qa": derivatives, "supporting_evidence": supporting,
        "cohorts": {"saved_state": selection["prior150"],
                    "provisional_state": [row["asin"] if isinstance(row, dict) else row for row in selection["new850"]],
                    "merged_coverage": selection["cumulative1000"]},
        "runtime_observation": {"matching_process_alive": before["matching_process_alive"] or after["matching_process_alive"],
            "process_before": before, "process_after": after, "locks": locks,
            "observation_state": "ACTIVE_PROVISIONAL" if not stable or before["matching_process_alive"] or after["matching_process_alive"]
                                 else "STABLE_OBSERVED_NONATOMIC", "input_receipts": list(reader.receipts.values()),
            "conflicts": reader.conflicts, "atomic_cross_file_snapshot": False}}
    out.mkdir(parents=True)
    (out / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "preparation_manifest.json").write_text(json.dumps({"observed_at": utc(),
        "config_sha256": digest((out / "config.json").read_bytes()), "network_calls": 0,
        "modified_original_files": 0}, indent=2), encoding="utf-8")
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-output-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    config = prepare(args.original_output_root, args.out)
    print(json.dumps({"configuration": str(args.out / "config.json"),
                      "runtime": config["runtime_observation"]["observation_state"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
