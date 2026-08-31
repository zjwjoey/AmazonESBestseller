# -*- coding: utf-8 -*-
"""Offline coverage check for category-quota tasks; never contacts Amazon."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from amazon_es_bestseller.collection.quota import normalize_group


def _rows(path: str) -> list[dict]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("records", value.get("items", []))
    return value if isinstance(value, list) else []


def assess(task: dict, records: list[dict]) -> dict:
    categories = task.get("categories", [])
    group_asins = defaultdict(set)
    asin_groups = defaultdict(set)
    asin_seen = Counter()
    for record in records:
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        group = normalize_group(record.get("category_group") or record.get("group"))
        if not asin:
            continue
        asin_seen[asin] += 1
        if group:
            group_asins[group].add(asin)
            asin_groups[asin].add(group)
    conflicts = sorted(asin for asin, groups in asin_groups.items() if len(groups) > 1)
    duplicates = sorted(asin for asin, count in asin_seen.items() if count > 1)
    groups = []
    for category in categories:
        group = normalize_group(category.get("category_group") or category.get("group"))
        quota = int(category.get("quota", 0) or 0)
        observed = len(group_asins[group])
        eligible = len(group_asins[group] - set(conflicts))
        groups.append({"category_group": group, "quota": quota,
                       "observed": observed, "eligible_observed": eligible,
                       "shortfall": max(quota - eligible, 0),
                       "ready": eligible >= quota})
    return {"task_id": task.get("task_id", ""),
            "target_unique": int(task.get("target_unique", 0) or 0),
            "observed_unique": len(asin_seen),
            "duplicate_asins": duplicates,
            "cross_group_conflicts": conflicts,
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
