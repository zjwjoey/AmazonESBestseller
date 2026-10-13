"""No browser or network: authorization, serial reservations and recovery."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import pytest
from amazon_es_bestseller.highspeed.authorization import validate_authorization, authorize_request
from amazon_es_bestseller.highspeed.budgets import validate_plan, budget_reason, madrid_day
from amazon_es_bestseller.highspeed.runner import HighSpeedRunner
from amazon_es_bestseller.highspeed.schedule import daily_plan, research_window

URL = "https://www.amazon.es/gp/bestsellers/home"
NOW = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)

def make_runner(store, **kwargs):
    kwargs.setdefault('qa_auditor', lambda current_store, run_id: {'gate_passed': False, 'errors': [], 'unique_valid_links': current_store.candidate_count(run_id)})
    return HighSpeedRunner(store, **kwargs)

def plan(tmp_path, stage="T0"):
    html = tmp_path / "saved.html"
    html.write_text("<html>real card fixture</html>", encoding="utf-8")
    return {"mode": "HIGH_SPEED_V2", "stage": stage, "run_id": "test", "live_enabled": stage != "T0",
        "target_unique_links": 30000, "max_duration_hours": 10, "max_requests_per_run": 1000,
        "max_requests_per_day": 1000, "max_pages_per_run": 1000, "max_raw_observations_per_run": 100000,
        "history_observation_limit": 1000000, "request_pacing_seconds": 3, "parallelism": 1,
        "category_cooldown_seconds": 0, "global_access_gate": True, "detail_collection": False,
        "translation": False, "image_download": False, "action_filtering": False,
        "sources": [{"source_id": "home", "source_type": "BEST_SELLERS", "research_category": "home",
                     "page_urls": [URL], "offline_pages": {URL: str(html)}}]}

def approval(p, tmp_path):
    proof = tmp_path / "proof.html"
    proof.write_text("actual reviewed navigation", encoding="utf-8")
    return {**p, "approved": True, "approval_id": "human-1", "approved_by": "tester",
        "store_root": str((tmp_path/"store").resolve()),
        "reviewed_at": (NOW-timedelta(hours=1)).isoformat(), "expires_at": (NOW+timedelta(days=1)).isoformat(),
        "access_gate_state": "NORMAL", "stop_conditions": ["CHALLENGE", "BLOCKED", "RATE_LIMITED", "UNKNOWN", "NETWORK_ERROR"],
        "sources": {"home": {"source_type": "BEST_SELLERS", "research_category": "home", "page_urls": [URL],
          "evidence": {"reviewed": True, "path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}}}}

class Store:
    def __init__(self, root):
        self.root = root; self.root.mkdir()
        self.run = None; self.requests = []; self.responses = []; self.saved_pages = []; self.observations = []; self.events = []; self.gate = {"state": "NORMAL"}
    def begin_run(self, p, *, started_at=None):
        self.run = {"plan": p, "started_at": started_at or NOW.isoformat()}; return self.run
    def get_run(self, run_id): return self.run
    def get_gate(self): return self.gate
    def set_gate(self, state, **kwargs): self.gate = {"state": state, **kwargs}
    def update_run(self, run_id, **kwargs): self.run.update(kwargs)
    def load_run(self, run_id):
        return {"run": self.run, "pages": self.saved_pages, "observations": self.observations,
                "requests": self.requests, "responses": self.responses}
    def reserve_request(self, run_id, request):
        self.requests.append(request); return {**request, "request_id": str(len(self.requests))}
    def save_response(self, run_id, reservation, response):
        path = self.root / (reservation["request_id"] + ".html")
        path.write_text(response["text"], encoding="utf-8")
        receipt = {**response, "kind": reservation["kind"], "html_path": str(path), "evidence_path": str(path)}
        self.responses.append(receipt); return receipt
    def commit_page(self, run_id, key, page, observations):
        if any(existing==page for existing in self.saved_pages): return
        self.saved_pages.append(page); self.observations.extend(observations)
    def append_event(self, run_id, event): self.events.append(event)
    def candidate_count(self, run_id): return len(self.observations)
    def summary(self, run_id):
        return {"requests": len(self.requests), "pages": len(self.saved_pages), "observations": len(self.observations),
                "requests_by_day": {madrid_day(NOW): len(self.requests)}}

def parser(html, **kwargs):
    return {"page": {"actual_count": 1, "completeness": "UNVERIFIED"},
            "observations": [{"raw_product_url": "https://www.amazon.es/dp/example"}]}

def test_new_mode_not_limited_to_legacy_observations(tmp_path):
    p = plan(tmp_path)
    assert validate_plan(p)["max_raw_observations_per_run"] == 100000
    assert budget_reason(p, {"observations": 35000}, now=NOW, kind="ACP") is None

@pytest.mark.parametrize("field,value", [("max_requests_per_run", 0), ("max_pages_per_run", True),
    ("request_pacing_seconds", 1), ("max_duration_hours", float("inf")), ("category_cooldown_seconds", 1800),
    ("translation", True), ("parallelism", 2)])
def test_invalid_or_unbounded_plan_rejected(tmp_path, field, value):
    p = plan(tmp_path); p[field] = value
    with pytest.raises(ValueError): validate_plan(p)

def test_expired_or_non_normal_global_gate_never_authorizes(tmp_path):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path)
    assert validate_authorization(p, a, {"state": "NORMAL"}, now=NOW)
    with pytest.raises(ValueError, match="GLOBAL_ACCESS_GATE"):
        validate_authorization(p, a, {"state": "NETWORK_ERROR"}, now=NOW)
    a["expires_at"] = NOW.isoformat()
    with pytest.raises(ValueError, match="EXPIRED"):
        validate_authorization(p, a, {"state": "NORMAL"}, now=NOW)

def test_t3_cannot_use_t1_approval_or_skip_real_prerequisites(tmp_path):
    p = plan(tmp_path, "T3"); a = approval(p, tmp_path)
    with pytest.raises(ValueError, match="PRIOR_STAGE_REQUIRED:T0"):
        validate_authorization(p, a, {"state": "NORMAL"}, now=NOW)
    a["stage"] = "T1"
    with pytest.raises(ValueError, match="MISMATCH"):
        validate_authorization(p, a, {"state": "NORMAL"}, now=NOW)

def test_opaque_acp_scope_no_generated_url_allowed(tmp_path):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path)
    with pytest.raises(ValueError, match="UNREVIEWED_ACP"):
        authorize_request(URL + "/nextPage", p["sources"][0], a, kind="ACP", descriptor={"method": "POST", "payload": "opaque"})

def test_offline_runner_and_committed_resume_never_repeat_request(tmp_path):
    p = plan(tmp_path); s = Store(tmp_path / "store")
    runner = make_runner(s, clock=lambda: NOW, parser=parser)
    runner.run(p)
    runner.run(p, resume=True)
    assert len(s.requests) == 1 and len(s.observations) == 1
    assert s.run["origin"] == "OFFLINE_FIXTURE"
    assert s.run["stop_reason"] == "SOURCE_PLAN_EXHAUSTED"

def test_crash_after_raw_receipt_reuses_body_before_parser(tmp_path):
    p = plan(tmp_path); s = Store(tmp_path / "store")
    def crash(*args, **kwargs): raise RuntimeError("simulated crash")
    with pytest.raises(RuntimeError): make_runner(s, clock=lambda: NOW, parser=crash).run(p)
    assert len(s.responses) == 1 and not s.saved_pages
    make_runner(s, clock=lambda: NOW, parser=parser).run(p, resume=True)
    assert len(s.requests) == 1 and len(s.observations) == 1

def test_access_restriction_saved_before_parse_and_stops_next_source(tmp_path):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path); s = Store(tmp_path / "store")
    class Restricted:
        def fetch_page(self, url):
            assert len(s.requests) == 1
            return {"status_code": 200, "url": url, "text": "Robot Check"}
    called = []
    result = make_runner(s, clock=lambda: NOW, parser=lambda *args, **kwargs: called.append(1),
        gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=Restricted())
    assert not called and s.gate["state"] == "CHALLENGE"
    assert len(s.responses) == 1 and result["run"]["stop_reason"] == "CHALLENGE"

def test_network_exception_has_immutable_receipt(tmp_path):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path); s = Store(tmp_path / "store")
    class Broken:
        def fetch_page(self, url): raise ConnectionError("fixture failure")
    make_runner(s, clock=lambda: NOW, parser=parser, gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=Broken())
    assert s.responses[0]["status_code"] is None
    assert "ConnectionError" in s.responses[0]["error"]
    assert s.responses[0]["final_url"] is None and s.responses[0]["url"] is None
    assert s.gate["state"] == "NETWORK_ERROR"

def test_resume_wall_clock_includes_interruption(tmp_path):
    p = plan(tmp_path); s = Store(tmp_path / "store"); s.begin_run(p)
    result = make_runner(s, clock=lambda: NOW+timedelta(hours=11), parser=parser).run(p, resume=True)
    assert not s.requests
    assert result["run"]["stop_reason"] == "MAX_DURATION_REACHED"
    assert result["run"]["elapsed_seconds"] == 39600

def test_daily_extension_28_days_disabled_no_fixed14_day_schedule(tmp_path):
    p = plan(tmp_path, "T1")
    daily = daily_plan(p, now=NOW)
    assert not daily["live_enabled"] and not daily["daily_schedule_enabled"]
    next_day = daily_plan(p, now=NOW+timedelta(days=1))
    assert daily["snapshot_id"] == daily["run_id"]
    assert next_day["snapshot_id"] == next_day["run_id"] != daily["run_id"]
    assert len(research_window(now=NOW)) == 28
    assert madrid_day("2026-10-25T22:30:00Z") == "2026-10-25"
    assert madrid_day("2026-10-25T23:30:00Z") == "2026-10-26"
    window = research_window(now="2026-10-24T22:30:00Z")
    assert window[:2] == ["2026-10-25", "2026-10-26"]
    assert len(set(window)) == 28

def two_page_setup(tmp_path):
    p = plan(tmp_path, "T1")
    p["sources"][0]["page_urls"].append(URL + "?pg=2")
    a = approval(p, tmp_path)
    a["sources"]["home"]["page_urls"] = list(p["sources"][0]["page_urls"])
    return p, a

def test_serial_spacing_browser_reused_and_no_category_cooldown(tmp_path):
    p, a = two_page_setup(tmp_path); s = Store(tmp_path / "store")
    current = [NOW]; starts = []; waits = []
    class Normal:
        def fetch_page(self, url):
            starts.append(current[0]); current[0] += timedelta(seconds=1)
            return {"status_code": 200, "url": url, "text": "normal"}
    def sleep(seconds):
        waits.append(seconds); current[0] += timedelta(seconds=seconds)
    make_runner(s, clock=lambda: current[0], sleep=sleep, parser=parser,
        gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=Normal())
    assert len(starts) == 2 and waits == [3]
    assert (starts[1]-starts[0]).total_seconds() == 4

def test_authorization_revalidated_after_wait_before_second_request(tmp_path):
    p, a = two_page_setup(tmp_path); s = Store(tmp_path / "store")
    current = [NOW]; calls = []
    a["expires_at"] = (NOW+timedelta(seconds=2)).isoformat()
    class Normal:
        def fetch_page(self, url):
            calls.append(url); return {"status_code": 200, "url": url, "text": "normal"}
    def sleep(seconds): current[0] += timedelta(seconds=seconds)
    result = make_runner(s, clock=lambda: current[0], sleep=sleep, parser=parser,
        gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=Normal())
    assert len(calls) == 1 and len(s.requests) == 1
    assert "EXPIRED" in result["run"]["stop_reason"]

def test_shared_gate_revalidated_after_wait(tmp_path):
    p, a = two_page_setup(tmp_path); s = Store(tmp_path / "store")
    current = [NOW]; shared = {"state": "NORMAL"}; calls = []
    class Normal:
        def fetch_page(self, url):
            calls.append(url); return {"status_code": 200, "url": url, "text": "normal"}
    def sleep(seconds):
        current[0] += timedelta(seconds=seconds); shared["state"] = "RATE_LIMITED"
    make_runner(s, clock=lambda: current[0], sleep=sleep, parser=parser,
        gate_reader=lambda: shared).run(p, authorization=a, transport=Normal())
    assert len(calls) == 1

def test_budget_exhaustion_stops_before_request_reservation(tmp_path):
    p = plan(tmp_path); s = Store(tmp_path / "store"); s.begin_run(p)
    s.requests = [{"observed_at": NOW.isoformat(), "url": URL}] * 1000
    result = make_runner(s, clock=lambda: NOW, parser=parser).run(p, resume=True)
    assert len(s.requests) == 1000 and not s.responses
    assert result["run"]["stop_reason"] == "RUN_REQUEST_BUDGET_EXHAUSTED"

def test_unreviewed_redirect_saves_response_but_never_parses(tmp_path):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path); s = Store(tmp_path / "store")
    class Redirect:
        def fetch_page(self, url): return {"status_code": 200, "url": "https://www.amazon.es/dp/unreviewed", "text": "normal"}
    result = make_runner(s, clock=lambda: NOW, parser=parser,
        gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=Redirect())
    assert len(s.responses) == 1 and not s.observations
    assert s.gate["state"] == "UNKNOWN"
    assert result["run"]["stop_reason"] == "UNREVIEWED_PAGE_URL"

def test_acp_failure_keeps_base_cards_and_partial_evidence(tmp_path):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path); s = Store(tmp_path / "store")
    endpoint = "https://www.amazon.es/acp/real-visible/nextPage"
    a["sources"]["home"].update(allow_acp=True, acp_urls=[endpoint])
    class ACPRestricted:
        def fetch_page(self, url): return {"status_code": 200, "url": url, "text": "normal"}
        def fetch_ajax(self, url, **kwargs): return {"status_code": 429, "url": url, "text": "rate limited"}
    def waiting(html, **kwargs):
        result = parser(html, **kwargs)
        result["page"]["acp_pending"] = {"url": endpoint, "method": "POST", "payload": "real opaque browser payload"}
        return result
    current = [NOW]
    make_runner(s, clock=lambda: current[0], sleep=lambda secs: current.__setitem__(0,current[0]+timedelta(seconds=secs)),
        parser=waiting, gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=ACPRestricted())
    assert len(s.observations) == 1 and len(s.responses) == 2
    assert s.saved_pages[0]["status"] == "PARTIAL" and not s.saved_pages[0]["page_complete"]
    assert s.run["stop_reason"] == "RATE_LIMITED"

def test_true_prior_stages_require_saved_receipt_body_not_labels(tmp_path):
    from amazon_es_bestseller.highspeed.authorization import verified_stage_artifact
    artifact = tmp_path / "artifact.json"; artifact.write_text("{}", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    value = {"origin": "LIVE_CAPTURE", "status_code": 200, "access_state": "NORMAL",
             "request_url": URL, "final_url": URL, "observed_at": NOW.isoformat()}
    receipt.write_text(json.dumps(value), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"stage": "T1", "origin": "LIVE_CAPTURE", "passed": True,
        "quality_gate_passed": True, "unique_valid_links": 1,
        "artifacts": [{"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}],
        "request_receipts": [{"path": str(receipt), "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}]}),encoding="utf-8")
    reference = {"path": str(gate), "sha256": hashlib.sha256(gate.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="PRIOR_STAGE_MANIFEST_REQUIRED"):
        verified_stage_artifact(reference, "T1")

def test_stage_t0_gate_recomputes_actual_artifact_pool(tmp_path):
    from amazon_es_bestseller.highspeed.authorization import verified_stage_artifact
    from amazon_es_bestseller.highspeed.links import candidate_id
    p = plan(tmp_path)
    p["run_id"] = "prior_t0"
    url = "https://www.amazon.es/dp/visible-link"
    values = {"manifest": {"run_id": "prior_t0", "stage": "T0", "origin": "OFFLINE_FIXTURE", "plan": p},
        "quality_report": {"run_id": "prior_t0", "gate_passed": True, "unique_valid_links": 1}}
    refs = {}
    for name, value in values.items():
        path = tmp_path / (name+".json"); path.write_text(json.dumps(value), encoding="utf-8")
        refs[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    rows = {"observations": [{"run_id": "prior_t0", "origin": "OFFLINE_FIXTURE", "source_id": "home",
                "source_type": "BEST_SELLERS", "raw_product_url": url}],
            "candidates": [{"candidate_id": candidate_id(url), "normalized_product_url": url}]}
    for name, entries in rows.items():
        path = tmp_path / (name+".jsonl"); path.write_text("\n".join(json.dumps(r) for r in entries), encoding="utf-8")
        refs[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    record = {"stage": "T0", "origin": "OFFLINE_FIXTURE", "run_id": "prior_t0", "passed": True,
        "quality_gate_passed": True, "unique_valid_links": 1, **refs, "artifacts": list(refs.values())}
    path = tmp_path / "stagegate.json"; path.write_text(json.dumps(record), encoding="utf-8")
    reference = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert verified_stage_artifact(reference, "T0")["unique_valid_links"] == 1
    record["unique_valid_links"] = 30000
    path.write_text(json.dumps(record), encoding="utf-8")
    reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="DECLARED_LINK_COUNT"):
        verified_stage_artifact(reference, "T0")

def test_hash_correct_zero_card_live_receipt_cannot_pass_t3_prerequisite(tmp_path):
    from amazon_es_bestseller.highspeed.authorization import verified_stage_artifact
    from amazon_es_bestseller.highspeed.links import candidate_id
    p = plan(tmp_path, "T1"); p["run_id"] = "prior_t1"
    types = ["BEST_SELLERS", "MOVERS_SHAKERS", "NEW_RELEASES", "MOST_WISHED_FOR", "MOST_GIFTED"]
    paths = ["bestsellers", "movers-and-shakers", "new-releases", "most-wished-for", "most-gifted"]
    p["sources"] = [{"source_id": t, "source_type": t, "research_category": "home", "page_urls": ["https://www.amazon.es/gp/"+u]} for t,u in zip(types,paths)]
    refs = {}
    def save(name, value, jsonl=False):
        path = tmp_path / (name+(".jsonl" if jsonl else ".json"))
        path.write_text("\n".join(json.dumps(r) for r in value) if jsonl else json.dumps(value), encoding="utf-8")
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    rows = [{"run_id": "prior_t1", "origin": "LIVE_CAPTURE", "source_id": t, "source_type": t,
             "raw_product_url": "https://www.amazon.es/dp/visible-"+str(i)} for i,t in enumerate(types)]
    prior_root = str((tmp_path / "prior_store").resolve())
    refs["manifest"] = save("manifest", {"run_id": "prior_t1", "stage": "T1", "origin": "LIVE_CAPTURE", "plan": p, "store_root": prior_root})
    refs["quality_report"] = save("quality", {"run_id": "prior_t1", "gate_passed": True, "unique_valid_links": 5})
    refs["observations"] = save("observations", rows, True)
    refs["candidates"] = save("candidates", [{"candidate_id": candidate_id(r["raw_product_url"]), "normalized_product_url": r["raw_product_url"]} for r in rows], True)
    source = p["sources"][0]; url = source["page_urls"][0]
    body = tmp_path / "zero_cards.html"; body.write_text("<html><h1>Los más vendidos</h1></html>", encoding="utf-8")
    receipt = {"run_id": "prior_t1", "source_id": source["source_id"], "source_type": source["source_type"],
        "origin": "LIVE_CAPTURE", "status_code": 200, "access_state": "NORMAL", "observed_at": NOW.isoformat(),
        "request_url": url, "final_url": url, "html_path": str(body), "sha256": hashlib.sha256(body.read_bytes()).hexdigest(), "kind": "PAGE"}
    refs["authorization"] = save("authorization", {"approved": True, "stage": "T1", "run_id": "prior_t1",
        "store_root": prior_root,
        "approval_id": "offline-test", "approved_by": "fixture", "reviewed_at": (NOW-timedelta(hours=1)).isoformat(),
        "expires_at": (NOW+timedelta(hours=1)).isoformat(), "sources": {source["source_id"]: {"page_urls": [url],
            "source_type": source["source_type"], "research_category": source["research_category"],
            "evidence": {"reviewed": True, "path": str(body), "sha256": hashlib.sha256(body.read_bytes()).hexdigest()}}}})
    request_ref = save("receipt", receipt)
    record = {"stage": "T1", "run_id": "prior_t1", "origin": "LIVE_CAPTURE", "passed": True,
        "quality_gate_passed": True, "unique_valid_links": 5, **refs, "artifacts": list(refs.values()), "request_receipts": [request_ref]}
    reference = save("gate", record)
    with pytest.raises(ValueError, match="LIVE_PAGE_QA_NOT_COMPLETE"):
        verified_stage_artifact(reference, "T1")

def test_finished_plan_is_not_a_quality_gate_pass(tmp_path):
    p = plan(tmp_path); p["target_unique_links"] = 1
    s = Store(tmp_path / "store")
    result = make_runner(s, clock=lambda: NOW, parser=parser,
        qa_auditor=lambda current_store, run_id: {"gate_passed": False, "errors": [], "unique_valid_links": 1, "unknown_pages": 1}).run(p)
    assert result["run"]["status"] == "FINISHED"
    assert result["run"]["quality_gate_passed"] is False
    assert result["run"]["qa"]["unknown_pages"] == 1

@pytest.mark.parametrize("history_version", [None, "link_url_v2.1", "current"])
def test_resume_rebases_corrected_progress_without_erasing_legacy_history(tmp_path, history_version):
    from amazon_es_bestseller.highspeed.links import NORMALIZER_VERSION, normalize_product_url
    aliases=[normalize_product_url(url) for url in [
        "https://www.amazon.es/dp/opaque-token/session-a",
        "https://www.amazon.es/gp/product/opaque-token/session-b"]]
    assert aliases[0]["normalized_product_url"] != aliases[1]["normalized_product_url"]
    assert aliases[0]["possible_duplicate_key"] == aliases[1]["possible_duplicate_key"]
    p=plan(tmp_path,"T1"); p["target_unique_links"]=5000
    a=approval(p,tmp_path)
    class CorrectedStore(Store):
        def candidate_count(self,run_id): return 4351+len(self.observations)
        def load_run(self,run_id): return {**super().load_run(run_id),"events":list(self.events)}
    s=CorrectedStore(tmp_path/"store"); s.begin_run(p,started_at=(NOW-timedelta(hours=1)).isoformat())
    old={"kind":"PROGRESS","elapsed_seconds":3500,"unique_links":5018}
    if history_version is not None:
        old["normalizer_version"]=NORMALIZER_VERSION if history_version == "current" else history_version
    if history_version == "current": old["unique_links"]=4350
    s.events.append(old.copy()); observed=[]
    class Normal:
        def fetch_page(self,url): return {"status_code":200,"url":url,"text":"<h1>Los más vendidos</h1>"}
    result=make_runner(s,clock=lambda:NOW,parser=parser,gate_reader=lambda:{"state":"NORMAL"},
        progress=observed.append).run(p,authorization=a,transport=Normal(),resume=True)
    assert len(s.requests) == 1 and result["run"]["stop_reason"] != "Progress must be monotonic"
    assert s.events[0] == old
    assert observed[-1]["unique_links"] == 4352
    assert observed[-1]["normalizer_version"] == NORMALIZER_VERSION
    assert observed[-1]["elapsed_seconds"] == 3600
    rebases=[e for e in s.events if e["kind"] == "NORMALIZATION_PROGRESS_REBASE"]
    assert bool(rebases) == (history_version != "current")
    if rebases:
        assert rebases[0]["unique_links"] == 4351
        assert observed[-1]["rolling_unique_per_hour"] == 0
        assert observed[-1]["eta_remaining_seconds"] is None

def test_normal_empty_root_continues_only_approved_leaf_preserving_failed_qa(tmp_path):
    root = "https://www.amazon.es/gp/bestsellers"
    p = plan(tmp_path, "T1"); p["sources"][0]["page_urls"] = [root, URL]
    a = approval(p, tmp_path); a["sources"]["home"]["page_urls"] = [root, URL]
    s = Store(tmp_path / "store"); current = [NOW]; fetched = []
    leaf = "<h1>Los más vendidos</h1><div data-expected-count='2'><div data-ranking-card='true'><span class='zg-bdg-text'>#1</span><a href='/dp/visible-one'>One</a></div><div data-ranking-card='true'><span class='zg-bdg-text'>#2</span><a href='/dp/visible-two'>Two</a></div></div>"
    class Normal:
        def fetch_page(self, url):
            fetched.append(url)
            return {"status_code": 200, "url": url, "text": "<h1>Los más vendidos</h1>" if url == root else leaf}
    result = make_runner(s, clock=lambda: current[0], sleep=lambda seconds: current.__setitem__(0, current[0]+timedelta(seconds=seconds)),
        gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=Normal())
    assert fetched == [root, URL] and len(s.observations) == 2
    assert s.saved_pages[0]["status"] == "FAILED"
    assert any(e["kind"] == "SOURCE_NAVIGATION_ONLY" for e in s.events)
    assert result["run"]["quality_gate_passed"] is False

def test_wrong_type_root_still_stops_before_approved_leaf(tmp_path):
    root = "https://www.amazon.es/gp/bestsellers"
    p = plan(tmp_path, "T1"); p["sources"][0]["page_urls"] = [root, URL]
    a = approval(p, tmp_path); a["sources"]["home"]["page_urls"] = [root, URL]
    s = Store(tmp_path / "store"); fetched = []
    class WrongType:
        def fetch_page(self, url):
            fetched.append(url)
            return {"status_code": 200, "url": url, "text": "<h1>Los más regalados</h1>"}
    make_runner(s, clock=lambda: NOW, gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=WrongType())
    assert fetched == [root]
    assert not any(e["kind"] == "SOURCE_NAVIGATION_ONLY" for e in s.events)

@pytest.mark.parametrize("damage", [None, "missing_navigation", "expected_count", "nonempty_content"])
def test_saved_dmusic_navigation_root_continues_but_malformed_leaf_stops(tmp_path, damage):
    from bs4 import BeautifulSoup
    root = "https://www.amazon.es/gp/bestsellers/dmusic"
    leaf = root+"/digital-music-track"
    body = (Path(__file__).parent/"fixtures/highspeed/night5000_dmusic_navigation_only.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(body,"lxml")
    if damage == "missing_navigation":
        soup.select_one("#zg-left-col").decompose()
    elif damage == "expected_count":
        soup.select_one("#zg-right-col")["data-expected-count"]="50"
    elif damage == "nonempty_content":
        soup.select_one("#zg-right-col").append(soup.new_tag("div"))
    body = str(soup)
    p=plan(tmp_path,"T1"); p["sources"][0].update(actual_source_url=root,page_urls=[root])
    p["sources"].append({"source_id":"track","source_type":"BEST_SELLERS","research_category":"home",
        "actual_source_url":leaf,"page_urls":[leaf]})
    a=approval(p,tmp_path); a["sources"]["home"]["page_urls"]=[root]
    a["sources"]["track"]={**a["sources"]["home"],"page_urls":[leaf]}
    s=Store(tmp_path/"store"); current=[NOW]; fetched=[]
    class Normal:
        def fetch_page(self,url):
            fetched.append(url)
            text=body if url == root else "<h1>Los más vendidos en Canciones</h1><div data-expected-count='1'><div data-ranking-card='true'><span class='zg-bdg-text'>#1</span><a href='/dp/visible-track'>Track</a></div></div>"
            return {"status_code":200,"url":url,"text":text}
    result=make_runner(s,clock=lambda:current[0],sleep=lambda seconds:current.__setitem__(0,current[0]+timedelta(seconds=seconds)),
        gate_reader=lambda:{"state":"NORMAL"}).run(p,authorization=a,transport=Normal())
    assert fetched == ([root,leaf] if damage is None else [root])
    assert s.saved_pages[0]["status"] == "FAILED"
    assert len(s.observations) == (1 if damage is None else 0)
    assert any(e["kind"] == "SOURCE_NAVIGATION_ONLY" for e in s.events) == (damage is None)
    assert result["run"]["quality_gate_passed"] is False
    # Simulate the pre-fix checkpoint: FAILED, without a navigation decision.
    s.saved_pages[0].pop("source_navigation_only",None)
    fetched.clear()
    resumed=make_runner(s,clock=lambda:current[0],gate_reader=lambda:{"state":"NORMAL"}).run(
        p,authorization=a,transport=Normal(),resume=True)
    assert fetched == []  # Reparse the saved normal receipt, never re-request it.
    if damage is None:
        assert any(page.get("source_navigation_only") is True for page in s.saved_pages)
        # A saved, proven navigation-only decision may subsequently be skipped.
        pages_before=len(s.saved_pages)
        make_runner(s,clock=lambda:current[0],gate_reader=lambda:{"state":"NORMAL"}).run(
            p,authorization=a,transport=Normal(),resume=True)
        assert len(s.saved_pages) == pages_before and fetched == []
    else:
        assert resumed["run"]["stop_reason"] == "PAGE_PARSE_OR_ACCESS_FAILED"

@pytest.mark.parametrize("independent", [False, True])
def test_gate_target_accounting_revalidates_journals_and_keeps_unknown_alias_conservative(tmp_path, independent):
    from test_highspeed_reviews import example
    from amazon_es_bestseller.highspeed.authorization import reviewed_target_count
    from amazon_es_bestseller.highspeed.links import candidate_id, normalize_product_url
    from amazon_es_bestseller.highspeed.reviews import validate_destination_review
    from amazon_es_bestseller.multisource.storage import immutable_json, sealed
    options = {"decision": "VERIFIED_INDEPENDENT_PURCHASABLE", "finals": ["https://www.amazon.es/dp/token?choice=blue", "https://www.amazon.es/dp/token?choice=red"]} if independent else {}
    review, candidates, _ = example(tmp_path, **options)
    alias = "https://www.amazon.es/dp/opaque-token?unverified_extra=1"
    candidates.append({"candidate_id": candidate_id(alias), "normalized_product_url": alias, "target_eligible": True})
    for final in options.get("finals", ["https://www.amazon.es/dp/actual-destination"]):
        candidates.append({"candidate_id": candidate_id(final), "normalized_product_url": final, "target_eligible": True})
    recomputed = [{**c, "possible_duplicate_key": normalize_product_url(c["normalized_product_url"])["possible_duplicate_key"]} for c in candidates]
    projection = validate_destination_review(review, candidates)
    journal = tmp_path / "review.journal.json"
    immutable_json(journal, sealed({"run_id": "proof_run", "review": review, "projection": projection}))
    for candidate in candidates[:2]:
        candidate.update(review_journal_path=str(journal), review_journal_sha256=hashlib.sha256(journal.read_bytes()).hexdigest())
    assert reviewed_target_count(candidates, recomputed, "proof_run") == (2 if independent else 1)
    journal.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        reviewed_target_count(candidates, recomputed, "proof_run")

def test_new_run_honors_previous_global_request_interval(tmp_path):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path); s = Store(tmp_path / "store")
    s.latest_request_time = lambda: NOW.isoformat()
    current = [NOW]; waits = []; fetch_times = []
    class Normal:
        def fetch_page(self, url):
            fetch_times.append(current[0]); return {"status_code": 200, "url": url, "text": "normal"}
    def sleep(seconds):
        waits.append(seconds); current[0] += timedelta(seconds=seconds)
    make_runner(s, clock=lambda: current[0], sleep=sleep, parser=parser, gate_reader=lambda: {"state": "NORMAL"}).run(p, authorization=a, transport=Normal())
    assert waits == [3] and fetch_times == [NOW+timedelta(seconds=3)]

def test_shared_source_proof_hashed_once_per_validation_and_rechecked_next_call(tmp_path, monkeypatch):
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path)
    p["sources"].append({**p["sources"][0], "source_id": "second", "page_urls": [URL+"?pg=2"]})
    a["sources"]["second"] = {**a["sources"]["home"], "page_urls": [URL+"?pg=2"]}
    proof_path = Path(a["sources"]["home"]["evidence"]["path"]).resolve()
    original = Path.read_bytes; reads = []
    def tracked(path):
        if path.resolve() == proof_path:
            reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", tracked)
    validate_authorization(p, a, {"state": "NORMAL"}, now=NOW)
    assert len(reads) == 1
    proof_path.write_text("changed proof", encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_PROOF_HASH_MISMATCH"):
        validate_authorization(p, a, {"state": "NORMAL"}, now=NOW)
    assert len(reads) == 2

def test_prepare_contract_error_preserves_normal_base_cards_before_stop(tmp_path):
    p=plan(tmp_path,"T1"); a=approval(p,tmp_path); a["sources"]["home"]["allow_native_acp"]=True
    s=Store(tmp_path/"store")
    class NoNative:
        def fetch_page(self,url): return {"status_code":200,"url":url,"text":"normal base cards"}
        def prepare_ajax_descriptor(self,descriptor,*,referer):
            raise ValueError("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED")
    def pending_parser(html,**kwargs):
        result=parser(html,**kwargs)
        result["page"]["acp_pending"]={"url":None,"request_contract":"NATIVE_REQUEST_DISCOVERY_REQUIRED"}
        return result
    result=make_runner(s,clock=lambda:NOW,parser=pending_parser,gate_reader=lambda:{"state":"NORMAL"}).run(p,authorization=a,transport=NoNative())
    assert len(s.observations)==1 and len(s.requests)==1
    assert s.saved_pages[0]["status"]=="PARTIAL" and not s.saved_pages[0]["page_complete"]
    assert result["run"]["stop_reason"]=="ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED"
    assert any(e["kind"]=="ACP_PREPARE" and e["success"] is False for e in s.events)

@pytest.mark.parametrize("main_heading",["Los más vendidos","Unknown list"])
def test_gifted_wrong_main_heading_cannot_be_rescued_by_unrelated_footer(main_heading):
    from amazon_es_bestseller.highspeed.adapters import parse_page
    url="https://www.amazon.es/gp/most-gifted/amazon-devices"
    html="<h1>Unknown banner</h1><div id='zg-right-col'><div class='_cDEzb_card-title_real'><h1>"+main_heading+"</h1></div><footer><h1>Los más regalados</h1></footer></div>"
    parsed=parse_page(html,source={"source_id":"gifted","source_type":"MOST_GIFTED","actual_source_url":url},
        run_id="test",page_url=url,page_number=1,evidence_path="offline-fixture",observed_at=NOW.isoformat())
    assert parsed['page']['source_type_verified'] is False

@pytest.mark.parametrize("failure,access,continues",[
    ("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED","NORMAL",True),
    ("ACP_NATIVE_BATCH_PROOF_INVALID","NORMAL",False),
    ("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED","UNKNOWN",False)])
def test_zero_card_native_unavailable_is_terminal_failed_only_with_normal_source_proof(tmp_path,failure,access,continues):
    from amazon_es_bestseller.highspeed.adapters import parse_page
    empty="https://www.amazon.es/gp/new-releases/boost"
    body=(Path(__file__).parent/"fixtures/highspeed/continue30000_new_releases_boost_zero_cards.html").read_text(encoding="utf-8")
    p=plan(tmp_path,"T1");p["sources"][0].update(source_type="NEW_RELEASES",actual_source_url=empty,page_urls=[empty])
    p["sources"].append({"source_id":"next","source_type":"BEST_SELLERS","research_category":"home", "actual_source_url":URL,"page_urls":[URL]})
    a=approval(p,tmp_path);a["sources"]["home"].update(source_type="NEW_RELEASES",page_urls=[empty],allow_native_acp=True)
    a["sources"]["next"]={**a["sources"]["home"],"source_type":"BEST_SELLERS","page_urls":[URL]}
    s=Store(tmp_path/"store");fetched=[];current=[NOW]
    class Missing:
        def fetch_page(self,url):
            fetched.append(url)
            return {"status_code":200,"url":url,"text":body if url==empty else "<h1>Los más vendidos</h1><div data-expected-count='1'><div class='ranking-card'><span class='zg-bdg-text'>#1</span><a href='/product/next'>Next</a></div></div>"}
        def prepare_ajax_descriptor(self,descriptor,*,referer):raise ValueError(failure)
    def actual(html,**kwargs):
        parsed=parse_page(html,**kwargs)
        if kwargs["page_url"]==empty:parsed["page"]["access_status"]=access
        return parsed
    runner=make_runner(s,clock=lambda:current[0],sleep=lambda seconds:current.__setitem__(0,current[0]+timedelta(seconds=seconds)),
        parser=actual,gate_reader=lambda:{"state":"NORMAL"})
    result=runner.run(p,authorization=a,transport=Missing())
    if continues:
        assert fetched==[empty,URL] and len(s.observations)==1
        assert s.saved_pages[0]["status"]=="FAILED" and s.saved_pages[0]["actual_count"]==0
        assert s.saved_pages[0]["terminal_for_snapshot"] is True
        assert s.saved_pages[0]["completeness"]=="UNKNOWN" and not result["run"]["quality_gate_passed"]
        # Reproduce the old checkpoint lacking a terminal decision. Resume must
        # re-audit its saved normal HTML and miss reason without another PAGE.
        s.saved_pages[0].update(status="PARTIAL",completeness="PARTIAL",terminal_for_snapshot=False)
        runner.run(p,authorization=a,transport=Missing(),resume=True)
        assert fetched==[empty,URL] and len(s.requests)==2
        assert s.saved_pages[-1]["status"]=="FAILED" and s.saved_pages[-1]["terminal_for_snapshot"] is True
        assert any(e.get("cached_failure_reaudited") for e in s.events)
    else:
        assert fetched==[empty] and result["run"]["stop_reason"]==failure
        assert not s.saved_pages[0].get("terminal_for_snapshot")

@pytest.mark.parametrize("damage",[None,"message_removed","nonempty_recs","product_link","unknown_access"])
@pytest.mark.parametrize("gifted",[False,True,"best"])
def test_official_empty_wished_grid_continues_but_unproven_empty_grid_stops(tmp_path,damage,gifted):
    from amazon_es_bestseller.highspeed.adapters import parse_page
    empty="https://www.amazon.es/gp/most-wished-for/dvd"
    kind="MOST_GIFTED" if gifted else "MOST_WISHED_FOR"
    if gifted:empty="https://www.amazon.es/gp/most-gifted/amazon-devices"
    name="continue30000_gifted_devices_empty.html" if gifted else "continue30000_wished_dvd_explicit_empty.html"
    if gifted=="best":
        kind="BEST_SELLERS";empty="https://www.amazon.es/gp/bestsellers/luggage";name="continue30000_best_luggage_empty.html"
    body=(Path(__file__).parent/"fixtures/highspeed"/name).read_text(encoding="utf-8")
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(body,"lxml");grid=soup.select_one('.p13n-desktop-grid')
    if damage=="message_removed":grid.select_one('h4').decompose()
    if damage=="nonempty_recs":grid['data-client-recs-list']='[{"opaque":"not-empty"}]'
    if damage=="product_link":
        link=soup.new_tag('a',href='/product/unparsed-product');link.string='Product';grid.append(link)
    body=str(soup)
    p=plan(tmp_path,"T1");p["sources"][0].update(source_type=kind,actual_source_url=empty,page_urls=[empty])
    p["sources"].append({"source_id":"next","source_type":"BEST_SELLERS","research_category":"home","actual_source_url":URL,"page_urls":[URL]})
    a=approval(p,tmp_path);a["sources"]["home"].update(source_type=kind,page_urls=[empty])
    a["sources"]["next"]={**a["sources"]["home"],"source_type":"BEST_SELLERS","page_urls":[URL]}
    s=Store(tmp_path/"store");fetched=[];current=[NOW]
    class Normal:
        def fetch_page(self,url):
            fetched.append(url)
            return {"status_code":200,"url":url,"text":body if url==empty else "<h1>Los más vendidos</h1><div data-expected-count='1'><div class='ranking-card'><span class='zg-bdg-text'>#1</span><a href='/product/next'>Next</a></div></div>"}
    def actual(html,**kwargs):
        parsed=parse_page(html,**kwargs)
        if kwargs["page_url"]==empty and damage=="unknown_access":parsed['page']['access_status']='UNKNOWN'
        return parsed
    runner=make_runner(s,clock=lambda:current[0],sleep=lambda seconds:current.__setitem__(0,current[0]+timedelta(seconds=seconds)),
        parser=actual,gate_reader=lambda:{"state":"NORMAL"})
    result=runner.run(p,authorization=a,transport=Normal())
    assert fetched==([empty,URL] if damage is None else [empty])
    assert s.saved_pages[0]['status']=='FAILED' and s.saved_pages[0]['actual_count']==0
    if damage is None:
        assert s.saved_pages[0]['source_empty'] is True and s.saved_pages[0]['terminal_for_snapshot'] is True
        assert s.saved_pages[0]['completeness']=='UNKNOWN' and result['run']['quality_gate_passed'] is False
        s.saved_pages[0].update(source_empty=False,terminal_for_snapshot=False)
        if gifted is True:s.saved_pages[0].update(source_type_verified=False,flags=['WRONG_PAGE_TYPE'])
        runner.run(p,authorization=a,transport=Normal(),resume=True)
        assert fetched==[empty,URL] and len(s.requests)==2
        assert s.saved_pages[-1]['source_empty'] is True
    else:assert not s.saved_pages[0].get('terminal_for_snapshot')

@pytest.mark.parametrize("failure,normal,continues", [
    ("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED",True,True),
    ("ACP_NATIVE_BATCH_PROOF_INVALID",True,False),
    ("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED",False,False)])
def test_saved_real_missing_endpoint_prefix_can_advance_only_when_verified_normal(tmp_path,failure,normal,continues):
    from amazon_es_bestseller.highspeed.adapters import parse_page
    fixture=Path(__file__).parent/"fixtures/highspeed/night5000_page2_missing_acp_path.html"
    pg2="https://www.amazon.es/gp/bestsellers/kitchen/2822691031?ie=UTF8&pg=2"
    p=plan(tmp_path,"T1")
    p["sources"][0].update(actual_source_url=pg2,page_urls=[pg2])
    p["sources"].append({"source_id":"next","source_type":"BEST_SELLERS","research_category":"home","actual_source_url":URL,"page_urls":[URL]})
    a=approval(p,tmp_path); a["sources"]["home"].update(page_urls=[pg2],allow_native_acp=True)
    a["sources"]["next"]={**a["sources"]["home"],"page_urls":[URL]}
    s=Store(tmp_path/"store"); fetched=[]; current=[NOW]
    leaf="<h1>Los más vendidos</h1><div data-expected-count='2'><div data-ranking-card='true'><span class='zg-bdg-text'>#1</span><a href='/dp/new-one'>One</a></div><div data-ranking-card='true'><span class='zg-bdg-text'>#2</span><a href='/dp/new-two'>Two</a></div></div>"
    class NoNative:
        def fetch_page(self,url):
            fetched.append(url)
            return {"status_code":200,"url":url,"text":fixture.read_text(encoding="utf-8") if url==pg2 else leaf}
        def prepare_ajax_descriptor(self,descriptor,*,referer): raise ValueError(failure)
    def actual_parser(html,**kwargs):
        result=parse_page(html,**kwargs)
        if kwargs["page_url"]==pg2 and not normal: result["page"]["access_status"]="UNKNOWN"
        return result
    runner=make_runner(s,clock=lambda:current[0],sleep=lambda seconds:current.__setitem__(0,current[0]+timedelta(seconds=seconds)),
        parser=actual_parser,gate_reader=lambda:{"state":"NORMAL"})
    result=runner.run(p,authorization=a,transport=NoNative())
    assert s.saved_pages[0]["status"]=="PARTIAL" and not s.saved_pages[0]["page_complete"]
    if continues:
        assert fetched==[pg2,URL] and len(s.observations)==32
        assert s.saved_pages[0]["continuation_unavailable"] is True
        assert s.saved_pages[0]["terminal_for_snapshot"] is True and s.saved_pages[0]["completeness"]=="UNKNOWN"
        assert any(e["kind"]=="SOURCE_CONTINUATION_UNAVAILABLE" for e in s.events)
        assert result["run"]["quality_gate_passed"] is False
        runner.run(p,authorization=a,transport=NoNative(),resume=True)
        assert fetched==[pg2,URL] and len(s.requests)==2
    else:
        assert fetched==[pg2] and len(s.observations)==30
        assert result["run"]["stop_reason"]==failure
        assert not s.saved_pages[0].get("terminal_for_snapshot")

def test_runner_with_durable_real_store_resume_and_raw_receipts(tmp_path):
    from amazon_es_bestseller.highspeed.store import RunStore
    p = plan(tmp_path)
    html="<h1>Los más vendidos</h1><div data-expected-count='1'><div data-ranking-card='true'><span class='zg-bdg-text'>#1</span><a href='/dp/visible-real'>Real ranked fixture item</a></div></div>"
    Path(p["sources"][0]["offline_pages"][URL]).write_text(html,encoding="utf-8")
    store = RunStore(tmp_path / "durable")
    try:
        first = make_runner(store).run(p)
        second = make_runner(store).run(p, resume=True)
        assert first["manifest"]["requests"] == second["manifest"]["requests"] == 1
        assert second["manifest"]["observations"] == 1
        assert second["manifest"]["unique_links"] == 1
        receipt = second["responses"][0]
        assert Path(receipt["html_path"]).read_text(encoding="utf-8") == html
        assert receipt["origin"] == "OFFLINE_FIXTURE"
    finally:
        store.close()

def test_new_live_restriction_updates_shared_gate_without_erasing_history(tmp_path):
    from amazon_es_bestseller.multisource.storage import immutable_json, sealed, read_sealed
    p = plan(tmp_path, "T1"); a = approval(p, tmp_path); s = Store(tmp_path / "store")
    shared = tmp_path / "shared_access_gate.json"
    immutable_json(shared, sealed({"state": "NORMAL", "human_record": "previous approval preserved"}))
    p["shared_access_gate_path"] = str(shared)
    a["shared_access_gate_path"] = str(shared.resolve())
    class Limited:
        def fetch_page(self, url): return {"status_code": 403, "url": url, "text": "blocked"}
    make_runner(s, clock=lambda: NOW, parser=parser).run(p, authorization=a, transport=Limited())
    assert read_sealed(shared)["state"] == "BLOCKED"
    history = list((tmp_path / "highspeed_gate_history").glob("*.json"))
    assert len(history) == 1 and read_sealed(history[0])["state"] == "NORMAL"
    assert read_sealed(history[0])["human_record"] == "previous approval preserved"

def test_origin_spoof_rejected_before_any_reservation(tmp_path):
    p = plan(tmp_path); p["origin"] = "LIVE_CAPTURE"
    s = Store(tmp_path / "store")
    with pytest.raises(ValueError, match="STAGE_ORIGIN_MISMATCH"):
        make_runner(s, clock=lambda: NOW).run(p)
    assert not s.requests

def test_historical_replay_preserves_original_time_and_requires_hash(tmp_path):
    p = plan(tmp_path); p["origin"] = "HISTORICAL_REPLAY"
    body_path = Path(p["sources"][0]["offline_pages"][URL])
    original = "2025-06-01T12:30:00Z"
    p["sources"][0]["offline_metadata"] = {URL: {"page_url": URL,
        "html_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(), "observed_at": original}}
    s = Store(tmp_path / "store")
    make_runner(s, clock=lambda: NOW, parser=parser).run(p)
    assert s.responses[0]["observed_at"] == original
    assert s.responses[0]["origin"] == "HISTORICAL_REPLAY"
    p["run_id"] = "bad"
    p["sources"][0]["offline_metadata"][URL]["html_sha256"] = "wrong"
    bad = Store(tmp_path / "badstore")
    make_runner(bad, clock=lambda: NOW, parser=parser).run(p)
    assert not bad.observations and bad.run["stop_reason"] == "NETWORK_ERROR"

