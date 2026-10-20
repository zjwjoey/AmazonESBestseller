import hashlib
import json

from amazon_es_bestseller.quality.source_evidence import audit_detail_run_evidence


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_detail_run_evidence_is_hash_bound_and_never_claims_source_audit_pass(tmp_path):
    html = b"<html>saved product evidence</html>"
    evidence = tmp_path / "html" / "B000000001.html"
    evidence.parent.mkdir()
    evidence.write_bytes(html)
    _write(tmp_path / "detail_execution_manifest.json", {"records": [
        {"ranking_asin": "B000000001", "status": "SUCCESS",
         "candidate_ids": ["candidate-1"],
         "candidate_observation_ids": ["observation-1"],
         "saved_html_path": "html/B000000001.html",
         "html_sha256": hashlib.sha256(html).hexdigest()},
        {"ranking_asin": "B000000002", "status": "SUCCESS"},
    ]})

    report = audit_detail_run_evidence(tmp_path)

    assert report["evidence_counts"] == {
        "REQUEST_EVIDENCE": 0, "VERIFIED_SAVED_EVIDENCE": 1, "INSUFFICIENT": 1,
    }
    assert report["records"][0]["candidate_ids"] == ["candidate-1"]
    assert report["records"][0]["candidate_observation_ids"] == ["observation-1"]
    assert report["source_audit_compatibility"]["formal_source_audit_pass"] is False


def test_request_evidence_requires_actual_request_response_and_hash(tmp_path):
    _write(tmp_path / "detail_execution_manifest.json", {"records": [
        {"ranking_asin": "B000000001", "requested_url": "https://www.amazon.es/dp/B000000001",
         "status_code": 200, "response_sha256": "abc"},
        {"ranking_asin": "B000000002", "status": "SUCCESS", "status_code": 200},
    ]})

    report = audit_detail_run_evidence(tmp_path)

    assert report["evidence_counts"]["REQUEST_EVIDENCE"] == 1
    assert report["records"][1]["evidence_level"] == "INSUFFICIENT"
    assert report["source_audit_compatibility"]["formal_source_audit_pass"] is False
