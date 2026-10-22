"""Explicit delegated rechecks never retry challenges or invent human handling."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.recovery import recheck_network_error
from amazon_es_bestseller.multisource.storage import atomic_json,digest,immutable_json,read_sealed,sealed

URL="https://www.amazon.es/gp/bestsellers"
HTML='<h1>Los más vendidos</h1><div id="zg" data-expected-count="1"><div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/product/actual-link">Item</a></div></div>'

def setup(tmp_path,state="NETWORK_ERROR"):
    store=RunStore(tmp_path/"store")
    shared=tmp_path/"shared"/"access_gate.json"
    prior={"state":state,"observed_at":"2026-10-13T07:08:45Z"}
    immutable_json(shared,sealed(prior))
    proof=tmp_path/"proof.html"; proof.write_text(HTML,encoding="utf-8")
    source={"source_id":"root","source_type":"BEST_SELLERS","research_category":None,"actual_source_url":URL,"page_urls":[URL]}
    plan=new_plan("recovery",stage="T1",origin="LIVE_CAPTURE",live_enabled=True,sources=[source],shared_access_gate_path=str(shared))
    now=datetime.now(timezone.utc)
    auth={**plan,"approved":True,"approval_id":"user1","approved_by":"User",
        "store_root":str(store.root),"access_gate_state":"NORMAL","reviewed_at":now.isoformat(),
        "expires_at":(now+timedelta(hours=1)).isoformat(),"stop_conditions":["CHALLENGE","BLOCKED","RATE_LIMITED","UNKNOWN","NETWORK_ERROR"],
        "sources":{"root":{"source_type":"BEST_SELLERS","research_category":None,"page_urls":[URL],
            "evidence":{"reviewed":True,"path":str(proof),"sha256":digest(proof.read_bytes())}}}}
    recovery={"approved":True,"agent_verification_authorized":True,"gate_sha256":digest(prior),
        "store_root":str(store.root),"run_id":plan["run_id"],"approved_by":"User","approval_id":"user-verify",
        "reviewed_at":now.isoformat(),"expires_at":auth["expires_at"],"max_attempts":2,"source_id":"root","page_url":URL}
    return store,plan,auth,recovery,shared

class Transport:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def fetch_page(self,url):
        self.calls.append(url);value=next(self.responses)
        if isinstance(value,Exception):raise value
        return value

def normal():return {"status_code":200,"url":URL,"text":HTML}

def test_delegate_can_verify_ordinary_network_error_then_restore_exact_gate(tmp_path):
    store,plan,auth,recovery,shared=setup(tmp_path)
    transport=Transport([normal()])
    receipt=recheck_network_error(store,plan,auth,recovery,transport)
    assert receipt["access_state"] == read_sealed(shared)["state"] == store.get_gate()["state"] == "NORMAL"
    assert store.summary(plan["run_id"])["requests"] == 1
    proof=read_sealed(read_sealed(shared)["agent_recovery_evidence"])
    assert proof["agent_handled"] is True and proof["human_handled"] is False

def test_one_budgeted_refresh_after_network_failure_preserves_both_receipts(tmp_path):
    store,plan,auth,recovery,shared=setup(tmp_path)
    transport=Transport([RuntimeError("Failed to fetch"),normal()]);waits=[]
    recheck_network_error(store,plan,auth,recovery,transport,sleep=waits.append)
    assert len(transport.calls) == 2 and 0<waits[0]<=3
    assert [r["status"] for r in store.requests(plan["run_id"])] == ["NETWORK_ERROR","NORMAL"]
    assert store.get_gate()["state"] == "NORMAL"

@pytest.mark.parametrize("status,body",[(403,"Access Denied"),(429,"Rate Limited"),(200,"Robot Check CAPTCHA")])
def test_new_access_restriction_stops_without_refresh(tmp_path,status,body):
    store,plan,auth,recovery,shared=setup(tmp_path)
    transport=Transport([{"status_code":status,"url":URL,"text":body},normal()])
    with pytest.raises(ValueError,match="RECOVERY_STOPPED"):
        recheck_network_error(store,plan,auth,recovery,transport)
    assert len(transport.calls) == 1
    assert read_sealed(shared)["state"] != "NORMAL"

def test_existing_challenge_cannot_use_network_recovery_approval(tmp_path):
    store,plan,auth,recovery,shared=setup(tmp_path,state="CHALLENGE")
    transport=Transport([normal()])
    with pytest.raises(ValueError,match="EXACT_USER"):
        recheck_network_error(store,plan,auth,recovery,transport)
    assert transport.calls == []

def test_normal_nonranking_html_cannot_clear_gate(tmp_path):
    store,plan,auth,recovery,shared=setup(tmp_path)
    transport=Transport([{"status_code":200,"url":URL,"text":"<html><h1>Unrelated normal webpage with some content</h1></html>"},normal()])
    with pytest.raises(ValueError,match="RECOVERY_STOPPED_UNKNOWN"):
        recheck_network_error(store,plan,auth,recovery,transport)
    assert len(transport.calls) == 1 and read_sealed(shared)["state"] == "UNKNOWN"

@pytest.mark.parametrize("changed", ["recovery_expiry", "main_expiry", "gate_fingerprint"])
@pytest.mark.parametrize("request_number", [1, 2])
def test_pacing_revalidates_both_approvals_and_exact_gate_before_reservation(tmp_path, monkeypatch, changed, request_number):
    import amazon_es_bestseller.highspeed.recovery as module
    from amazon_es_bestseller.highspeed.budgets import aware_time
    store,plan,auth,recovery,shared=setup(tmp_path)
    now=[datetime.now(timezone.utc)]
    prior=read_sealed(shared)
    auth["reviewed_at"]=recovery["reviewed_at"]=(now[0]-timedelta(seconds=1)).isoformat()
    if changed == "recovery_expiry":
        recovery["expires_at"]=(now[0]+timedelta(seconds=1)).isoformat()
    if changed == "main_expiry":
        auth["expires_at"]=(now[0]+timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(module,"aware_time",lambda value=None: aware_time(value) if value is not None else now[0])
    transport=Transport([normal()] if request_number == 1 else [RuntimeError("Failed to fetch"), normal()])
    monkeypatch.setattr(store,"latest_request_time",lambda:
        None if request_number == 2 and not transport.calls else now[0].isoformat())
    def wait(seconds):
        now[0]+=timedelta(seconds=seconds)
        if changed == "gate_fingerprint":
            atomic_json(shared,sealed({**prior,"new_evidence":"changed during pacing"}))
    with pytest.raises(ValueError,match="EXPIRED|GATE_CHANGED"):
        recheck_network_error(store,plan,auth,recovery,transport,sleep=wait)
    assert len(transport.calls) == request_number-1
    assert store.summary(plan["run_id"])["requests"] == request_number-1
    if request_number == 2:
        assert store.requests(plan["run_id"])[0]["status"] == "NETWORK_ERROR"
    assert read_sealed(shared)["state"] == "NETWORK_ERROR"
    if changed == "gate_fingerprint":
        assert read_sealed(shared)["new_evidence"] == "changed during pacing"
    else:
        assert read_sealed(shared) == prior
