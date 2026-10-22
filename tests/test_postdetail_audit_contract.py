import copy
import hashlib
import json
import time
from types import SimpleNamespace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from amazon_es_bestseller.cli import main
from amazon_es_bestseller.qa.field_closure import audit_field_closure
from amazon_es_bestseller.qa.validators import validate_spec
from amazon_es_bestseller.quality.audit import run_quality_audit
from amazon_es_bestseller.quality.source_evidence import audit_detail_run_evidence
from amazon_es_bestseller.saved_evidence import SavedHtmlIndex, SavedEvidenceChanged
from amazon_es_bestseller.translation.production import build_release_translation_candidate

ASIN = "B075JJRFVV"


def test_source_stage_defers_chinese_without_changing_source_or_notes():
    product = {"asin": ASIN, "title_es_raw": "Botella de agua", "notes": "人工备注"}
    before = copy.deepcopy(product)
    source = audit_field_closure([product], stage="source")
    assert not [r for r in source["records"] if r["field"] == "title_zh" and r["severity"] == "P1"]
    translated = audit_field_closure([product], translations={ASIN: {"title_zh": "水瓶"}})
    assert not [r for r in translated["records"] if r["field"] == "title_zh" and r["severity"] == "P1"]
    missing = audit_field_closure([product])
    assert len([r for r in missing["records"] if r["field"] == "title_zh" and r["severity"] == "P1"]) == 1
    assert product == before


def test_metrics_count_unique_cells_and_source_gaps_are_not_errors():
    product = {"asin": ASIN, "current_price_raw": "10,00 EUR", "current_price": 999}
    report = audit_field_closure([product], stage="source")
    metrics = report["metrics"]
    assert metrics["error_cells"] == 1
    assert metrics["by_column"]["当前售价"]["error_rate"] == 1
    assert metrics["by_column"]["品牌"]["error_rate"] == 0
    assert metrics["by_column"]["品牌"]["blank_rate"] == 1
    assert metrics["error_rate"] == 1 / metrics["reviewed_cells"]


def test_selected_scope_counts_and_timing_and_progress(monkeypatch):
    from amazon_es_bestseller.quality import audit
    original = audit.audit_detail_structure
    clock = [100.0]
    monkeypatch.setattr(audit, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    class AuditClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 22, tzinfo=tz) + timedelta(seconds=clock[0] - 100)
    monkeypatch.setattr(audit, "datetime", AuditClock)
    def delayed(*args, **kwargs):
        clock[0] += 2.5
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, "audit_detail_structure", delayed)
    events = []
    report = run_quality_audit([], [{"asin": ASIN}, {"asin": "B000000001"}],
                               checks=["detail_structure"], asins=[ASIN], progress=events.append)
    assert report["summary"]["input_skus"] == 2
    assert report["summary"]["total_skus"] == report["summary"]["pass_skus"] == 1
    assert report["elapsed_seconds"] == 2.5
    assert report["started_at"] < report["completed_at"]
    assert report["final_quality_status"] == "PARTIAL_REVIEW"
    assert not report["gate"]["research_ready"]
    assert len(report["gate"]["missing_checks"]) == 7
    assert [r["event"] for r in events] == ["start", "check_start", "check_complete", "complete"]


def test_saved_html_shared_by_checks_and_scoped_before_reading(tmp_path):
    from amazon_es_bestseller.collection.detail import parse_detail_page
    html = Path("tests/fixtures/html/product_lunchbag.html").read_text(encoding="utf-8")
    for asin in (ASIN, "B000000001", "B000000002"):
        (tmp_path / (asin + ".html")).write_text(html.replace(ASIN, asin), encoding="utf-8")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    report = run_quality_audit([], [parse_detail_page(html, ASIN)], detail_html_dirs=[tmp_path],
                               asins=[ASIN], checks=["offline_replay", "field_closure"])
    assert report["summary"]["evidence_io"]["physical_html_reads"] == 1
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()} == before


def test_cache_is_bounded_and_changed_evidence_is_rejected(tmp_path):
    path = tmp_path / (ASIN + ".html")
    path.write_text("<html>one</html>", encoding="utf-8")
    cache = SavedHtmlIndex([tmp_path], max_bodies=1)
    cache.read(path)
    other = tmp_path / "other.html"
    other.write_text("<html>two</html>", encoding="utf-8")
    cache.read(other)
    assert len(cache._bodies) == 1
    path.write_text("<html>changed input</html>", encoding="utf-8")
    with pytest.raises(SavedEvidenceChanged):
        cache.read(path)


def test_progress_json_is_complete_and_separate_from_inputs(tmp_path):
    products = tmp_path / "products.json"
    products.write_text(json.dumps([{"asin": ASIN, "title_es_raw": "Botella"}]), encoding="utf-8")
    before = products.read_bytes()
    progress = tmp_path / "progress.json"
    assert main(["audit-fields", "--products", str(products), "--details", "",
                 "--rankings", "", "--stage", "source", "--out", str(tmp_path / "report.json"),
                 "--progress-json", str(progress)]) == 0
    assert json.loads(progress.read_text(encoding="utf-8"))["event"] == "complete"
    assert products.read_bytes() == before


@pytest.mark.parametrize("variation,spec", [("900 ml", "0,9 L"), ("9 L", "9升"), ("9 L", "9000毫升")])
def test_capacity_equivalent_units_are_accepted(variation, spec):
    _, issues = validate_spec({"asin": ASIN, "selected_variation_raw": variation, "spec_v2": spec})
    assert not issues


def test_request_metadata_is_not_reported_as_verified_content(tmp_path):
    path = tmp_path / "detail_execution_manifest.json"
    path.write_text(json.dumps({"records": [{"asin": ASIN, "requested_url": "https://www.amazon.es/dp/" + ASIN,
                                            "status_code": 200, "response_sha256": "abc"}]}), encoding="utf-8")
    report = audit_detail_run_evidence(tmp_path)
    assert report["unverified_request_metadata_count"] == 1
    assert report["verified_request_count"] == 0
    assert report["records"][0]["content_hash_verified"] is False
    assert report["source_audit_compatibility"]["formal_source_audit_pass"] is False


def test_matching_request_content_is_verified_and_tampering_is_not(tmp_path):
    html = tmp_path / "product.html"
    html.write_text("<html>source</html>", encoding="utf-8")
    manifest = {"records": [{"asin": ASIN, "requested_url": "https://www.amazon.es/dp/" + ASIN,
                            "status_code": 200, "saved_html_path": "product.html",
                            "response_sha256": hashlib.sha256(html.read_bytes()).hexdigest()}]}
    (tmp_path / "detail_execution_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert audit_detail_run_evidence(tmp_path)["verified_request_count"] == 1
    html.write_text("<html>changed</html>", encoding="utf-8")
    assert audit_detail_run_evidence(tmp_path)["verified_request_count"] == 0


def test_stale_promoted_empty_envelope_cannot_rebuild_ready_candidate():
    report = build_release_translation_candidate({"records": [{"asin": ASIN, "fields": [
        {"field": "title_es_raw", "target_field": "title_zh", "promotion_status": "PROMOTED", "final_zh": " "}
    ]}]})
    assert report["release_status"] != "READY"
    assert report["records"][0]["field_statuses"]["title_zh"] == "QA_BLOCKED"


def test_progress_cannot_overwrite_raw_input(tmp_path):
    products = tmp_path / "products.json"
    products.write_text(json.dumps([{"asin": ASIN}]), encoding="utf-8")
    before = products.read_bytes()
    with pytest.raises(SystemExit, match="进度文件不得覆盖输入证据"):
        main(["audit-fields", "--products", str(products), "--details", "", "--rankings", "",
              "--out", str(tmp_path / "report.json"), "--progress-json", str(products)])
    assert products.read_bytes() == before


def test_progress_cannot_write_under_raw_run_directory(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    products = tmp_path / "products.json"
    products.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="只读输入目录之外"):
        main(["audit-fields", "--products", str(products), "--details", "", "--rankings", "",
              "--run-dir", str(raw), "--out", str(tmp_path / "report.json"),
              "--progress-json", str(raw / "progress.json")])
    assert not list(raw.iterdir())


def test_unknown_translation_status_is_not_pass():
    from amazon_es_bestseller.translation.qa import build_qa_report
    report = build_qa_report([{"fields": {
        "title_zh": {"translation_status": "success"},
        "specification_zh": {"translation_status": "unrecognized"},
    }}])
    assert report["status"] != "pass"


def test_malformed_capacity_does_not_crash():
    _, issues = validate_spec({"selected_variation_raw": "1..2 L", "spec_v2": "1 L"})
    assert issues


def test_report_sha_comes_from_loaded_checkout_not_working_directory(tmp_path, monkeypatch):
    from amazon_es_bestseller.quality.report import _git_sha
    import subprocess
    root = Path(__file__).resolve().parents[1]
    expected = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.chdir(tmp_path)
    assert _git_sha() == expected
