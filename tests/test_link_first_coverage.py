import json

import pytest

from test_link_first_runner import FIXTURE, Session, runner


def test_current_batch_identity_exclusion_preserves_fresh_evidence(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "selection_eligible", lambda row, link: False, raising=False)
    link = {"snapshot_item_id": "excluded", "product_link": "https://www.amazon.es/dp/B000CRBEJ2",
            "research_category": "a", "ranking_source_url": "https://www.amazon.es/gp/bestsellers/tools/123",
            "ranking_page_number": 1, "bestseller_rank": 1, "category_trail": ["Hogar"]}
    run._detail(Session([(200, FIXTURE)]), link, run.plan["categories"][0])
    result = json.loads((run.output / "detail_evidence/excluded/result.json").read_text())
    assert result["status"] == "SUCCESS"
    assert result["record"]["asin"] == "B000CRBEJ2"
    assert result["selected"] is False
    assert result["selection_excluded"] is True
    assert not run.selected
    assert (run.output / result["html_file"]).exists()
    resumed = runner(tmp_path, monkeypatch, resume=True)
    assert not resumed.selected


def coverage(tmp_path, monkeypatch, *, resume=False):
    from amazon_es_bestseller.monitoring.link_first_coverage import CoverageRunner
    seed = tmp_path / "current.json"
    if not seed.exists():
        seed.write_text(json.dumps([{"asin": "B000CRBEJ2", "title_es_raw": "Never reuse me"}]))
    (tmp_path / "setup").mkdir(exist_ok=True)
    setup = runner(tmp_path / "setup", monkeypatch,
                   resume=(tmp_path / "setup/run/run_metadata.json").exists())
    return CoverageRunner(setup.plan_path, tmp_path / "coverage", tmp_path,
                          exclude_current_details=[seed], sample_size=2, resume=resume)


def test_coverage_excludes_only_confirmed_identity_and_binds_input_hash(tmp_path, monkeypatch):
    run = coverage(tmp_path, monkeypatch)
    assert not run.selection_eligible({"asin": "B000CRBEJ2"}, {})
    assert run.selection_eligible({"asin": "B000CRBEJ3"}, {})
    assert not run.metadata["previous_details_reuse"]
    assert run.metadata["excluded_current_unique"] == 1
    assert not run.records and not run.selected
    assert run.metadata["excluded_current_files"]
    (tmp_path / "current.json").write_text("[]")
    with pytest.raises(ValueError, match="CURRENT_BATCH_EXCLUSION_DRIFT"):
        run.guards()
    with pytest.raises(ValueError, match="RUN_METADATA_DRIFT"):
        coverage(tmp_path, monkeypatch, resume=True)


def test_coverage_rejects_invalid_identity_before_browser(tmp_path, monkeypatch):
    (tmp_path / "current.json").write_text('[{"asin":"not-an-asin"}]')
    with pytest.raises(ValueError, match="INVALID_CURRENT_BATCH_IDENTITY"):
        coverage(tmp_path, monkeypatch)


def test_default_selection_policy_is_unchanged(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    assert run.selection_eligible({"asin": "B000CRBEJ2"}, {}) is True
    assert run.runtime_metadata() == {}


def test_known_identity_url_is_skipped_before_navigation(tmp_path, monkeypatch):
    run = coverage(tmp_path, monkeypatch)
    from amazon_es_bestseller.orchestration.link_first import LinkFirstRunner
    links = [{"product_link": url} for url in (
        "https://www.amazon.es/name/dp/B000CRBEJ2?ref=x",
        "https://www.amazon.es/gp/product/B000CRBEJ2",
        "https://www.amazon.es/dp/B000CRBEJ3",
        "https://www.amazon.es/unknown",
    )]
    monkeypatch.setattr(LinkFirstRunner, "_eligible_links", lambda *args: links)
    assert run._eligible_links({}) == links[2:]
    assert len(links) == 4  # Original snapshot is never edited.
