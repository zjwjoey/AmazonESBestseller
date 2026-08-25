import json
from pathlib import Path

from amazon_es_bestseller.category_discovery import CategoryNode
from amazon_es_bestseller.models import ProductSummary, RankingRecord
from amazon_es_bestseller.reports import (
    build_field_availability,
    duplicate_summary,
    write_category_tree,
    write_detail_field_availability,
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


def test_field_availability_includes_product_detail_fields_when_supplied():
    rows = build_field_availability(
        [RankingRecord(asin="B012345678")],
        [ProductSummary(asin="B012345678", details="brand: Casa")],
    )

    details_row = next(row for row in rows if row["field"] == "details")
    assert details_row["source"] == "products"
    assert details_row["records"] == 1
    assert details_row["non_null"] == 1


def test_detail_field_availability_reports_sample_presence(tmp_path: Path):
    path = write_detail_field_availability(
        [{"title": True, "brand": False}, {"title": True, "brand": True}],
        tmp_path / "detail_field_availability.csv",
    )

    content = path.read_text(encoding="utf-8-sig")
    assert "title,2,2,1.0,detail_pages" in content
    assert "brand,2,1,0.5,detail_pages" in content


def test_category_tree_preserves_depth_three_parentage(tmp_path: Path):
    nodes = [
        CategoryNode("Baño", "https://example.test/bano", "1", "Hogar y cocina", 2, "root"),
        CategoryNode("Accesorios", "https://example.test/accessories", "2", "Baño", 3, "bano"),
    ]
    csv_path = tmp_path / "category_tree.csv"
    json_path = tmp_path / "category_tree.json"

    write_category_tree(nodes, csv_path, json_path)

    assert "Hogar y cocina,Baño,Accesorios" in csv_path.read_text(encoding="utf-8-sig")
    tree = json.loads(json_path.read_text(encoding="utf-8"))
    assert tree["children"][0]["name"] == "Baño"
    assert tree["children"][0]["children"][0]["name"] == "Accesorios"


def test_duplicate_summary_excludes_missing_asin_from_duplicate_count():
    summary = duplicate_summary([
        RankingRecord(asin="B012345678"),
        RankingRecord(asin=None),
    ])

    assert summary["duplicate_records"] == 0
    assert summary["duplicate_rate"] == 0.0
    assert summary["missing_asin_records"] == 1
