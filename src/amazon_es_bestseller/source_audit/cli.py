"""Standalone offline Source Audit V1 and Translation Preparation V1."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import publish, read_json, separate, verify_bundle
from .loader import Snapshot, snapshot
from .report import prepare_translation, repair_plan, run_audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot", help="Create a double-hash-verified copy; never freeze/modify the collector")
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--data-kind", choices=["DETAIL5500", "HISTORICAL_REAL", "SYNTHETIC_TEST"], default="DETAIL5500")
    for name in ("inspect", "audit"):
        p = sub.add_parser(name)
        p.add_argument("--input", required=True, help="Frozen snapshot copy, not an active runtime directory")
        p.add_argument("--out", required=True)
        if name == "audit":
            p.add_argument("--expected-count", type=int)
    for name in ("repair-plan", "repair-copy", "prepare-translation"):
        p = sub.add_parser(name)
        p.add_argument("--audit", required=True)
        p.add_argument("--out", required=True)
        if name == "prepare-translation":
            p.add_argument("--cache-copy", help="Optional independently reviewed semantic-memory copy; no legacy cache writes")
    p = sub.add_parser("report")
    p.add_argument("--audit", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot(args.input, args.out, data_kind=args.data_kind)
            result = {"copied_files": len(result["files"]), "frozen_copy": True,
                      "consistency": result["consistency"], "data_kind": result["data_kind"]}
        elif args.command == "inspect":
            separate(args.input, args.out)
            source = Snapshot(args.input)
            result = {"snapshot_manifest_sha256": source.sha256, "task_count": len(source.tasks()),
                      "detail_rows": len(source.rows()), "copied_files": len(source.files),
                      "data_kind": source.manifest["data_kind"], "frozen_copy": True}
            publish(args.out, {"input_inspection.json": result}, kind="source_inspection",
                    inputs={"snapshot_manifest_sha256": source.sha256})
        elif args.command == "audit":
            if args.expected_count is not None and args.expected_count <= 0:
                raise ValueError("EXPECTED_COUNT_MUST_BE_POSITIVE")
            result = run_audit(args.input, args.out, expected_count=args.expected_count)
        elif args.command == "repair-plan":
            result = repair_plan(args.audit, args.out)
        elif args.command == "repair-copy":
            from .repair import repair_copy
            result = repair_copy(args.audit, args.out)
        elif args.command == "prepare-translation":
            result = prepare_translation(args.audit, args.out, cache=args.cache_copy)
        else:
            verify_bundle(args.audit, kind="source_audit")
            result = read_json(Path(args.audit) / "source_audit_summary.json")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": str(exc), "amazon_requests": 0,
                          "translation_api_requests": 0}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
