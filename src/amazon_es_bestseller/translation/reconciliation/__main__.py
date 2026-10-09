"""Explicit-input offline CLI; no live translation or mutable-cache loader."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .diagnostics import benchmark
from .report import reconcile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Translation V2 reconciliation; no provider requests.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("reconcile")
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    perf = sub.add_parser("benchmark")
    perf.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "reconcile":
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        result = reconcile(config, args.out)
        print(json.dumps({key: result[key] for key in ("observation_state", "selected_unique_skus",
            "saved_result_unique_skus", "http_attempts", "original_http200_qa_counts", "conflict_counts")}, ensure_ascii=False))
    else:
        result = benchmark(args.out)
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
