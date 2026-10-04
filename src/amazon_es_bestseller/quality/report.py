"""Write independent, human-readable quality artifacts."""
from __future__ import annotations

import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_CHECK_FILES = {
    "ranking_identity": "ranking_identity_audit.json",
    "ranking_integrity": "ranking_integrity_audit.json",
    "access_evidence": "access_evidence_audit.json",
    "detail_identity": "detail_identity_audit.json",
    "offline_replay": "offline_replay_audit.json",
    "detail_structure": "detail_structure_audit.json",
    "category_provenance": "category_provenance_audit.json",
    "field_closure": "field_closure_audit.json",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_quality_report(report: Mapping, output_root: str | Path) -> dict[str, Any]:
    """Persist a report without touching any input evidence files."""
    run_id = str(report.get("run_id") or
                 "quality_%sZ" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    target = Path(output_root) / run_id
    target.mkdir(parents=True, exist_ok=False)
    checks = report.get("checks") or {}
    for check, name in _CHECK_FILES.items():
        if check in checks:
            _json_write(target / name, checks[check])
    issues = list(report.get("issues") or [])
    _json_write(target / "quality_issues.json", issues)
    fields = ["asin", "stage", "check", "severity", "status", "issue_code",
              "message", "source_file", "evidence"]
    with (target / "quality_issues.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in issues:
            record = dict(row)
            record["evidence"] = json.dumps(record.get("evidence") or {}, ensure_ascii=False,
                                             sort_keys=True)
            writer.writerow(record)
    summary = dict(report.get("summary") or {})
    _json_write(target / "summary.json", summary)
    base_manifest = {
        "run_id": run_id,
        "git_sha": str(report.get("git_sha") or _git_sha()),
        "profile": str(report.get("profile") or "stable-research"),
        "ranking_parser": str(report.get("ranking_parser") or "V1"),
        "detail_parser": str(report.get("detail_parser") or "V1"),
        "quality_gate_version": str(report.get("quality_contract_version") or "1"),
        "quality_checks_enabled": list(report.get("quality_checks_enabled") or checks.keys()),
        "source_urls": list(report.get("source_urls") or []),
        "planned_pages": list(report.get("planned_pages") or []),
        "started_at": str(report.get("started_at") or _now()),
        "completed_at": str(report.get("completed_at") or _now()),
        "network_mode": str(report.get("network_mode") or "OFFLINE_QUALITY_AUDIT"),
        "network_requests": 0,
        "final_quality_status": str(report.get("final_quality_status") or "BLOCKED"),
        "source_hashes": dict(report.get("source_hashes") or {}),
    }
    _json_write(target / "quality_manifest.json", base_manifest)
    _json_write(target / "run_manifest.json", base_manifest)
    return {"path": target, "manifest": base_manifest, "summary": summary,
            "issues": issues}
