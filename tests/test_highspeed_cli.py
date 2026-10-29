"""V2 command integration against temporary saved fixtures only."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import hashlib
import pytest
import shutil
import subprocess
import sys
import uuid
import os
from amazon_es_bestseller.cli import build_parser, main
from amazon_es_bestseller.highspeed.cli import dispatch, load_json
from amazon_es_bestseller.highspeed.contracts import DEFAULT_CONFIG
from amazon_es_bestseller.multisource.storage import immutable_json, sealed

URL = "https://www.amazon.es/gp/bestsellers/home"
HTML = "<h1>Los más vendidos</h1><div data-expected-count='2'><div data-ranking-card='true'><span class='zg-bdg-text'>#1</span><a href='/dp/opaque-one'>One</a></div><div data-ranking-card='true'><span class='zg-bdg-text'>#2</span><a href='/dp/opaque-two'>Two</a></div></div>"

def invoke(*argv):
    return dispatch(build_parser().parse_args(list(argv)))

def offline_fixture(tmp_path):
    html = tmp_path / "saved.html"; html.write_text(HTML, encoding="utf-8")
    plan = {**DEFAULT_CONFIG, "mode": "HIGH_SPEED_V2", "stage": "T0", "run_id": "cli_saved",
        "origin": "OFFLINE_FIXTURE", "sources": [{"source_id": "home", "source_type": "BEST_SELLERS",
        "actual_source_url": URL, "research_category": "居家生活", "page_urls": [URL], "offline_pages": {URL: str(html)}}]}
    path = tmp_path / "plan.json"; path.write_text(json.dumps(plan), encoding="utf-8")
    return path, plan

def test_help_registers_new_mode_and_preserves_old_mode():
    parser = build_parser()
    assert parser.parse_args(["--offline", "highspeed", "plan", "--run-id", "new"]).operation == "plan"
    assert parser.parse_args(["--offline", "multisource", "status", "--root", "old", "--run-id", "old"]).operation == "status"
    selected = parser.parse_args(["highspeed", "resume", "--root", "isolated", "--plan", "reviewed.json", "--profile-dir", "fixed-night-profile"])
    assert selected.profile_dir == "fixed-night-profile"

def test_saved_html_run_resume_qa_report_gate_export(tmp_path):
    path, plan = offline_fixture(tmp_path)
    root = str(tmp_path / "store")
    first = invoke("--offline", "highspeed", "run", "--root", root, "--plan", str(path))
    second = invoke("--offline", "highspeed", "resume", "--root", root, "--plan", str(path))
    assert first["summary"]["requests"] == second["summary"]["requests"] == 1
    assert second["summary"]["unique_links"] == 2
    quality = invoke("--offline", "highspeed", "qa", "--root", root, "--run-id", plan["run_id"])
    assert quality["gate_passed"] and quality["unique_valid_links"] == 2
    report = invoke("--offline", "highspeed", "report", "--root", root, "--run-id", plan["run_id"], "--out", str(tmp_path / "reports"))
    assert Path(report["manifest_path"]).is_file()
    gate = invoke("--offline", "highspeed", "gate-export", "--root", root, "--run-id", plan["run_id"], "--out", str(tmp_path / "gates"))
    assert gate["passed"]
    from amazon_es_bestseller.highspeed.authorization import verified_stage_artifact
    assert verified_stage_artifact(gate["gate"], "T0")["unique_valid_links"] == 2

def test_offline_plan_and_discovery_never_claim_availability(tmp_path):
    path = tmp_path / "pending.json"
    result = invoke("--offline", "highspeed", "plan", "--stage", "T3", "--run-id", "pending", "--out", str(path))
    assert not result["live_enabled"] and result["sources"] == []
    html = tmp_path / "navigation.html"
    html.write_text("<div id='zg_browseRoot'><a href='/gp/bestsellers/home'>Hogar</a></div>", encoding="utf-8")
    found = invoke("--offline", "highspeed", "discover", "--html", str(html), "--page-url", "https://www.amazon.es/gp/bestsellers",
        "--source-type", "BEST_SELLERS", "--observed-at", "2026-10-13T05:49:28Z")
    assert found["live_requests"] == 0 and len(found["coverage"]) == 75
    assert found["sources"][0]["source_status"] == "UNVERIFIED"
    assert all(row["status"] == "UNVERIFIED" for row in found["coverage"])

def test_unapproved_live_never_initializes_browser(tmp_path, monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    monkeypatch.setattr(BrowserSession, "__enter__", lambda self: pytest.fail("browser must not open"))
    path, plan = offline_fixture(tmp_path)
    gate = tmp_path / "shared_gate.json"; immutable_json(gate, sealed({"state": "NETWORK_ERROR"}))
    plan.update(stage="T1", origin="LIVE_CAPTURE", live_enabled=True, shared_access_gate_path=str(gate))
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="LEGACY_HIGHSPEED_LIVE_RETIRED"):
        invoke("highspeed", "run", "--root", str(tmp_path / "store"), "--plan", str(path), "--live")

def test_invalid_t0_live_switch_never_opens_browser(tmp_path, monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    monkeypatch.setattr(BrowserSession, "__enter__", lambda self: pytest.fail("browser must not open"))
    path, _ = offline_fixture(tmp_path)
    with pytest.raises(ValueError, match="T0_MUST_REMAIN_OFFLINE"):
        invoke("highspeed", "run", "--root", str(tmp_path / "store"), "--plan", str(path), "--live")

def test_pending_templates_are_not_live_authorization():
    folder = Path(__file__).resolve().parents[1] / "configs/highspeed"
    for stage in ("T1", "T2", "T3"):
        plan = load_json(folder / (stage+".plan.pending.json"))
        approval = load_json(folder / (stage+".authorization.pending.json"))
        assert not plan["live_enabled"] and not approval["approved"]
        assert plan["sources"] == [] and approval["sources"] == {}
        assert plan["max_requests_per_run"] > 45 and plan["max_requests_per_day"] > 15
        assert plan["category_cooldown_seconds"] == 0 and plan["max_raw_observations_per_run"] > 30000

def test_retired_live_rejects_even_when_store_lock_is_held(tmp_path, monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    from amazon_es_bestseller.highspeed.store import RunStore
    monkeypatch.setattr(BrowserSession, "__init__", lambda *args, **kwargs: pytest.fail("contended lock must prevent browser construction"))
    path, plan = offline_fixture(tmp_path)
    plan.update(stage="T1", origin="LIVE_CAPTURE", live_enabled=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    root = tmp_path / "store"
    store = RunStore(root)
    try:
        with store.run_lock(plan["run_id"]):
            with pytest.raises(ValueError, match='LEGACY_HIGHSPEED_LIVE_RETIRED'):
                invoke("highspeed", "run", "--root", str(root), "--plan", str(path), "--live")
    finally:
        store.close()

def test_offline_entry_time_includes_store_initialization(tmp_path, monkeypatch):
    import amazon_es_bestseller.highspeed.cli as cli
    from amazon_es_bestseller.highspeed.store import RunStore
    path, plan = offline_fixture(tmp_path)
    original = RunStore.__init__
    entry = datetime.now(timezone.utc) - timedelta(seconds=2)
    monkeypatch.setattr(cli, "utc_text", lambda: entry.isoformat().replace("+00:00", "Z"))
    def initialize(self, root):
        original(self, root)
    monkeypatch.setattr(RunStore, "__init__", initialize)
    result = invoke("--offline", "highspeed", "run", "--root", str(tmp_path / "store"), "--plan", str(path))
    assert datetime.fromisoformat(result["run"]["started_at"].replace("Z", "+00:00")) == entry
    assert result["run"]["elapsed_seconds"] >= 2

def test_retired_live_rejects_even_when_shared_lock_is_held(tmp_path, monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    from amazon_es_bestseller.multisource.storage import file_lock
    monkeypatch.setattr(BrowserSession, "__init__", lambda *args, **kwargs: pytest.fail("shared lock must prevent browser construction"))
    path, plan = offline_fixture(tmp_path)
    shared = tmp_path / "shared" / "access_gate.json"
    shared.parent.mkdir()
    immutable_json(shared, sealed({"state": "NORMAL"}))
    plan.update(stage="T1", origin="LIVE_CAPTURE", live_enabled=True, shared_access_gate_path=str(shared))
    path.write_text(json.dumps(plan), encoding="utf-8")
    with file_lock(shared.parent / "highspeed_live.lock"):
        with pytest.raises(ValueError, match='LEGACY_HIGHSPEED_LIVE_RETIRED'):
            invoke("highspeed", "run", "--root", str(tmp_path / "different_store"), "--plan", str(path), "--live")

def test_legacy_helpers_removed_and_python_launcher_retains_offline_query_cli():
    repository = Path(__file__).resolve().parents[1]
    assert not (repository / 'scripts/Invoke-HighSpeed.ps1').exists()
    assert not (repository / 'scripts/Invoke-Multisource.ps1').exists()
    result = subprocess.run([sys.executable, '-B', str(repository / 'scripts/amazon_es.py'),
        "--offline", "highspeed", "--help"], cwd=repository,
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert "gate-export" in result.stdout

def test_cli_renormalize_keeps_original_journals_and_request_count(tmp_path):
    path, plan = offline_fixture(tmp_path); root = tmp_path / "store"
    invoke("--offline", "highspeed", "run", "--root", str(root), "--plan", str(path))
    journals = {p: p.read_bytes() for p in (root / "runs" / plan["run_id"] / "pages").glob("*.json")}
    result = invoke("--offline", "highspeed", "renormalize", "--root", str(root), "--run-id", plan["run_id"], "--out", str(tmp_path / "views"))
    assert result["is_derivation"] and result["live_requests"] == 0 and result["capture_times_preserved"]
    assert all(p.read_bytes() == original for p, original in journals.items())
    status = invoke("--offline", "highspeed", "status", "--root", str(root), "--run-id", plan["run_id"])
    assert status["summary"]["requests"] == 1

def test_cli_small_offline_benchmark_never_claims_amazon_target(tmp_path):
    path, plan = offline_fixture(tmp_path)
    specs = tmp_path / "pages.json"
    specs.write_text(json.dumps([{"html_path": plan["sources"][0]["offline_pages"][URL],
        "page_url": URL, "source": plan["sources"][0], "observed_at": "2026-10-13T05:49:28Z"}]), encoding="utf-8")
    # Windows temporary publication needs space for the 64-byte content hash.
    benchmark_root = tmp_path.parent / ("b_"+uuid.uuid4().hex[:8])
    result = invoke("--offline", "highspeed", "benchmark", "--pages", str(specs), "--out", str(benchmark_root),
        "--synthetic-unique", "3", "--synthetic-observations", "6")
    assert result["live_requests"] == 0 and not result["live_t3_completed"]
    assert result["synthetic_target_verified"]

def test_cli_gate_recovery_missing_human_expiry_cannot_clear_gate(tmp_path):
    from amazon_es_bestseller.highspeed.store import RunStore
    root = tmp_path / "store"; store = RunStore(root)
    store.set_gate("NETWORK_ERROR", reason="fixture")
    before = (root / "access_gate.json").read_bytes(); store.close()
    authorization = tmp_path / "handling.json"
    authorization.write_text(json.dumps({"approved": True, "human_handled": True, "store_root": str(root.resolve())}), encoding="utf-8")
    with pytest.raises(ValueError, match="EXACT_VALID_HUMAN"):
        invoke("--offline", "highspeed", "gate-recover", "--root", str(root), "--authorization", str(authorization))
    assert (root / "access_gate.json").read_bytes() == before

@pytest.mark.skipif(os.name != "nt", reason="Windows ordinary-path publication bound")
def test_long_windows_root_rejected_before_store_writes_or_browser(tmp_path, monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    from amazon_es_bestseller.highspeed.store import RunStore
    monkeypatch.setattr(RunStore, "__init__", lambda *args, **kwargs: pytest.fail("unsupported path must fail before DB/store writes"))
    monkeypatch.setattr(BrowserSession, "__init__", lambda *args, **kwargs: pytest.fail("unsupported path must fail before browser"))
    path, plan = offline_fixture(tmp_path)
    # Remain over the publication bound even with a short pytest basetemp.
    root = tmp_path / ("deep_runtime_"+"x"*180)
    with pytest.raises(ValueError, match="SHORTER_ROOT_REQUIRED"):
        invoke("--offline", "highspeed", "run", "--root", str(root), "--plan", str(path))
    assert not root.exists()
