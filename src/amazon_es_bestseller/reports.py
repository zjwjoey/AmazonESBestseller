import csv
from dataclasses import asdict, fields
from pathlib import Path

from .models import ProductSummary, RankingRecord


def _write_dicts(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)
    return path


def write_ranking_csv(records: list[RankingRecord], path: Path) -> Path:
    return _write_dicts([asdict(record) for record in records], path)


def write_products_csv(products: list[ProductSummary], path: Path) -> Path:
    return _write_dicts([asdict(product) for product in products], path)


def build_field_availability(records: list[RankingRecord]) -> list[dict]:
    total = len(records)
    rows = []
    for field in fields(RankingRecord):
        name = field.name
        non_null = sum(getattr(record, name) is not None for record in records)
        rows.append(
            {
                "field": name,
                "records": total,
                "non_null": non_null,
                "null": total - non_null,
                "availability_rate": round(non_null / total, 4) if total else 0.0,
                "source": "ranking_records",
            }
        )
    return rows


def write_field_availability_csv(records: list[RankingRecord], path: Path) -> Path:
    return _write_dicts(build_field_availability(records), path)


def duplicate_summary(records: list[RankingRecord]) -> dict[str, float | int]:
    unique_asins = len({record.asin for record in records if record.asin})
    duplicate_records = max(len(records) - unique_asins, 0)
    return {
        "ranking_records": len(records),
        "unique_asins": unique_asins,
        "duplicate_records": duplicate_records,
        "duplicate_rate": round(duplicate_records / len(records), 4) if records else 0.0,
    }
