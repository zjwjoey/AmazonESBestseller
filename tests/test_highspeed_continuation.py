"""Synthetic offline captures exercise derivation; no browser or network."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import pytest

from amazon_es_bestseller.highspeed.continuation import MODE,seed_successor,validate_seed_provenance
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.quality import audit_run
from amazon_es_bestseller.highspeed.runner import HighSpeedRunner
from amazon_es_bestseller.multisource.storage import digest,read_sealed,immutable_json,sealed

URL="https://www.amazon.es/gp/bestsellers"
TIME="2026-10-13T01:00:00Z"


def fixture(tmp_path,*,bad_source=False,mixed=False):
    store=RunStore(tmp_path/"s")
    source={"source_id":"root","source_type":"BEST_SELLERS","research_category":None,
        "actual_source_url":URL,"page_urls":[URL]}
    parent=new_plan("parent",stage="T1",origin="LIVE_CAPTURE",live_enabled=True,sources=[source])
    store.begin_run(parent,started_at=TIME)
    key=hashlib.sha256(("root\0"+URL).encode()).hexdigest()
    for i,urls in enumerate([["/dp/opaque/session-a"],["/dp/opaque/session-a","/gp/product/opaque/session-b","/product/two"]]):
        unconfirmed=bad_source or (mixed and i==0)
        if mixed and i==0: urls=["/product/unconfirmed-old"]
        observed="2026-10-13T01:00:0%dZ"%i
        html=("<h1>Unrelated normal page</h1>" if unconfirmed else "<h1>Los más vendidos</h1>")+"<div id='zg' data-expected-count='%d'>"%len(urls)
        html+=''.join("<div class='ranking-card'><span class='zg-bdg-text'>#%d</span><a href='%s'>Item</a></div>"%(n+1,u) for n,u in enumerate(urls))+"</div>"
        reservation=store.reserve_request("parent",{"kind":"PAGE","url":URL,"source_id":"root",
            "source_type":"BEST_SELLERS","origin":"LIVE_CAPTURE","observed_at":observed})
        receipt=store.save_response("parent",reservation,{"text":html,"status_code":200,"access_state":"NORMAL",
            "origin":"LIVE_CAPTURE","source_id":"root","source_type":"BEST_SELLERS","observed_at":observed,"final_url":URL})
        parsed=parse_page(html,source=source,run_id="parent",page_url=URL,page_number=1,observed_at=observed,
            evidence_path=receipt["html_path"],http_status=200,origin="LIVE_CAPTURE")
        if unconfirmed:
            # Model old collectors retaining visible cards before source QA.
            visible=parse_page(html.replace("Unrelated normal page","Los más vendidos"),source=source,
                run_id="parent",page_url=URL,page_number=1,observed_at=observed,
                evidence_path=receipt["html_path"],http_status=200,origin="LIVE_CAPTURE")
            parsed["observations"]=visible["observations"]
            for row in parsed["observations"]:row["evidence_sha256"]=receipt["sha256"]
        store.commit_page("parent",key,{**parsed["page"],"source":source},parsed["observations"])
    store.update_run("parent",quality_gate_passed=False)
    child=new_plan("child",stage="T1",origin="LIVE_CAPTURE",live_enabled=True,sources=[deepcopy(source)],
        parent_run_id="parent",continuation_mode=MODE)
    return store,parent,child


def test_seed_preserves_all_indexed_revisions_without_new_receipts_or_budget(tmp_path):
    store,parent,child=fixture(tmp_path)
    child["task_started_at"]="2026-10-13T02:00:00Z"
    before=list(map(tuple,store.db.execute("SELECT * FROM observations WHERE run_id='parent'")))
    summary=seed_successor(store,"parent",child)
    assert store.get_run("child")["started_at"]=="2026-10-13T02:00:00+00:00"
    assert store.get_run("parent")["started_at"]=="2026-10-13T01:00:00+00:00"
    assert summary["requests"]==0 and summary["observations"]==4
    assert store.candidate_count("child")==store.candidate_count("parent")==2
    assert list(store.iter_responses("child"))==[]
    assert list(map(tuple,store.db.execute("SELECT * FROM observations WHERE run_id='parent'")))==before
    rows=list(store.iter_observations("child"))
    assert all(r["origin"]=="HISTORICAL_REPLAY" and r["is_derivation"] for r in rows)
    assert all(r["parent_run_id"]=="parent" and len(r["parent_observation_ids"])==1 for r in rows)
    assert {r["observed_at"] for r in rows}=={"2026-10-13T01:00:00Z","2026-10-13T01:00:01Z"}
    page=store.pages("child")[0]
    proof=validate_seed_provenance(page,child,store=store)
    assert len(proof["parent_rows"])==4 and proof["provenance"]["parent_quality_gate_passed"] is False
    # Five-list QA remains failed; historical cards are accepted research candidates.
    qa=audit_run(store,"child")
    assert qa["unique_valid_links"]==2 and qa["quality_gate_passed"] is False
    assert not any(f["code"]=="PAGE_EVIDENCE_AUDIT_FAILED" for f in qa["findings"])
    seed_successor(store,"parent",child)
    assert store.summary("child")["observations"]==4 and store.summary("child")["requests"]==0


@pytest.mark.parametrize("damage",["html","receipt","journal","scope","stage"])
def test_seed_refuses_tampered_or_unapproved_evidence_before_import(tmp_path,damage):
    store,parent,child=fixture(tmp_path)
    if damage=="html":Path(store.pages("parent")[0]["evidence_path"]).write_text("changed",encoding="utf8")
    if damage=="receipt":Path(next(store.iter_responses("parent"))["evidence_path"]).write_text("{}",encoding="utf8")
    if damage=="journal":Path(store.pages("parent")[0]["journal_path"]).write_text("{}",encoding="utf8")
    if damage=="scope":child["sources"][0]["research_category"]="home"
    if damage=="stage":child["stage"]="T3"
    with pytest.raises((ValueError,KeyError)):seed_successor(store,"parent",child)
    assert store.db.execute("SELECT count(*) FROM observations WHERE run_id='child'").fetchone()[0]==0


def test_imported_evidence_cannot_be_relabeled_as_formal_t3_or_changed_rows(tmp_path):
    store,parent,child=fixture(tmp_path);seed_successor(store,"parent",child)
    page=store.pages("child")[0]
    with pytest.raises(ValueError,match="FORMAL_LIVE"):
        validate_seed_provenance(page,{**child,"stage":"T3"},store=store)
    proof_path=Path(page["seed_provenance_path"])
    proof_path.write_text("{}",encoding="utf8")
    assert any(f["code"]=="PAGE_EVIDENCE_AUDIT_FAILED" for f in audit_run(store,"child")["findings"])


def test_unconfirmed_source_rows_are_preserved_but_not_counted_or_skipped(tmp_path):
    store,parent,child=fixture(tmp_path,bad_source=True)
    seed_successor(store,"parent",child)
    assert store.summary("child")["observations"]==4 and store.candidate_count("child")==0
    assert all(r["source_evidence_eligible"] is False for r in store.iter_observations("child"))
    qa=audit_run(store,"child")
    assert qa["quality_gate_passed"] is False and qa["unique_valid_links"]==0
    assert not any(f["code"]=="PAGE_EVIDENCE_AUDIT_FAILED" for f in qa["findings"])


def test_old_unconfirmed_row_is_not_promoted_by_new_verified_page_revision(tmp_path):
    store,parent,child=fixture(tmp_path,mixed=True)
    assert store.candidate_count("parent")==2
    old=list(store.iter_observations("parent"))
    assert sum(r["source_evidence_eligible"] is False for r in old)==1
    seed_successor(store,"parent",child)
    imported=list(store.iter_observations("child"))
    assert store.pages("child")[0]["source_type_verified"] is True
    assert len(imported)==4 and store.candidate_count("child")==2
    assert sum(r["source_evidence_eligible"] is False for r in imported)==1
    unconfirmed=next(r for r in imported if r["raw_product_url"]=="/product/unconfirmed-old")
    assert unconfirmed["source_evidence_eligible"] is False and unconfirmed["target_eligible"] is False
    page=store.pages("child")[0];journal=read_sealed(page["journal_path"])
    changed=deepcopy(journal["observations"])
    next(r for r in changed if r["raw_product_url"]=="/product/unconfirmed-old")["source_evidence_eligible"]=True
    store.commit_page("child",page["page_key"],{k:v for k,v in page.items() if k!="journal_path"},changed)
    assert any(f["code"]=="PAGE_EVIDENCE_AUDIT_FAILED" for f in audit_run(store,"child")["findings"])


def test_partial_terminal_source_keeps_incompleteness_and_no_new_requests(tmp_path):
    store,parent,child=fixture(tmp_path)
    page=store.pages("parent")[0]; journal=read_sealed(page["journal_path"])
    previous={**journal["page"],"status":"PARTIAL","page_complete":False,"completeness":"UNKNOWN",
        "terminal_for_snapshot":True,"continuation_unavailable":True}
    store.commit_page("parent",page["page_key"],previous,journal["observations"])
    seed_successor(store,"parent",child)
    imported=store.pages("child")[0]
    assert imported["status"]=="PARTIAL" and imported["terminal_for_snapshot"] is True
    assert any(f["code"]=="PAGE_COMPLETENESS_NOT_PROVEN" for f in audit_run(store,"child")["findings"])


def test_missing_parent_receipt_fails_successor_qa_without_live_relabel(tmp_path):
    store,parent,child=fixture(tmp_path);seed_successor(store,"parent",child)
    Path(next(store.iter_responses("parent"))["evidence_path"]).unlink()
    qa=audit_run(store,"child")
    assert qa["quality_gate_passed"] is False
    assert any(f["code"]=="PAGE_EVIDENCE_AUDIT_FAILED" for f in qa["findings"])
    assert store.summary("child")["requests"]==0


@pytest.mark.parametrize("damage",[None,"title","url","rank","source"])
def test_frozen_legacy_price_title_remains_bound_without_rewriting_raw(tmp_path,damage):
    store,parent,child=fixture(tmp_path)
    source=parent['sources'][0]
    html="<h1>Los más vendidos</h1><div data-expected-count='1'><div class='ranking-card'><span class='zg-bdg-text'>#1</span><a class='a-link-normal' href='/product/legacy-title'><span class='a-size-base p13n-sc-price'>9,99 €</span><div class='p13n-sc-css-line-clamp-3'>Real preserved product name</div></a></div></div>"
    observed='2026-10-13T03:00:00Z'
    request=store.reserve_request('parent',{'kind':'PAGE','url':URL,'source_id':'root','source_type':'BEST_SELLERS','observed_at':observed})
    receipt=store.save_response('parent',request,{'text':html,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE',
        'source_id':'root','source_type':'BEST_SELLERS','observed_at':observed,'final_url':URL})
    parsed=parse_page(html,source=source,run_id='parent',page_url=URL,page_number=1,observed_at=observed,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
    parsed['observations'][0].update(product_title_raw='9,99 €',parser_version='link_first_adapters_v2.1')
    parsed['observations'][0].pop('product_title_evidence',None)
    if damage:
        field,value={'title':('product_title_raw','Invented important attribute'),'url':('raw_product_url','/product/not-in-html'),
            'rank':('ranking_position',2),'source':('source_type','MOST_GIFTED')}[damage]
        parsed['observations'][0][field]=value
    key=store.pages('parent')[0]['page_key'];store.commit_page('parent',key,parsed['page'],parsed['observations'])
    before=list(map(tuple,store.db.execute("SELECT * FROM observations WHERE run_id='parent'")))
    if damage:
        with pytest.raises(ValueError,match='CONTINUATION_'):seed_successor(store,'parent',child)
        assert store.db.execute("SELECT count(*) FROM observations WHERE run_id='child'").fetchone()[0]==0
        return
    seed_successor(store,'parent',child)
    assert any(r['product_title_raw']=='9,99 €' for r in store.iter_observations('child'))
    assert list(map(tuple,store.db.execute("SELECT * FROM observations WHERE run_id='parent'")))==before
    assert not any(f['code']=='PAGE_EVIDENCE_AUDIT_FAILED' for f in audit_run(store,'child')['findings'])


@pytest.mark.parametrize("unconfirmed",[False,True])
def test_resume_skips_seed_complete_page_and_fetches_only_new_frozen_source(tmp_path,unconfirmed):
    store,parent,child=fixture(tmp_path,bad_source=unconfirmed)
    next_url="https://www.amazon.es/gp/bestsellers/home"
    child["sources"].append({"source_id":"home","source_type":"BEST_SELLERS","research_category":"home",
        "actual_source_url":next_url,"page_urls":[next_url]})
    seed_successor(store,"parent",child)
    now=datetime.now(timezone.utc); proof=tmp_path/"source.html";proof.write_text("reviewed fixture navigation")
    authorization={**child,"approved":True,"approved_by":"offline fixture","approval_id":"fixture-only",
        "store_root":str(store.root),"access_gate_state":"NORMAL","reviewed_at":(now-timedelta(minutes=1)).isoformat(),
        "expires_at":(now+timedelta(hours=1)).isoformat(),"stop_conditions":["CHALLENGE","BLOCKED","RATE_LIMITED","UNKNOWN","NETWORK_ERROR"],
        "sources":{s["source_id"]:{"source_type":s["source_type"],"research_category":s.get("research_category"),
            "page_urls":s["page_urls"],"evidence":{"reviewed":True,"path":str(proof),"sha256":digest(proof.read_bytes())}} for s in child["sources"]}}
    calls=[]
    class Fake:
        def fetch_page(self,url):
            calls.append(url)
            return {"status_code":200,"url":url,"text":"<h1>Los más vendidos</h1><div data-expected-count='1'><div class='ranking-card'><span class='zg-bdg-text'>#1</span><a href='/product/new'>New</a></div></div>"}
    result=HighSpeedRunner(store,gate_reader=lambda:{"state":"NORMAL"}).run(child,authorization=authorization,transport=Fake(),resume=True)
    assert calls==([] if unconfirmed else [next_url])
    assert store.summary("child")["requests"]==(0 if unconfirmed else 1)
    assert store.candidate_count("child")== (0 if unconfirmed else 3)
    if unconfirmed:
        assert result["run"]["stop_reason"]=="PAGE_PARSE_OR_ACCESS_FAILED"
        assert store.pages("child")[0]["origin"]=="HISTORICAL_REPLAY"
        assert store.summary("child")["observations"]==4
