"""Real SQLite/journal regressions: crash gaps, finite budgets and offline views."""
import json
from pathlib import Path
import pytest

from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.quality import audit_run
from amazon_es_bestseller.highspeed.reprocess import renormalize_run

URL = "https://www.amazon.es/gp/bestsellers"
TIME = "2026-10-13T08:00:00Z"

def setup(tmp_path, run_id="run", **overrides):
    store = RunStore(tmp_path / "store")
    source = {"source_id": "root", "source_type": "BEST_SELLERS", "page_urls": [URL], "actual_source_url": URL}
    plan = new_plan(run_id, sources=[source], **overrides)
    store.begin_run(plan, started_at=TIME)
    return store, plan, source

def page(store, plan, source, urls, *, key="page", title="Item"):
    cards = ''.join('<div class="ranking-card"><span class="zg-bdg-text">#%s</span>%s</div>' %
        (i+1, '<a href="%s"><span data-product-title>%s</span></a>' % (u, title) if u else '<span data-product-title>Missing</span>')
        for i,u in enumerate(urls))
    html = '<h1>Los más vendidos</h1><div id="zg" data-expected-count="%s">%s</div>' % (len(urls), cards)
    evidence = store.save_html(html)
    parsed = parse_page(html, source=source, run_id=plan["run_id"], page_url=URL,
        page_number=1, observed_at=TIME, evidence_path=evidence["html_path"], origin=plan["origin"])
    store.commit_page(plan["run_id"], key, parsed["page"], parsed["observations"])
    return parsed

def test_aliases_do_not_inflate_target_and_raw_cards_never_disappear(tmp_path):
    store,p,s = setup(tmp_path)
    page(store,p,s,["/dp/opaque?tag=a", "/dp/opaque?tag=b", "/dp/opaque?tag=b",
        "/gp/product/opaque?th=2", "/product/different", None])
    summary = store.summary("run")
    assert summary["observations"] == 6 and summary["unique_links"] == 2
    assert summary["normalized_unique_url_count"] == 3
    assert summary["exact_duplicates"] == summary["normalized_duplicates"] == summary["invalid_links"] == 1
    assert sum(c["target_eligible"] for c in store.iter_candidates("run")) == 2
    assert all("asin" not in k.lower() for row in store.iter_candidates("run") for k in row)
    assert audit_run(store,"run")["gate_passed"]

def test_request_limits_cannot_be_replaced_and_ambiguous_attempt_stays_charged(tmp_path):
    store,p,s = setup(tmp_path, max_requests_per_run=1)
    with pytest.raises(ValueError, match="BUDGET_CONFLICT"):
        store.reserve_request("run", {"kind":"PAGE", "limits":{**p,"max_requests_per_run":100}})
    reserved = store.reserve_request("run", {"kind":"PAGE", "url":URL, "observed_at":TIME})
    store.close()
    store = RunStore(tmp_path/"store"); store.begin_run(p)
    assert store.requests("run")[0]["status"] == "RESERVED"
    with pytest.raises(ValueError, match="REQUEST_BUDGET_EXHAUSTED"):
        store.reserve_request("run", {"kind":"ACP", "observed_at":TIME})
    assert not audit_run(store,"run")["gate_passed"]
    with pytest.raises(ValueError, match="OWN_RESERVED"):
        store.save_response("run", {"request_id":"missing"}, {"text":"body"})

def test_page_publication_before_index_commit_recovers_idempotently(tmp_path, monkeypatch):
    store,p,s=setup(tmp_path)
    original=store._index_page
    monkeypatch.setattr(store,"_index_page",lambda *args: (_ for _ in ()).throw(RuntimeError("crash after publish")))
    with pytest.raises(RuntimeError): page(store,p,s,["/product/a","/product/b"])
    assert store.summary("run")["observations"] == 0
    monkeypatch.setattr(store,"_index_page",original)
    store.recover("run"); store.recover("run")
    assert store.summary("run")["observations"] == 2
    assert audit_run(store,"run")["gate_passed"]

def test_changed_slot_preserves_both_versions_and_latest_page(tmp_path):
    store,p,s=setup(tmp_path)
    page(store,p,s,["/product/a"],title="First")
    page(store,p,s,["/product/b"],title="Second")
    assert store.summary("run")["observations"] == 2
    rows=list(store.iter_observations("run"))
    assert rows[1]["parent_observation_id"] == rows[0]["observation_id"]
    store.recover("run")
    assert len(list(store.iter_observations("run"))) == 2
    assert audit_run(store,"run")["gate_passed"]

def test_saved_restriction_recovered_before_response_sql_publication(tmp_path,monkeypatch):
    store,p,s=setup(tmp_path)
    reserved=store.reserve_request("run",{"kind":"PAGE","url":URL,"observed_at":TIME})
    original=store._transaction
    monkeypatch.setattr(store,"_transaction",lambda: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        store.save_response("run",reserved,{"text":"Robot Check","access_state":"CHALLENGE","origin":"LIVE_CAPTURE"})
    assert store.get_gate()["state"] == "CHALLENGE"
    monkeypatch.setattr(store,"_transaction",original)
    store.recover("run")
    with pytest.raises(ValueError,match="HUMAN_GATE_RECOVERY"):
        store.set_gate("NORMAL")

def test_raw_budget_rejects_indexing_but_preserves_entire_page_journal(tmp_path):
    store,p,s=setup(tmp_path,max_raw_observations_per_run=1)
    with pytest.raises(ValueError,match="RAW_OBSERVATION"):
        page(store,p,s,["/product/a","/product/b"])
    journal=next((tmp_path/"store"/"runs"/"run"/"pages").glob("*.json"))
    from amazon_es_bestseller.multisource.storage import read_sealed
    assert len(read_sealed(journal)["observations"]) == 2
    assert store.summary("run")["observations"] == 0

def test_corrupt_html_fails_recovery_and_audit(tmp_path):
    store,p,s=setup(tmp_path)
    parsed=page(store,p,s,["/product/a"])
    Path(parsed["page"]["evidence_path"]).write_text("corrupted",encoding="utf-8")
    assert not audit_run(store,"run")["gate_passed"]
    with pytest.raises(ValueError,match="PAGE_EVIDENCE_CORRUPT"): store.recover("run")

def test_offline_renormalization_is_new_view_preserving_raw_and_capture_times(tmp_path):
    store,p,s=setup(tmp_path)
    page(store,p,s,["/dp/token?opaque=a","/dp/token?opaque=b",None])
    before=list(store.iter_observations("run"))
    first=renormalize_run(store,"run",tmp_path/"views")
    second=renormalize_run(store,"run",tmp_path/"views")
    assert first["directory"] != second["directory"]
    assert first["is_derivation"] and first["unique_valid_links"] == 1 and first["raw_observations"] == 3
    assert first["live_requests"] == 0
    derived=[json.loads(line) for line in Path(first["artifacts"]["observations"]["path"]).read_text(encoding="utf-8").splitlines()]
    assert [r["observed_at"] for r in derived] == [r["observed_at"] for r in before]
    assert list(store.iter_observations("run")) == before

def test_stable_candidate_identity_across_daily_runs(tmp_path):
    store,p,s=setup(tmp_path)
    page(store,p,s,["/product/a"])
    first=list(store.iter_candidates("run"))[0]["candidate_id"]
    tomorrow=new_plan("tomorrow",sources=[s]); store.begin_run(tomorrow)
    page(store,tomorrow,s,["/product/a"])
    assert list(store.iter_candidates("tomorrow"))[0]["candidate_id"] == first
    assert store.candidate_count("run") == store.candidate_count("tomorrow") == 1
    assert store.summary("tomorrow")["history_observations"] == 2

def test_plan_start_is_immutable_and_marker_protects_other_directories(tmp_path):
    store,p,s=setup(tmp_path)
    assert store.begin_run(p,started_at="2026-10-14T00:00:00Z")["started_at"].startswith("2026-10-13")
    with pytest.raises(ValueError,match="PLAN_CONFLICT"): store.begin_run({**p,"max_requests_per_run":1})
    other=tmp_path/"production"; other.mkdir(); (other/"data.json").write_text("{}")
    with pytest.raises(ValueError,match="ISOLATED_EMPTY"): RunStore(other)

def test_live_quality_requires_actual_reserved_response_not_fixture_success_label(tmp_path):
    store,p,s=setup(tmp_path,origin="LIVE_CAPTURE",stage="T1")
    page(store,p,s,["/product/a"])
    audit=audit_run(store,"run")
    assert not audit["gate_passed"]
    assert any("EXACT_NORMAL_REQUEST_PROOF" in f.get("reason","") for f in audit["findings"])

@pytest.mark.parametrize("independent",[False,True])
def test_human_reviews_project_without_overwriting_original_journals(tmp_path,independent):
    from test_highspeed_reviews import example
    options={"decision":"VERIFIED_INDEPENDENT_PURCHASABLE",
        "finals":["https://www.amazon.es/dp/token?choice=blue","https://www.amazon.es/dp/token?choice=red"]} if independent else {}
    review,candidates,_=example(tmp_path,**options)
    store,p,s=setup(tmp_path,origin="HISTORICAL_REPLAY")
    urls=[c["normalized_product_url"] for c in candidates]
    page(store,p,s,urls+["https://www.amazon.es/dp/opaque-token?unknown=1"])
    journals={path:path.read_bytes() for path in (tmp_path/"store"/"runs"/"run"/"pages").glob("*.json")}
    assert store.candidate_count("run") == 1
    store.apply_review("run",review)
    expected=2 if independent else 1
    assert store.candidate_count("run") == expected
    views=list(store.iter_candidates("run"))
    assert sum(c["target_eligible"] for c in views) == expected
    assert sum(c["strict_buyable_confirmed"] for c in views) == (2 if independent else 0)
    assert all(path.read_bytes() == data for path,data in journals.items())
    assert audit_run(store,"run")["gate_passed"]
    with pytest.raises(ValueError,match="ALREADY_EXISTS"): store.apply_review("run",review)
    store.close(); store=RunStore(tmp_path/"store"); store.begin_run(p)
    assert store.candidate_count("run") == expected

def test_explicit_human_gate_recovery_does_not_hide_new_restriction(tmp_path):
    from datetime import datetime,timedelta,timezone
    from amazon_es_bestseller.multisource.storage import digest
    store,p,s=setup(tmp_path)
    req=store.reserve_request("run",{"kind":"PAGE","url":URL,"observed_at":TIME})
    store.save_response("run",req,{"text":"Robot Check","access_state":"CHALLENGE","origin":"LIVE_CAPTURE","observed_at":TIME})
    now=datetime.now(timezone.utc)
    approval={"approved":True,"human_handled":True,"approved_by":"human","approval_id":"handling1",
        "store_root":str(store.root),"gate_sha256":digest(store.get_gate()),
        "reviewed_at":now.isoformat(),"expires_at":(now+timedelta(hours=1)).isoformat()}
    store.recover_gate(approval)
    assert store.get_gate()["state"] == "NORMAL"
    req=store.reserve_request("run",{"kind":"PAGE","url":URL,"observed_at":now.isoformat()})
    store.save_response("run",req,{"text":"Denied","access_state":"BLOCKED","origin":"LIVE_CAPTURE","observed_at":now.isoformat()})
    assert store.get_gate()["state"] == "BLOCKED"
    with pytest.raises(ValueError,match="EXACT_VALID"): store.recover_gate(approval)
