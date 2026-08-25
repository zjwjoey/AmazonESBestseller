import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse

from amazon_es_bestseller.continuation import apply_detail_to_row
from amazon_es_bestseller.detail_parser import parse_detail_page


def _asin_from_url(url: str) -> str | None:
    parts = urlparse(url).path.rstrip("/").split("/")
    return parts[-1].upper() if len(parts) >= 3 and parts[-2] == "dp" else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair product fields from already saved detail HTML")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    with (args.run_dir / "products.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for field in ("parent_asin_status", "image_path", "image_download_status", "image_download_error"):
        for row in rows:
            row.setdefault(field, "")
    by_asin = {row["asin"]: row for row in rows}
    repaired = 0
    with (args.run_dir / "access_events.csv").open(encoding="utf-8", newline="") as handle:
        events = list(csv.DictReader(handle))
    for page_number, event in enumerate(events, start=1):
        asin = _asin_from_url(event["requested_url"])
        path = args.run_dir / "html" / f"page_{page_number:02d}.html"
        if not asin or asin not in by_asin or event["access_state"] != "NORMAL" or not path.exists():
            continue
        apply_detail_to_row(by_asin[asin], parse_detail_page(path.read_text(encoding="utf-8"), asin))
        repaired += 1
    output = args.run_dir / "products_repaired.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print({"reparsed_saved_details": repaired, "output": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
