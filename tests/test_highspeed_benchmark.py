import json
from pathlib import Path
from amazon_es_bestseller.highspeed.benchmark import run_benchmark


def test_t0_persists_all_duplicate_evidence_and_restores_without_network(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("b")
    html = tmp_path / "real_saved_fixture.html"
    html.write_text('<h1>Most gifted</h1><div id="zg" data-expected-count="1">'
                    '<div data-ranking-card="true"><span class="zg-bdg-text">#1</span>'
                    '<a href="/product/opaque">Title</a></div></div>', encoding="utf-8")
    result = run_benchmark([{"html_path": str(html), "page_url": "https://www.amazon.es/gp/most-gifted",
        "source": {"source_id": "gifted", "source_type": "MOST_GIFTED", "actual_source_url": "https://www.amazon.es/gp/most-gifted"}}],
        tmp_path / "benchmarks", 20, 50)
    assert result["live_requests"] == 0
    assert not result["live_t3_completed"]
    assert result["synthetic_target_verified"]
    assert result["synthetic"]["metrics"]["raw_observations"] == 50
    assert result["synthetic"]["metrics"]["unique_link_candidates"] == 20
    assert result["saved_real"]["metrics"]["unique_link_candidates"] == 1
    assert result["synthetic"]["restart_verified"]
    assert result["synthetic"]["strong_audit"]["gate_passed"]
    checkpoint = result["synthetic"]["metrics"]["final_full_checkpoint_result"]
    assert checkpoint[0] == 0 and checkpoint[1] == checkpoint[2]
    assert result["synthetic"]["metrics"]["sqlite_final_close_seconds"] >= 0
    assert result["synthetic"]["metrics"]["stages"]["restart_and_basic_qa"]["total_seconds"] > 0
    assert not result["synthetic"]["metrics"]["ten_hour_live_target_passed"]
    directory = Path(result["output_directory"])
    assert json.loads((directory / "benchmark.json").read_text(encoding="utf-8"))["stage"] == "T0"


def test_benchmark_strong_audit_rejects_cards_fabricated_by_parser(tmp_path_factory, monkeypatch):
    from amazon_es_bestseller.highspeed import benchmark
    original = benchmark.parse_page
    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        if result["observations"]:
            result["observations"][0]["raw_product_url"] = "/product/fabricated-not-in-raw-html"
        return result
    monkeypatch.setattr(benchmark, "parse_page", corrupt)
    result = benchmark.run_benchmark([], tmp_path_factory.mktemp("b"), 5, 10)
    assert not result["synthetic"]["strong_audit"]["gate_passed"]
    assert not result["synthetic_target_verified"]
    assert any(f["code"] == "RAW_OBSERVATIONS_NOT_BOUND_TO_REPARSED_CARDS"
               for f in result["synthetic"]["strong_audit"]["findings"])
