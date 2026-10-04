import json
from pathlib import Path

from amazon_es_bestseller.collection.ranking import collect_rankings
from amazon_es_bestseller.monitoring.ranking_identity import extract as identity_extract


class _Page:
    def __init__(self, html):
        self.html = html

    def content(self):
        return self.html


class _Session:
    def __init__(self, html):
        self.page = _Page(html)

    def goto(self, url):
        return 200

    def wait_between_requests(self):
        return None


def test_collector_persists_identity_immediately_after_saved_html(tmp_path):
    html = "<div id='gridItemRoot' data-asin='B078C6QR1C'><a href='/dp/B078C6QR1C'>x</a></div>"
    result = collect_rankings(["https://www.amazon.es/gp/bestsellers/tools"],
                              _Session(html), str(tmp_path))
    identity = json.loads((Path(result.run_dir) / "identity.json").read_text(encoding="utf-8"))
    assert identity[0]["asin"] == "B078C6QR1C"
    assert identity[0]["product_url"] == "https://www.amazon.es/dp/B078C6QR1C"
    assert identity[0]["source_url"] == "https://www.amazon.es/gp/bestsellers/tools"
    assert identity[0]["page_number"] == 1
    status = json.loads((Path(result.run_dir) / "identity_status.json").read_text(encoding="utf-8"))
    assert status["identity_status"] == "IDENTITY_COMPLETENESS_UNKNOWN"


def test_collector_exposes_identity_failure_without_removing_ranking_evidence(tmp_path, monkeypatch):
    html = "<div id='gridItemRoot' data-asin='B078C6QR1C'></div>"

    def fail(*_args, **_kwargs):
        raise RuntimeError("parser regression")

    monkeypatch.setattr(identity_extract, "extract_identity_from_evidence", fail)
    result = collect_rankings(["https://www.amazon.es/gp/bestsellers/tools"],
                              _Session(html), str(tmp_path))
    run = Path(result.run_dir)
    assert (run / "rankings.json").exists()
    status = json.loads((run / "identity_status.json").read_text(encoding="utf-8"))
    assert status["identity_status"] == "IDENTITY_FAILED"
    assert status["identity_complete"] is False
