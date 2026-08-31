# -*- coding: utf-8 -*-
"""Offline synthetic scale benchmark; never contacts Amazon or DeepSeek."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:  # ``resource`` is POSIX-only; the benchmark also runs on Windows.
    import resource
except ImportError:  # pragma: no cover - exercised on Windows hosts
    resource = None

from amazon_es_bestseller.export.excel import export_workbook


def build_records(count: int) -> list[dict]:
    return [{
        "asin": f"B{i:09d}",
        "title_es_raw": f"Producto sintético {i}",
        "current_price_raw": "9,99 €",
        "product_url": f"https://www.amazon.es/dp/B{i:09d}",
    } for i in range(1, count + 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--offline", action="store_true", required=True,
                        help="required safety flag; this benchmark is synthetic only")
    parser.add_argument("--out-dir", default="outputs/benchmarks/scale_5000")
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("--count must be positive")

    records = build_records(args.count)
    start = time.perf_counter()
    wb = export_workbook(records, profile="business")
    elapsed = time.perf_counter() - start
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = out_dir / "synthetic_business.xlsx"
    wb.save(workbook_path)
    rss_kb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
              if resource is not None else None)
    report = {
        "synthetic": True,
        "count": args.count,
        "profile": "business",
        "sheetnames": wb.sheetnames,
        "rows_per_sheet": {name: wb[name].max_row for name in wb.sheetnames},
        "elapsed_seconds": round(elapsed, 3),
        "max_rss_kb": rss_kb,
        "workbook": str(workbook_path),
    }
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
