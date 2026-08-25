import argparse
import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from amazon_es_bestseller.cli import _live_probe
from amazon_es_bestseller.continuation import apply_detail_to_row, select_missing_detail_rows
from amazon_es_bestseller.detail_parser import parse_detail_page
from amazon_es_bestseller.models import AccessState, ProbeEvent
from amazon_es_bestseller.run_store import RunStore


def _asin_from_url(url: str) -> str | None:
    parts = urlparse(url).path.rstrip("/").split("/")
    return parts[-1].upper() if len(parts) >= 3 and parts[-2] == "dp" else None


def _load_events(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_availability(run_dir: Path, ranking_rows: list[dict[str, str]], product_rows: list[dict[str, str]], details) -> None:
    result = []
    for source, rows in (("ranking_records", ranking_rows), ("products", product_rows)):
        for field in rows[0]:
            non_null = sum(bool(row.get(field)) for row in rows)
            result.append({"field": field, "records": len(rows), "non_null": non_null, "null": len(rows) - non_null, "availability_rate": round(non_null / len(rows), 4), "source": source})
    for field in ("rating", "review_count", "seller", "fulfilled_by", "ean", "gtin", "upc"):
        present = sum(bool(detail.candidate_fields.get(field)) for detail in details)
        result.append({"field": field, "records": len(details), "non_null": present, "null": len(details) - present, "availability_rate": round(present / len(details), 4) if details else 0.0, "source": "detail_candidates"})
    with (run_dir / "field_availability.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result[0]))
        writer.writeheader(); writer.writerows(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reparse saved product details and collect only missing details")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    with (args.run_dir / "products.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for field in ("parent_asin_status", "image_path", "image_download_status", "image_download_error"):
        for row in rows:
            row.setdefault(field, "")
    by_asin = {row["asin"]: row for row in rows}
    details = []
    for page_number, event in enumerate(_load_events(args.run_dir / "access_events.csv"), start=1):
        asin = _asin_from_url(event["requested_url"])
        if not asin or event["access_state"] != AccessState.NORMAL.value:
            continue
        html_path = args.run_dir / "html" / f"page_{page_number:02d}.html"
        if not html_path.exists() or asin not in by_asin:
            continue
        detail = parse_detail_page(html_path.read_text(encoding="utf-8"), asin)
        apply_detail_to_row(by_asin[asin], detail)
        details.append(detail)
    missing = select_missing_detail_rows(rows)
    continuation_store = RunStore.create(
        args.run_dir / "continuations",
        datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    events: list[ProbeEvent] = []
    if missing:
        events = _live_probe(
            continuation_store,
            [row["product_url"] for row in missing],
            args.delay,
            args.headless,
        )
        for page_number, event in enumerate(events, start=1):
            if event.access_state is not AccessState.NORMAL:
                break
            asin = _asin_from_url(event.requested_url)
            if not asin or asin not in by_asin:
                continue
            detail = parse_detail_page((continuation_store.html_dir / f"page_{page_number:02d}.html").read_text(encoding="utf-8"), asin)
            apply_detail_to_row(by_asin[asin], detail)
            details.append(detail)
    continuation_store.close()
    fields = list(rows[0])
    with (args.run_dir / "products.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    with (args.run_dir / "ranking_records.csv").open(encoding="utf-8-sig", newline="") as handle:
        rankings = list(csv.DictReader(handle))
    _write_availability(args.run_dir, rankings, rows, details)
    states = ", ".join(event.access_state.value for event in events) or "offline only"
    (args.run_dir / "detail_completion_report.md").write_text(
        f"# Detail continuation\n\n- Saved details reparsed: {len(details) - len(events)}\n- Newly requested details: {len(events)}\n- Continuation access states: {states}\n- Products with detail JSON: {sum(bool(row['details_json']) for row in rows)} / {len(rows)}\n",
        encoding="utf-8",
    )
    print({"requested": len(events), "complete_products": sum(bool(row["details_json"]) for row in rows)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
