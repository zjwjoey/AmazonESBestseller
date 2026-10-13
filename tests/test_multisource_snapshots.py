"""Cross-module crash, idempotency, authorization and cumulative budget tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.cli import main, build_parser
from amazon_es_bestseller.multisource.authorization import validate_authorization
from amazon_es_bestseller.multisource.budgets import BudgetLedger
from amazon_es_bestseller.multisource.contracts import SOURCE_BUDGETS, timestamp
from amazon_es_bestseller.multisource.identity import ProductIdentityRegistry
from amazon_es_bestseller.multisource.migrations import upgrade_state, rollback_state
from amazon_es_bestseller.multisource.schedule import daily_plan
from amazon_es_bestseller.multisource.snapshots import SnapshotManager
from amazon_es_bestseller.multisource.sources import source_id
from amazon_es_bestseller.multisource.storage import digest, atomic_json, read_sealed, sealed
from amazon_es_bestseller.transport.base import TransportResponse

FIXTURE = Path(__file__).parent / "fixtures" / "multisource" / "best_sellers.html"
URL = "https://www.amazon.es/gp/bestsellers/kitchen/12345"


def setup(tmp_path):
    source = {"source_id": source_id("BEST_SELLERS", URL, "厨房用品"),
        "source_type": "BEST_SELLERS", "actual_source_url": URL,
        "research_category": "厨房用品", "browse_node_id": "12345",
        "mapping_evidence": {"reason": "Explicit synthetic fixture node mapping"},
        "source_status": "UNVERIFIED"}
    plan = {"phase": "P0", "live_enabled": False, "parallelism": 1,
        "max_requests": 100, "max_pages": 50, "max_requests_per_day": 25,
        "max_observations": 1500, "request_delay_seconds": 2}
    pages = [{"source_id": source["source_id"], "page_url": URL,
        "page_number": 1, "html_path": str(FIXTURE), "observed_at": "2026-10-12T10:00:00Z"}]
    return SnapshotManager(tmp_path / "isolated"), source, plan, pages


def approval(source, plan, **values):
    now = datetime.now(timezone.utc)
    return {"approved": True, "approval_id": "offline-test-approval", "approved_by": "test-human",
        "phase": "P0", "reviewed_at": (now-timedelta(minutes=1)).isoformat(),
        "expires_at": (now+timedelta(hours=1)).isoformat(),
        "access_gate_state": "NORMAL", "stop_conditions": ["CHALLENGE", "BLOCKED", "RATE_LIMITED", "UNKNOWN", "NETWORK_ERROR"],
        **{k: plan[k] for k in ("max_requests", "max_pages", "max_requests_per_day", "max_observations")},
        "sources": {source["source_id"]: {"source_type": source["source_type"], "research_category": source["research_category"],
            "page_urls": [URL], "evidence": "Offline fake transport authorization fixture", "allow_acp": False}}, **values}


def test_roundtrip_rerun_checkpoint_corruption_and_reparse_are_idempotent(tmp_path):
    manager, source, plan, pages = setup(tmp_path)
    first = manager.run(plan, [source], run_id="day1", pages=pages)
    assert len(first["observations"]) == 2
    assert first["manifest"]["origin"] == "OFFLINE_FIXTURE"
    raw = (Path(first["path"]) / "observations.jsonl").read_bytes()
    checkpoint = manager._run_dir("day1") / "checkpoint.json"
    checkpoint.write_text("broken", encoding="utf8")
    restarted = SnapshotManager(manager.root)
    assert restarted.status("day1")["checkpoint"]["counters"]["observations"] == 2
    repeated = restarted.run(plan, [source], run_id="day1", pages=pages)
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"] == 2
    assert len(repeated["observations"]) == 2
    version = manager.reparse("day1", version="v2")
    assert {o["observation_id"] for o in version["observations"]} == {o["observation_id"] for o in first["observations"]}
    assert version["manifest"]["local_date"] == "2026-10-12"
    assert (Path(first["path"]) / "observations.jsonl").read_bytes() == raw
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"] == 2


def test_frozen_artifact_and_raw_evidence_corruption_fail_closed(tmp_path):
    manager, source, plan, pages = setup(tmp_path)
    bundle = manager.run(plan, [source], run_id="day1", pages=pages)
    path = Path(bundle["path"]) / "observations.jsonl"
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b"B000000001", b"B000000009"))
    with pytest.raises(ValueError, match="ARTIFACT_CORRUPTED"):
        manager.load("day1")
    path.write_bytes(raw)
    Path(bundle["pages"][0]["evidence_path"]).write_text("damaged", encoding="utf8")
    with pytest.raises(ValueError, match="CORRUPT_EVIDENCE"):
        manager.reparse("day1", version="v2")
    assert not manager.load("day1")["quality"]["gate_passed"]


def test_partial_immutable_publish_crash_reuses_journal_without_duplicate_counts(tmp_path, monkeypatch):
    manager, source, plan, pages = setup(tmp_path)
    import amazon_es_bestseller.multisource.snapshots as module
    original = module.immutable_json
    failed = [False]
    def fail_once(path, value):
        if Path(path).parent.name == "pages" and not failed[0]:
            failed[0] = True
            raise OSError("simulated interruption before page commit")
        return original(path, value)
    monkeypatch.setattr(module, "immutable_json", fail_once)
    with pytest.raises(OSError):
        manager.run(plan, [source], run_id="recovery", pages=pages)
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"] == 2
    bundle = manager.run(plan, [source], run_id="recovery", pages=pages, resume=True)
    assert len(bundle["observations"]) == 2
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"] == 2


def test_review_and_undo_regenerate_snapshot_counts_without_overwriting(tmp_path):
    manager, source, plan, pages = setup(tmp_path)
    before = manager.run(plan, [source], run_id="day1", pages=pages)
    raw_path = Path(before["path"]) / "observations.jsonl"
    checksum = digest(raw_path.read_bytes())
    registry = ProductIdentityRegistry(manager.root / "identity")
    event = registry.review("same", [o["observation_id"] for o in before["observations"]], actor="human", evidence="Offline review example")
    assert manager.load("day1")["quality"]["counts"]["confirmed_unique_products"] == 1
    registry.undo(event["event_id"], actor="human", evidence="Reverted wrong test merge")
    assert manager.load("day1")["quality"]["counts"]["confirmed_unique_products"] == 0
    assert digest(raw_path.read_bytes()) == checksum


@pytest.mark.parametrize("bad", [{"approved": False}, {"phase": "P1"}, {"max_requests": 1}, {"stop_conditions": []}, {"access_gate_state": "BLOCKED"}])
def test_live_preflight_rejects_unreviewed_scope_before_transport(tmp_path, bad):
    manager, source, plan, pages = setup(tmp_path)
    plan["live_enabled"] = True
    auth = approval(source, plan, **bad)
    class NeverCalled:
        def fetch_page(self, url):
            pytest.fail("Unapproved run accessed transport")
    with pytest.raises(ValueError):
        manager.run(plan, [source], run_id="denied", origin="LIVE_CAPTURE", pages=[{k:v for k,v in pages[0].items() if k != "html_path"}], authorization=auth, transport=NeverCalled())
    assert manager.budget.counters("LIVE_CAPTURE")["request_count"] == 0


@pytest.mark.parametrize("code,body,state", [(403,"denied","BLOCKED"),(429,"slow","RATE_LIMITED"),(200,"Robot Check","CHALLENGE")])
def test_restriction_stops_all_new_requests_and_requires_new_human_review(tmp_path, monkeypatch, code, body, state):
    manager, source, plan, pages = setup(tmp_path)
    plan["live_enabled"] = True
    auth = approval(source, plan)
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep", lambda _: None)
    class Fake:
        calls = 0
        def fetch_page(self, url):
            self.calls += 1
            return TransportResponse(code, url, body)
    transport = Fake()
    live_pages = [{"source_id": source["source_id"], "page_url": URL, "page_number": 1}]
    with pytest.raises(ValueError, match="ACCESS_STOP"):
        manager.run(plan, [source], run_id="blocked", origin="LIVE_CAPTURE", pages=live_pages, authorization=auth, transport=transport)
    assert transport.calls == 1 and manager._gate()["state"] == state
    assert manager.status("blocked")["checkpoint"]["access_state"] == state
    with pytest.raises(ValueError, match="NEW_HUMAN"):
        manager.run(plan, [source], run_id="blocked", origin="LIVE_CAPTURE", pages=live_pages, authorization=auth, transport=transport, resume=True)
    assert transport.calls == 1
    assert manager.budget.counters("LIVE_CAPTURE")["request_count"] == 1


def test_previous_phase_gate_cannot_be_fixture_or_failed(tmp_path):
    _, source, plan, _ = setup(tmp_path)
    plan.update(phase="P1", live_enabled=True, max_observations=5000)
    source["source_status"] = "AVAILABLE"
    gatepath = tmp_path / "previous.json"
    for origin, passed in [("OFFLINE_FIXTURE", True),("LIVE_CAPTURE", False)]:
        atomic_json(gatepath, sealed({"phase":"P0", "origin":origin, "passed":passed}))
        auth = approval(source, plan, phase="P1", previous_phase_gate_path=str(gatepath), previous_phase_gate_sha256=digest(gatepath.read_bytes()))
        with pytest.raises(ValueError, match="PREVIOUS_PHASE_GATE_FAILED"):
            validate_authorization(plan, auth, [source], {"state":"NORMAL"})


def test_global_30000_and_source_budgets_count_observations_once(tmp_path):
    ledger = BudgetLedger(tmp_path)
    for source, count in SOURCE_BUDGETS.items():
        rows = [{"observation_id": f"{source}_{i}", "source_type": source} for i in range(count)]
        assert ledger.accept(source, rows, origin="OFFLINE_FIXTURE", phase="P3")
        assert not ledger.accept(source, rows, origin="OFFLINE_FIXTURE", phase="P3")
    assert ledger.counters("OFFLINE_FIXTURE")["observation_count"] == 30000
    assert ledger.counters("LIVE_CAPTURE")["observation_count"] == 0
    with pytest.raises(ValueError, match="OBSERVATION_BUDGET"):
        ledger.accept("extra", [{"observation_id":"extra", "source_type":"BEST_SELLERS"}], origin="OFFLINE_FIXTURE", phase="P3")


def test_phase_daily_request_and_page_caps_are_independent(tmp_path):
    ledger = BudgetLedger(tmp_path)
    caps = {"max_requests":3, "max_pages":1, "max_requests_per_day":2}
    args = dict(origin="LIVE_CAPTURE", source_type="BEST_SELLERS", url=URL, observed_at="2026-10-01T22:30:00Z", caps=caps)
    ledger.reserve_request("r1", kind="PAGE", **args)
    ledger.reserve_request("r2", kind="ACP", **args)
    with pytest.raises(ValueError, match="REQUEST_BUDGET"):
        ledger.reserve_request("r3", kind="ACP", **args)
    args["observed_at"] = "2026-10-02T22:30:00Z"
    with pytest.raises(ValueError, match="PAGE_BUDGET"):
        ledger.reserve_request("r3", kind="PAGE", **args)
    assert ledger.counters("LIVE_CAPTURE")["daily_requests"] == {"2026-10-02":2}
    with pytest.raises(ValueError, match="OBSERVATION_BUDGET"):
        ledger.accept("p0", [{"observation_id":str(i),"source_type":"BEST_SELLERS"} for i in range(1501)], origin="LIVE_CAPTURE",phase="P0")


def test_budget_adjustment_preserves_audit_and_cannot_raise_global_cap(tmp_path):
    ledger = BudgetLedger(tmp_path)
    changed = {**SOURCE_BUDGETS, "BEST_SELLERS":8500,"NEW_RELEASES":6500}
    with pytest.raises(ValueError, match="EXPLICIT_REVIEW"):
        ledger.adjust(changed, actor="reviewer", reason="source gap", replacement_sources=["reviewed node"])
    replacement=[{"source_id":"reviewed node","source_type":"NEW_RELEASES","research_category":"厨房用品",
        "verification_evidence":{"origin":"LIVE_CAPTURE"},"review_evidence":"Approved fixture plan",
        "page_urls":["https://www.amazon.es/gp/new-releases/kitchen"],"quantity":500,"tier":3,"cross_source_approved":True}]
    ledger.adjust(changed, actor="reviewer", reason="source gap", replacement_sources=replacement, approved=True)
    assert ledger.counters("LIVE_CAPTURE")["adjustments"][0]["old_budget"] == SOURCE_BUDGETS
    with pytest.raises(ValueError, match="GLOBAL_BUDGET"):
        ledger.adjust({**changed,"NEW_RELEASES":6501}, actor="reviewer",reason="raise",replacement_sources=replacement,approved=True)


def test_copy_on_upgrade_and_exact_rollback(tmp_path):
    path = tmp_path / "old.json"
    initial = {"schema_version":0,"payload":{"counters":{"observations":12}}}
    original_bytes=(json.dumps(initial,indent=2)+"\n").encode("utf-8")
    path.write_bytes(original_bytes)
    upgraded = upgrade_state(path)
    assert read_sealed(path) == initial["payload"]
    assert upgrade_state(path)["changed"] is False
    assert rollback_state(upgraded["journal"])["rolled_back"]
    assert json.loads(path.read_bytes()) == initial
    assert path.read_bytes()==original_bytes
    assert upgrade_state(path)["journal"]==upgraded["journal"]


def test_daily_schedule_has_fourteen_slots_and_cannot_redate_history(tmp_path):
    _, source, plan, pages = setup(tmp_path)
    planfile = tmp_path / "plan.json"
    atomic_json(planfile,{"schema_version":1,"origin":"OFFLINE_FIXTURE","plan":plan,"sources":[source],"pages":pages})
    config = tmp_path / "schedule.json"
    atomic_json(config,{"schema_version":1,"enabled":True,"timezone":"Europe/Madrid","days":14,"start_date":"2026-10-12","schedule_id":"sim","plan_file":"plan.json"})
    ids = {daily_plan(config, local_date=f"2026-10-{i:02d}")["run_id"] for i in range(12,26)}
    assert len(ids) == 14
    with pytest.raises(ValueError,match="OUTSIDE"):
        daily_plan(config,local_date="2026-10-26")
    payload = json.loads(planfile.read_bytes()); payload["origin"]="HISTORICAL_REPLAY"; atomic_json(planfile,payload)
    with pytest.raises(ValueError,match="CANNOT_REDATE"):
        daily_plan(config,local_date="2026-10-13")


def test_cli_replay_status_pause_resume_report_and_legacy_registration(tmp_path,capsys):
    manager, source, plan, pages = setup(tmp_path)
    planfile = tmp_path / "plan.json"
    atomic_json(planfile,{"schema_version":1,"run_id":"cli-day","origin":"OFFLINE_FIXTURE","plan":plan,"sources":[source],"pages":pages})
    common = ["--root",str(manager.root)]
    assert main(["multisource","replay",*common,"--plan",str(planfile)]) == 0
    capsys.readouterr()
    assert main(["multisource","status",*common,"--run-id","cli-day"]) == 0
    capsys.readouterr()
    assert main(["multisource","pause",*common,"--run-id","cli-day"]) == 0
    capsys.readouterr()
    assert main(["multisource","resume",*common,"--run-id","cli-day","--plan",str(planfile)]) == 0
    capsys.readouterr()
    assert main(["multisource","report",*common,"--run-id","cli-day","--out",str(tmp_path/"reports")]) == 0
    output=json.loads(capsys.readouterr().out)
    assert Path(output["text"]).is_file()
    assert build_parser().parse_args(["export"]).command == "export"
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"] == 2


def test_cli_report_and_verify_use_requested_reparse_version(tmp_path, capsys, monkeypatch):
    manager, source, plan, pages = setup(tmp_path)
    manager.run(plan, [source], run_id="versioned", pages=pages)
    manager.reparse("versioned", version="v2")
    common = ["--root", str(manager.root), "--run-id", "versioned", "--version", "v2"]
    assert main(["multisource", "report", *common, "--out", str(tmp_path / "reports")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(Path(result["json"]).read_text(encoding="utf-8"))["manifest"]["version"] == "v2"
    seen = []
    original = SnapshotManager.load
    def track(self, run_id, **kwargs):
        seen.append(kwargs.get("version", "v1"))
        return original(self, run_id, **kwargs)
    monkeypatch.setattr(SnapshotManager, "load", track)
    assert main(["multisource", "verify", *common]) == 0
    assert seen == ["v2"]


def test_store_and_run_ids_cannot_overwrite_unrelated_files(tmp_path):
    (tmp_path/"production.json").write_text("protected")
    with pytest.raises(ValueError,match="DEDICATED_EMPTY"):
        SnapshotManager(tmp_path)
    manager,source,plan,pages=setup(tmp_path)
    with pytest.raises(ValueError,match="INVALID_STORAGE_ID"):
        manager.run(plan,[source],run_id="../../production",pages=pages)


def test_live_acp_response_recovery_reuses_saved_body_and_charges_only_two_requests(tmp_path, monkeypatch):
    from test_multisource_sources import acp_page
    manager, source, plan, _ = setup(tmp_path)
    plan["live_enabled"] = True
    auth = approval(source, plan)
    auth["sources"][source["source_id"]]["allow_acp"] = True
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep", lambda _: None)
    class Fake:
        page_calls = acp_calls = 0
        def fetch_page(self, url):
            self.page_calls += 1
            return TransportResponse(200, url, acp_page())
        def fetch_ajax(self, url, **kw):
            self.acp_calls += 1
            body = "<div id='gridItemRoot' data-asin='B000000002'><span class='zg-bdg-text'>#2</span><a href='/dp/B000000002'>Cuchara</a></div>"
            return TransportResponse(200, url, body, request_url=url, request_payload=kw.get("payload"))
    fake = Fake()
    import amazon_es_bestseller.multisource.snapshots as module
    publish = module.immutable_json
    failures = [False]
    def crash(path, payload):
        if Path(path).parent.name == "pages" and not failures[0]:
            failures[0] = True
            raise OSError("crash after ACP response")
        return publish(path, payload)
    monkeypatch.setattr(module, "immutable_json", crash)
    pages = [{"source_id":source["source_id"],"page_url":URL,"page_number":1}]
    with pytest.raises(OSError):
        manager.run(plan,[source],run_id="acp",origin="LIVE_CAPTURE",pages=pages,authorization=auth,transport=fake)
    bundle=manager.run(plan,[source],run_id="acp",origin="LIVE_CAPTURE",pages=pages,authorization=auth,transport=fake,resume=True)
    assert fake.page_calls == fake.acp_calls == 1
    assert manager.budget.counters("LIVE_CAPTURE")["request_count"] == 2
    assert len(bundle["observations"]) == 2
    assert bundle["observations"][1]["evidence_path"].endswith(".acp.html")
    assert bundle["quality"]["rates"]["evidence_traceability"] == 1


def test_live_daily_cap_amendment_preserves_original_spec_and_cumulative_budget(tmp_path, monkeypatch):
    manager, source, plan, _ = setup(tmp_path)
    plan.update(live_enabled=True, max_requests=3, max_requests_per_day=1)
    url2 = URL + "?pg=2"
    auth = approval(source, plan)
    auth["sources"][source["source_id"]]["page_urls"].append(url2)
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep", lambda _: None)
    class Fake:
        calls = 0
        def fetch_page(self, url):
            self.calls += 1
            return TransportResponse(200, url, FIXTURE.read_text(encoding="utf-8"))
    fake = Fake()
    with pytest.raises(ValueError, match="REQUEST_BUDGET_EXHAUSTED"):
        manager.run(plan, [source], run_id="cap-change", origin="LIVE_CAPTURE", authorization=auth, transport=fake)
    spec_path = manager._run_dir("cap-change") / "run_spec.json"
    original = spec_path.read_bytes()
    amended = dict(plan, max_requests_per_day=3)
    with pytest.raises(ValueError, match="UNREVIEWED_OR_INVALID_BUDGET"):
        manager.run(amended, [source], run_id="cap-change", origin="LIVE_CAPTURE", authorization=auth, transport=fake, resume=True)
    new = deepcopy(auth)
    new.update(approval_id="explicit-user-cap-change", max_requests_per_day=3)
    result = manager.run(amended, [source], run_id="cap-change", origin="LIVE_CAPTURE", authorization=new, transport=fake, resume=True)
    assert spec_path.read_bytes() == original
    assert fake.calls == 2
    assert manager.budget.counters("LIVE_CAPTURE")["request_count"] == 2
    assert result["manifest"]["execution_plan"]["max_requests_per_day"] == 3
    assert len(result["manifest"]["plan_amendments"]) == 1
    expanded = dict(amended, max_requests=4)
    newer = dict(new, max_requests=4)
    with pytest.raises(ValueError, match="IMMUTABLE_RUN_SPEC_CONFLICT"):
        manager.run(expanded, [source], run_id="cap-change", origin="LIVE_CAPTURE", authorization=newer, transport=fake, resume=True)


def test_explicit_new_human_authorization_can_resume_restricted_page(tmp_path,monkeypatch):
    manager,source,plan,_=setup(tmp_path)
    plan["live_enabled"]=True
    old=approval(source,plan)
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep",lambda _:None)
    class Fake:
        calls=0
        def fetch_page(self,url):
            self.calls+=1
            return TransportResponse(403,url,"denied") if self.calls==1 else TransportResponse(200,url,FIXTURE.read_bytes().decode())
    fake=Fake(); pages=[{"source_id":source["source_id"],"page_url":URL,"page_number":1}]
    with pytest.raises(ValueError,match="ACCESS_STOP"):
        manager.run(plan,[source],run_id="human",origin="LIVE_CAPTURE",pages=pages,authorization=old,transport=fake)
    new=approval(source,plan,approval_id="new-human-review",clear_access_gate=True)
    result=manager.run(plan,[source],run_id="human",origin="LIVE_CAPTURE",pages=pages,authorization=new,transport=fake,resume=True)
    assert len(result["observations"]) == 2 and fake.calls==2
    assert manager.status("human")["checkpoint"]["access_state"] == "NORMAL"
    assert manager.budget.counters("LIVE_CAPTURE")["request_count"]==2


def test_acp_resume_in_fresh_browser_refreshes_only_uncommitted_page(tmp_path, monkeypatch):
    from test_multisource_sources import acp_page
    manager, source, plan, _ = setup(tmp_path)
    plan["live_enabled"] = True
    old = approval(source, plan)
    old["sources"][source["source_id"]]["allow_acp"] = True
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep", lambda _: None)
    class Fake:
        page_calls = acp_calls = 0
        current_url = "about:blank"
        def requires_ajax_page_context(self, url):
            return self.current_url != url
        def fetch_page(self, url):
            self.page_calls += 1
            self.current_url = url
            return TransportResponse(200, url, acp_page().replace("'token'", "'token-%d'" % self.page_calls))
        def fetch_ajax(self, url, **kwargs):
            assert self.current_url == URL, "saved HTML cannot establish browser origin"
            self.acp_calls += 1
            if self.acp_calls == 1:
                return TransportResponse(400, url, "Documento no encontrado")
            assert kwargs["headers"]["x-amz-acp-params"] == "token-2"
            return TransportResponse(200, url, "<div id='gridItemRoot' data-asin='B000000002'><span class='zg-bdg-text'>#2</span><a href='/dp/B000000002'>Cuchara</a></div>")
    fake = Fake()
    pages = [{"source_id": source["source_id"], "page_url": URL, "page_number": 1}]
    with pytest.raises(ValueError, match="ACCESS_STOP: UNKNOWN"):
        manager.run(plan, [source], run_id="fresh-context", origin="LIVE_CAPTURE", pages=pages, authorization=old, transport=fake)
    receipts = {path: path.read_bytes() for path in (manager._run_dir("fresh-context") / "requests").glob("*.json")}
    fake.current_url = "about:blank"
    new = approval(source, plan, approval_id="new-human-review", clear_access_gate=True)
    new["sources"][source["source_id"]]["allow_acp"] = True
    bundle = manager.run(plan, [source], run_id="fresh-context", origin="LIVE_CAPTURE", pages=pages, authorization=new, transport=fake, resume=True)
    assert fake.page_calls == fake.acp_calls == 2
    assert manager.budget.counters("LIVE_CAPTURE")["request_count"] == 4
    assert len(bundle["observations"]) == 2
    assert all(path.read_bytes() == body for path, body in receipts.items())


def test_scheduler_uses_captured_native_acp_descriptor_before_metered_request(tmp_path, monkeypatch):
    from test_multisource_sources import acp_page
    manager, source, plan, _ = setup(tmp_path)
    plan["live_enabled"] = True
    auth = approval(source, plan)
    auth["sources"][source["source_id"]]["allow_acp"] = True
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep", lambda _: None)
    class Fake:
        prepared = False
        def fetch_page(self, url):
            return TransportResponse(200, url, acp_page())
        def prepare_ajax_descriptor(self, descriptor, *, referer):
            assert referer == URL
            self.prepared = True
            return {**descriptor, "payload": "native-body", "headers": {"content-type": "native-type"}}
        def fetch_ajax(self, url, **kwargs):
            assert self.prepared
            assert kwargs["payload"] == "native-body"
            assert kwargs["headers"] == {"content-type": "native-type"}
            return TransportResponse(200, url, "<div id='gridItemRoot' data-asin='B000000002'><span class='zg-bdg-text'>#2</span><a href='/dp/B000000002'>Cuchara</a></div>")
    pages = [{"source_id": source["source_id"], "page_url": URL, "page_number": 1}]
    result = manager.run(plan, [source], run_id="native-contract", origin="LIVE_CAPTURE", pages=pages, authorization=auth, transport=Fake())
    assert len(result["observations"]) == 2
    assert manager.budget.counters("LIVE_CAPTURE")["request_count"] == 2


def test_saved_unreviewed_redirect_cannot_pass_on_resume(tmp_path,monkeypatch):
    manager,source,plan,_=setup(tmp_path); plan["live_enabled"]=True
    auth=approval(source,plan)
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep",lambda _:None)
    class Fake:
        def fetch_page(self,url):
            return TransportResponse(200,"https://www.amazon.es/gp/bestsellers/toys",FIXTURE.read_bytes().decode())
    pages=[{"source_id":source["source_id"],"page_url":URL,"page_number":1}]
    with pytest.raises(ValueError,match="UNREVIEWED_PAGE_URL"):
        manager.run(plan,[source],run_id="redirect",origin="LIVE_CAPTURE",pages=pages,authorization=auth,transport=Fake())
    assert manager._gate()["state"]=="UNREVIEWED_REDIRECT"
    new=approval(source,plan,approval_id="new",clear_access_gate=True)
    with pytest.raises(ValueError,match="UNREVIEWED_PAGE_URL"):
        manager.run(plan,[source],run_id="redirect",origin="LIVE_CAPTURE",pages=pages,authorization=new,transport=Fake(),resume=True)


def test_reparse_preserves_original_http_restriction(tmp_path):
    manager,source,plan,pages=setup(tmp_path)
    pages[0]["http_status"]=403
    before=manager.run(plan,[source],run_id="offline403",pages=pages)
    after=manager.reparse("offline403",version="v2")
    assert before["pages"][0]["status"]==after["pages"][0]["status"]=="BLOCKED"
    assert not after["observations"]


def test_interrupted_final_gate_publication_recovers_without_refetch(tmp_path,monkeypatch):
    manager,source,plan,pages=setup(tmp_path)
    import amazon_es_bestseller.multisource.snapshots as module
    publish=module.immutable_json; failed=[False]
    def crash(path,value):
        if Path(path).name=="phase_gate.json" and not failed[0]:
            failed[0]=True
            raise OSError("interrupted gate publication")
        return publish(path,value)
    monkeypatch.setattr(module,"immutable_json",crash)
    with pytest.raises(OSError):
        manager.run(plan,[source],run_id="gate-crash",pages=pages)
    bundle=manager.run(plan,[source],run_id="gate-crash",pages=pages,resume=True)
    assert (Path(bundle["path"])/"phase_gate.json").is_file()
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"]==2


def test_fixture_schedule_preserves_madrid_date_at_utc_midnight(tmp_path):
    _,source,plan,pages=setup(tmp_path)
    pages[0]["observed_at"]="2026-10-12T22:30:00Z"
    atomic_json(tmp_path/"plan.json",{"schema_version":1,"origin":"OFFLINE_FIXTURE","plan":plan,"sources":[source],"pages":pages})
    atomic_json(tmp_path/"schedule.json",{"schema_version":1,"enabled":True,"timezone":"Europe/Madrid","days":14,"start_date":"2026-10-12","schedule_id":"dst","plan_file":"plan.json"})
    chosen=daily_plan(tmp_path/"schedule.json",local_date="2026-10-12")
    assert timestamp(chosen["pages"][0]["observed_at"])["local_date"]=="2026-10-12"


def test_parser_identity_correction_cannot_inherit_reviewed_old_product(tmp_path,monkeypatch):
    manager,source,plan,pages=setup(tmp_path)
    original=manager.run(plan,[source],run_id="identity-fix",pages=pages)
    registry=ProductIdentityRegistry(manager.root/"identity")
    base_id=original["observations"][0]["observation_id"]
    registry.review("same",[base_id],actor="human",evidence="Original test item checked")
    old_pid=manager.load("identity-fix")["observations"][0]["product_id"]
    from amazon_es_bestseller.multisource.adapters import BestSellersAdapter
    parse=BestSellersAdapter.parse
    def corrected(self,*args,**kwargs):
        result=parse(self,*args,**kwargs)
        row=result["observations"][0]
        for key in ("asin_from_card","asin_observed","observed_asin","asin_from_raw_url"):
            row[key]="B000000003"
        for key in ("raw_product_url","product_url_raw","normalized_product_url"):
            row[key]="https://www.amazon.es/dp/B000000003"
        return result
    monkeypatch.setattr(BestSellersAdapter,"parse",corrected)
    reparsed=manager.reparse("identity-fix",version="v2")
    changed=reparsed["observations"][0]
    assert changed["product_id"] is None and changed["identity_view_changed"]
    assert changed["observation_id"]==base_id
    registry.review("same",[changed["identity_view_id"]],actor="human",evidence="Corrected item verified separately")
    new=manager.load("identity-fix",version="v2")["observations"][0]
    assert new["product_id"] and new["product_id"]!=old_pid
    assert manager.load("identity-fix")["observations"][0]["product_id"]==old_pid


def test_historical_replay_requires_original_capture_metadata_and_preserves_time(tmp_path):
    manager,source,plan,pages=setup(tmp_path)
    with pytest.raises(ValueError,match="HISTORICAL_CAPTURE_METADATA_REQUIRED"):
        manager.run(plan,[source],run_id="missing-metadata",origin="HISTORICAL_REPLAY",pages=pages)
    metadata=tmp_path/"capture.json"
    atomic_json(metadata,{"observed_at":pages[0]["observed_at"],"page_url":URL,"html_sha256":digest(FIXTURE.read_bytes())})
    pages[0]["capture_metadata_path"]=str(metadata)
    bundle=manager.run(plan,[source],run_id="historical",origin="HISTORICAL_REPLAY",pages=pages)
    assert bundle["manifest"]["local_date"]=="2026-10-12"
    assert bundle["manifest"]["origin"]=="HISTORICAL_REPLAY"
    assert manager.reparse("historical",version="v2")["observations"][0]["observed_at"]==pages[0]["observed_at"]
    pages[0]["observed_at"]="2026-10-13T08:00:00Z"
    with pytest.raises(ValueError,match="METADATA_MISMATCH"):
        manager.run(plan,[source],run_id="bad-redate",origin="HISTORICAL_REPLAY",pages=pages)


def test_global_offline_switch_blocks_live_before_browser_import(tmp_path,capsys):
    _,source,plan,_=setup(tmp_path); plan["live_enabled"]=True
    planfile=tmp_path/"plan.json"; authfile=tmp_path/"auth.json"
    atomic_json(planfile,{"schema_version":1,"run_id":"live-denied","origin":"LIVE_CAPTURE","plan":plan,"sources":[source]})
    atomic_json(authfile,approval(source,plan))
    with pytest.raises(SystemExit) as error:
        main(["--offline","multisource","run","--root",str(tmp_path/"store"),"--plan",str(planfile),"--authorization",str(authfile),"--live"])
    assert error.value.code==2 and "GLOBAL_OFFLINE" in capsys.readouterr().err


def test_stopped_run_has_exportable_failed_stage_diagnostic(tmp_path,monkeypatch,capsys):
    manager,source,plan,_=setup(tmp_path); plan["live_enabled"]=True
    monkeypatch.setattr("amazon_es_bestseller.multisource.snapshots.time.sleep",lambda _:None)
    class Fake:
        def fetch_page(self,url): return TransportResponse(403,url,"Forbidden")
    with pytest.raises(ValueError,match="ACCESS_STOP"):
        manager.run(plan,[source],run_id="stopped",origin="LIVE_CAPTURE",pages=[{"source_id":source["source_id"],"page_url":URL}],authorization=approval(source,plan),transport=Fake())
    bundle=manager.load("stopped")
    assert bundle["manifest"]["frozen"] is False and bundle["quality"]["gate"]=="FAIL"
    assert bundle["quality"]["counts"]["requests"]==1
    assert main(["multisource","exceptions","--root",str(manager.root),"--run-id","stopped","--out",str(tmp_path/"failed_report")])==0
    report=json.loads(capsys.readouterr().out)
    assert Path(report["exceptions"]).is_file()


def test_category_cooldown_deadline_survives_pause_and_reconstruction(tmp_path):
    from amazon_es_bestseller.multisource.snapshots import CheckpointManager
    manager,source,plan,pages=setup(tmp_path)
    manager.run(plan,[source],run_id="cooldown",pages=pages)
    directory=manager._run_dir("cooldown")
    spec=read_sealed(directory/"run_spec.json")
    checkpoint_manager=CheckpointManager(directory)
    checkpoint,_=checkpoint_manager.reconstruct(spec)
    manager.pause("cooldown")
    next_source={**source,"research_category":"办公与文具"}
    assert manager._wait_category(next_source,plan,directory,checkpoint,checkpoint_manager) is False
    deadline=checkpoint["next_request_allowed_at"]
    assert deadline and CheckpointManager(directory).reconstruct(spec)[0]["next_request_allowed_at"]==deadline
    assert (datetime.fromisoformat(deadline.replace("Z","+00:00"))-datetime.fromisoformat(checkpoint["last_completed_at"].replace("Z","+00:00"))).total_seconds()==1800


def test_zero_category_cooldown_requires_specific_approval(tmp_path):
    _,source,plan,_=setup(tmp_path)
    plan.update(live_enabled=True,cooldown_after_category_seconds=0)
    auth=approval(source,plan)
    with pytest.raises(ValueError,match="CATEGORY_COOLDOWN"):
        validate_authorization(plan,auth,[source],{"state":"NORMAL"})
    auth["cooldown_after_category_seconds"]=0
    assert validate_authorization(plan,auth,[source],{"state":"NORMAL"})==auth


def test_interrupted_schema_upgrade_keeps_rollback_journal_and_resumes(tmp_path,monkeypatch):
    import amazon_es_bestseller.multisource.migrations as module
    path=tmp_path/"state.json"
    atomic_json(path,{"schema_version":0,"payload":{"records":[1,2]}})
    original=path.read_bytes(); publish=module.atomic_json; failed=[False]
    def fail_once(destination,value,**kwargs):
        if Path(destination)==path and not failed[0]:
            failed[0]=True
            raise OSError("interrupted state publication")
        return publish(destination,value,**kwargs)
    monkeypatch.setattr(module,"atomic_json",fail_once)
    with pytest.raises(OSError): upgrade_state(path)
    assert path.read_bytes()==original
    assert len(list((tmp_path/"schema_history").glob("*.migration.json")))==1
    result=upgrade_state(path)
    assert read_sealed(path)=={"records":[1,2]}
    rollback_state(result["journal"])
    assert path.read_bytes()==original


def test_replay_cannot_inherit_live_mode_from_plan(tmp_path,capsys):
    _,source,plan,_=setup(tmp_path); plan["live_enabled"]=True
    planfile=tmp_path/"live_plan.json"; authfile=tmp_path/"approved.json"
    atomic_json(planfile,{"schema_version":1,"run_id":"forbidden-replay","origin":"LIVE_CAPTURE","plan":plan,"sources":[source]})
    atomic_json(authfile,approval(source,plan))
    with pytest.raises(SystemExit) as error:
        main(["multisource","replay","--root",str(tmp_path/"denied"),"--plan",str(planfile),"--authorization",str(authfile),"--live"])
    assert error.value.code==2 and "REPLAY_CANNOT_CREATE_LIVE_CAPTURE" in capsys.readouterr().err


def test_known_tracking_aliases_reuse_page_slots_and_observation_budget(tmp_path):
    manager,source,plan,pages=setup(tmp_path)
    pages[0]["page_url"]=URL+"?ref=one"
    pages.append({**pages[0],"page_url":URL+"?ref=two"})
    bundle=manager.run(plan,[source],run_id="tracking-alias",pages=pages)
    assert len(bundle["observations"])==2 and len(bundle["pages"])==1
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"]==2
    assert bundle["quality"]["status"]=="COMPLETE"
    assert "ref=one" in bundle["observations"][0]["page_url_raw"]


def test_duplicate_physical_source_cannot_be_counted_under_two_research_mappings(tmp_path):
    manager,source,plan,pages=setup(tmp_path)
    duplicate={**source,"source_id":source_id("BEST_SELLERS",URL,"居家生活"),"research_category":"居家生活"}
    with pytest.raises(ValueError,match="DUPLICATE_PHYSICAL_SOURCE_MAPPING"):
        manager.run(plan,[source,duplicate],run_id="duplicate-source",pages=pages)
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"]==0


def test_reparse_preserves_original_slot_ids_when_key_algorithm_is_upgraded(tmp_path,monkeypatch):
    import amazon_es_bestseller.multisource.contracts as contracts
    import uuid
    current=contracts.observation_id
    monkeypatch.setattr(contracts,"observation_id",lambda snapshot,source,page,slot:
        "obs_"+uuid.uuid5(uuid.NAMESPACE_URL,"|".join((snapshot,source,page,str(slot)))).hex)
    manager,source,plan,pages=setup(tmp_path); pages[0]["page_url"]=URL+"?ref=old-key"
    original=manager.run(plan,[source],run_id="old-slot-key",pages=pages)
    ids=[row["observation_id"] for row in original["observations"]]
    monkeypatch.setattr(contracts,"observation_id",current)
    updated=manager.reparse("old-slot-key",version="v2")
    assert [row["observation_id"] for row in updated["observations"]]==ids
    assert manager.budget.counters("OFFLINE_FIXTURE")["observation_count"]==2
