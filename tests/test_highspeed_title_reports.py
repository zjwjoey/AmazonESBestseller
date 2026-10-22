"""Report titles are derived evidence; captured parser history stays immutable."""
import csv
import hashlib
import json
from pathlib import Path
import pytest

from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.reports import export_report
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.title_review import review_titles

URL = "https://www.amazon.es/gp/bestsellers"
TIME = "2026-10-14T01:00:00Z"


def captured(store, source, key, title="Fuente: producto de cocina"):
    html = ('<h1>Los más vendidos</h1><div id="zg" data-expected-count="1">'
            '<div class="ranking-card"><span class="zg-bdg-text">#1</span>'
            f'<a href="/product/{key}"><span data-product-title>{title}</span></a>'
            '</div></div>')
    evidence = store.save_html(html)
    parsed = parse_page(html, source=source, run_id="run", page_url=URL + "?pg=" + key,
        page_number=1, observed_at=TIME, evidence_path=evidence["html_path"], origin="OFFLINE_FIXTURE")
    # Reproduce the old selector's captured price-only title, without changing HTML.
    parsed["observations"][0]["product_title_raw"] = "19,99 €"
    store.commit_page("run", key, parsed["page"], parsed["observations"])


@pytest.fixture
def snapshot(tmp_path):
    store = RunStore(tmp_path / "store")
    source = {"source_id": "root", "source_type": "BEST_SELLERS", "page_urls": [URL], "actual_source_url": URL}
    store.begin_run(new_plan("run", sources=[source]), started_at=TIME)
    captured(store, source, "one")
    yield store, source
    store.close()


def read_rows(directory, name):
    return [json.loads(line) for line in (directory / (name + ".jsonl")).read_text(encoding="utf-8").splitlines()]


def test_reviewed_source_title_used_in_candidate_and_observation_csv_without_raw_mutation(snapshot, tmp_path):
    store, _ = snapshot
    before = list(store.iter_observations("run"))
    paths = {r["evidence_path"]: hashlib.sha256(Path(r["evidence_path"]).read_bytes()).hexdigest() for r in before}
    review = review_titles(store, "run", tmp_path / "reviews")
    result = export_report(store, "run", tmp_path / "reports", title_review_manifest=Path(review["directory"]) / "manifest.json")
    directory = Path(result["report_directory"])
    for name in ("observations", "unique_links", "eligible_target_links"):
        row = read_rows(directory, name)[0]
        assert row["product_title_raw"] == row["original_product_title_raw"] == "19,99 €"
        assert row["product_title_for_research"] == "Fuente: producto de cocina"
        assert row["title_review_status"] == "CORRECTED"
        assert row["title_review_evidence"]["source_html_sha256"] == before[0]["evidence_sha256"]
        with (directory / (name + ".csv")).open(encoding="utf-8-sig", newline="") as handle:
            assert list(csv.DictReader(handle))[0]["product_title_for_research"] == "Fuente: producto de cocina"
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["unique_link_candidates"] == 1
    assert summary["title_review"]["observation_status_counts"] == {"CORRECTED": 1}
    assert list(store.iter_observations("run")) == before
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha for p, sha in paths.items())


def test_new_rows_missing_review_are_pending_not_price_title_fallback(snapshot, tmp_path):
    store, source = snapshot
    review = review_titles(store, "run", tmp_path / "reviews")
    captured(store, source, "two", "Otra fuente")
    result = export_report(store, "run", tmp_path / "reports", title_review_manifest=Path(review["directory"]) / "manifest.json")
    rows = read_rows(Path(result["report_directory"]), "unique_links")
    pending = next(r for r in rows if r["raw_product_url"] == "/product/two")
    assert pending["title_review_status"] == "PENDING_REVIEW"
    assert pending["product_title_for_research"] is None
    assert pending["product_title_raw"] == "19,99 €"


def test_corrupt_projection_is_rejected_before_any_report_is_delivered(snapshot, tmp_path):
    store, _ = snapshot
    review = review_titles(store, "run", tmp_path / "reviews")
    artifact = Path(review["artifacts"]["title_corrections"]["path"])
    artifact.write_text(artifact.read_text(encoding="utf-8").replace("Fuente:", "Inventado:"), encoding="utf-8")
    with pytest.raises(ValueError):
        export_report(store, "run", tmp_path / "reports", title_review_manifest=Path(review["directory"]) / "manifest.json")
    assert not list((tmp_path / "reports").glob("report_*"))


def test_unverified_source_title_stays_blank_for_research_and_counts_unchanged(snapshot, tmp_path):
    store, source = snapshot
    captured(store, source, "blank", "")
    review = review_titles(store, "run", tmp_path / "reviews")
    plain = export_report(store, "run", tmp_path / "plain")
    reviewed = export_report(store, "run", tmp_path / "reviewed",
        title_review_manifest=Path(review["directory"]) / "manifest.json")
    rows = read_rows(Path(reviewed["report_directory"]), "unique_links")
    blank = next(r for r in rows if r["raw_product_url"] == "/product/blank")
    assert blank["title_review_status"] == "UNVERIFIED"
    assert blank["title_review_reason"]
    assert blank["product_title_for_research"] is None
    assert blank["product_title_raw"] == "19,99 €"
    original_summary = json.loads(Path(plain["summary_path"]).read_text(encoding="utf-8"))
    reviewed_summary = json.loads(Path(reviewed["summary_path"]).read_text(encoding="utf-8"))
    for key in ("raw_observations", "unique_link_candidates", "normalized_unique_url_count",
                "independent_purchasable_skus_confirmed", "dedup_status_counts"):
        assert original_summary[key] == reviewed_summary[key]


def test_ordinary_reports_keep_existing_schema_and_title_without_projection(snapshot, tmp_path):
    store, _ = snapshot
    result = export_report(store, "run", tmp_path / "reports")
    row = read_rows(Path(result["report_directory"]), "unique_links")[0]
    assert row["product_title_raw"] == "19,99 €"
    assert "product_title_for_research" not in row
    assert "title_review_status" not in row
