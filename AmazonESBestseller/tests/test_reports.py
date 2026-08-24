from pathlib import Path

from amazon_es_bestseller.models import RankingRecord
from amazon_es_bestseller.reports import (
    build_field_availability,
    write_ranking_csv,
)


def test_ranking_csv_preserves_duplicate_appearances(tmp_path: Path):
    records = [RankingRecord(asin="B012345678", rank=1), RankingRecord(asin="B012345678", rank=3)]
    path = write_ranking_csv(records, tmp_path / "ranking_records.csv")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_field_availability_reports_null_counts():
    records = [RankingRecord(asin="B012345678", title="one"), RankingRecord(asin=None, title=None)]
    rows = build_field_availability(records)
    asin_row = next(row for row in rows if row["field"] == "asin")
    assert asin_row["records"] == 2
    assert asin_row["non_null"] == 1
    assert asin_row["null"] == 1
