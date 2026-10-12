import hashlib
import importlib.util
import json
from pathlib import Path


def entry():
    path = Path(__file__).resolve().parents[1] / "tools/fast_parallel_coverage.py"
    spec = importlib.util.spec_from_file_location("fast_coverage_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_network_failure_preserves_evidence_and_never_creates_rankings(tmp_path):
    module = entry()
    html = tmp_path / "attempt_002.html"
    html.write_text("<html>net::ERR_FAILED</html>", encoding="utf-8")
    meta = {"html_file": html.name, "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
            "http_status": None, "final_url": "chrome-error://chromewebdata/",
            "access_state": "UNKNOWN", "error": "Page.goto: net::ERR_FAILED"}
    assert module.record_exhausted_page(tmp_path, tmp_path, meta)
    result = json.loads((tmp_path / "page_result.json").read_text(encoding="utf-8"))
    assert result["records"] == []
    assert not result["source_complete"]
    assert not result["snapshot_authoritative"]
    assert hashlib.sha256(html.read_bytes()).hexdigest() == meta["html_sha256"]


def test_restriction_or_wrong_hash_is_never_accepted_as_ordinary_failure(tmp_path):
    module = entry()
    html = tmp_path / "attempt_002.html"
    html.write_text("<html>Robot Check</html>", encoding="utf-8")
    meta = {"html_file": html.name, "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
            "http_status": None, "final_url": "chrome-error://chromewebdata/",
            "access_state": "UNKNOWN", "error": "Page.goto: net::ERR_FAILED"}
    assert not module.record_exhausted_page(tmp_path, tmp_path, meta)
    assert not (tmp_path / "page_result.json").exists()
    meta.update(http_status=403, access_state="BLOCKED")
    assert not module.record_exhausted_page(tmp_path, tmp_path, meta)
