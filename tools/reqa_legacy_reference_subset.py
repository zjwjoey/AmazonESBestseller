"""Re-QA a bounded legacy-reference subset without provider calls."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amazon_es_bestseller.translation.protection import protect
from amazon_es_bestseller.translation.qa import qa_field


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--asins", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("immutable output exists: %s" % args.output_dir)
    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    selected = {value.strip().upper() for value in args.asins.split(",") if value.strip()}
    rows = []
    for candidate in payload.get("candidates") or []:
        if str(candidate.get("asin") or "").upper() not in selected:
            continue
        before = candidate.get("qa") or {}
        after = qa_field(protect(str(candidate.get("legacy_es") or "")),
                         str(candidate.get("legacy_zh") or ""),
                         str(candidate.get("legacy_es") or ""),
                         field=str(candidate.get("field") or ""))
        rows.append({"asin": candidate.get("asin"), "field": candidate.get("field"),
                     "field_hash": candidate.get("field_hash"), "before": before, "after": after,
                     "changed": before != after})
    args.output_dir.mkdir(parents=True)
    output = {"schema_version": "legacy-reference-subset-reqa-v1", "provider_calls": 0,
              "asins": sorted(selected), "rows": rows,
              "summary": {"rows": len(rows), "changed": sum(row["changed"] for row in rows),
                          "pass_after": sum(row["after"].get("qa_status") == "pass" for row in rows)}}
    (args.output_dir / "legacy_reference_subset_reqa.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
