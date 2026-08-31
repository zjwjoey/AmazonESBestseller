# -*- coding: utf-8 -*-
"""Offline coverage check for category-quota tasks; never contacts Amazon."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _rows(path: str) -> list[dict]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("records", value.get("items", []))
    return value if isinstance(value, list) else []


def assess(task: dict, records: list[dict]) -> dict:
    categories = task.get("categories", [])
    counts = Counter(str(r.get("category_group") or "").strip() for r in records
                     if str(r.get("asin") or "").strip())
    groups = []
    for category in categories:
        group = category.get("category_group", "")
        quota = int(category.get("quota", 0) or 0)
        observed = counts.get(group, 0)
        groups.append({"category_group": group, "quota": quota,
                       "observed": observed,
                       "shortfall": max(quota - observed, 0),
                       "ready": observed >= quota})
    return {"task_id": task.get("task_id", ""),
            "target_unique": int(task.get("target_unique", 0) or 0),
            "observed_unique": len({str(r.get("asin")).strip().upper() for r in records
                                    if str(r.get("asin") or "").strip()}),
            "ready": all(g["ready"] for g in groups),
            "groups": groups}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = assess(json.loads(Path(args.task).read_text(encoding="utf-8")),
                    _rows(args.manifest))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
