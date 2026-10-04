import json

from amazon_es_bestseller import cli


def test_identity_extract_cli_is_offline_and_writes_audit(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "page.html").write_text(
        "<div id='gridItemRoot' data-asin='B078C6QR1C'></div>", encoding="utf-8")
    out = tmp_path / "extract"
    assert cli.main(["--offline", "ranking-identity-extract", "--evidence-dir", str(evidence),
                     "--out-dir", str(out)]) == 0
    assert json.loads((out / "identity.json").read_text(encoding="utf-8"))[0]["product_url"] == "https://www.amazon.es/dp/B078C6QR1C"


def test_identity_snapshot_cli_can_save_incomplete_debug_snapshot(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "page.html").write_text(
        "<div id='gridItemRoot' data-asin='B078C6QR1C'></div>", encoding="utf-8")
    out = tmp_path / "snapshots"
    assert cli.main(["--offline", "ranking-identity-snapshot", "--evidence-dir", str(evidence),
                     "--out-dir", str(out), "--expected-count", "2",
                     "--allow-incomplete-debug"]) == 0
    manifest = next(out.glob("**/manifest.json"))
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "IDENTITY_PARTIAL"
