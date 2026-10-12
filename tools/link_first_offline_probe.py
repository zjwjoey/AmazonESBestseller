"""Read archived HTML across categories; no network or historical data writes."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amazon_es_bestseller.collection.detail import parse_detail_page  # noqa: E402
from amazon_es_bestseller.orchestration.link_first import file_hash, immutable_json  # noqa: E402
from amazon_es_bestseller.quality.staged_fields import audit_detail_fields, resolve_selected_identity  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-category", type=int, default=4)
    args = parser.parse_args()
    paths = []
    rng = random.Random(20261010)
    for category in sorted((args.source_dir / "categories").iterdir()):
        available = sorted((category / "detail_cache" / "html").glob("*.html"))
        paths.extend(rng.sample(available, min(args.per_category, len(available))))
    for name in ("B000CRBEJ2", "B0CMY7VF71", "B0943QDPBC", "B0CSP8DBVR", "B0DCGHLHMM", "B08BYLMK7C", "B08JJ8LJDS"):
        paths.extend((args.source_dir / "categories").glob("*/detail_cache/html/" + name + ".html"))
    records, statuses, issues = [], Counter(), Counter()
    for path in dict.fromkeys(paths):
        before = file_hash(path)
        html = path.read_text(encoding="utf-8")
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        url = "https://www.amazon.es/dp/" + path.stem
        identity = resolve_selected_identity(html, meta.get("requested_url") or url, meta.get("final_url") or url)
        row = parse_detail_page(html, identity["asin"])
        result = audit_detail_fields(html, row, identity)
        statuses[result["status"]] += 1
        issues.update(i["code"] for i in result["issues"])
        assert before == file_hash(path)
        records.append({"archived_asin": path.stem, "html": str(path), "html_sha256": before,
                        "identity": identity, "audit": result})
    report = {"scope": len(records), "category_count": len({Path(r["html"]).parents[2].name for r in records}),
              "statuses": dict(statuses), "issues": dict(issues), "records": records,
              "network_requests": 0, "input_hashes_unchanged": True}
    immutable_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
