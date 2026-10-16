"""Offline mock transport; no claim that these are live HTTP receipts."""
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.collection import detail_recovery5500 as mod
from amazon_es_bestseller.collection.quick_selection import file_sha
from amazon_es_bestseller.multisource.storage import digest, immutable_json, read_sealed, sealed


def fixture():
    f = Path(__file__).parent / 'fixtures/detail_bridge5500'
    raw = gzip.decompress((f/'actual_child_parent_canonical.html.gz').read_bytes())
    p = json.loads((f/'actual_child_parent_canonical.provenance.json').read_text())
    assert hashlib.sha256(raw).hexdigest() == p['sha256']
    return raw.decode('utf8'), p['task']


def sandbox(tmp_path, monkeypatch, status=200):
    h, t = fixture()
    t = {**t, 'run_id': 'offline-test'}
    original = tmp_path/'receipts/original.json'
    immutable_json(original, sealed({'state':'CHALLENGE','kind':'PAGE','started_at':'2020-01-01T00:00:00+00:00'}))
    cp = tmp_path/'tasks'/t['task_id']/'checkpoint.json'
    immutable_json(cp, sealed({'status':'ACCESS_STOP','receipt_path':str(original)}))
    gate = tmp_path/'access_gate.json'; shared = tmp_path/'shared.json'
    for p in (gate,shared):
        immutable_json(p,sealed({'state':'CHALLENGE','receipt_path':str(original)}))
    plan = {'tasks':[t], 'run_id':t['run_id'], 'shared_gate_path':str(shared)}
    ap=tmp_path/'recovery.json'
    approval={'approved':True,'run_id':t['run_id'],'task_id':t['task_id'],'request_url':t['request_url'],
        'plan_sha256':digest(plan),'code_sha':'offline-test',
        'max_attempts':1,'authority':'EXPLICIT_USER_MANUAL_HANDLING_AND_CONTINUATION',
        'user_quotes':['offline test authorization, not live approval'],'runtime_root':str(tmp_path.resolve()),
        'gate_sha256':{str(p):file_sha(p) for p in (gate,shared)}}
    immutable_json(ap,sealed(approval))
    ref={'path':str(ap),'sha256':file_sha(ap)}
    auth={'starts_at':'2020-01-01T00:00:00+00:00','request_deadline':'2099-01-01T00:00:00+00:00',
        'max_requests':6050,'max_page_requests':6050,'request_start_interval_seconds':3,'code_sha':'offline-test'}
    monkeypatch.setattr(mod,'uncertain_setup_page_holds',lambda *a:2)
    calls=[]
    class Page:
        url=t['request_url']
        main_frame=object()
        def route(self,pattern,callback): self.boundary=callback
        def on(self,*a): pass  # Deliberately suppress all response events.
        def goto(self,*a,**kw):
            # The reservation must already exist BEFORE transport.
            rows=[read_sealed(p) for p in (tmp_path/'receipts').glob('*recovery*.json')]
            assert len(rows)==1 and rows[0]['state']=='RESERVED'
            calls.append(a[0])
            return SimpleNamespace(status=status) if status is not None else None
        def content(self): return h
    session=SimpleNamespace(page=Page(),wait_for_product_page=lambda:None,wait_for_price_text=lambda:None)
    bridge=SimpleNamespace(root=tmp_path,plan=plan,progress=lambda:None,validate_authorization=lambda a:None)
    return session,bridge,auth,t,ref,calls,h


@pytest.mark.parametrize('status,expected',[(200,'NORMAL'),(None,'UNKNOWN'),(403,'BLOCKED'),(429,'RATE_LIMITED')])
def test_direct_transport_status_is_preserved_when_events_are_absent(tmp_path,monkeypatch,status,expected):
    s,b,a,t,ref,calls,h=sandbox(tmp_path,monkeypatch,status)
    before={str(p):p.read_bytes() for p in [b.root/'access_gate.json',b.root/'shared.json',b.root/'tasks'/t['task_id']/'checkpoint.json']}
    rp,r,html=mod.capture_once(s,b,a,t,ref)
    assert calls==[t['request_url']] and r['status_code']==status and r['state']==expected
    assert all(Path(p).read_bytes()==v for p,v in before.items())  # No Gate/checkpoint changes.
    assert r['human_handled'] is False
    # Even if initial route callbacks were absent, no later reload is allowed.
    decision=[]
    request=SimpleNamespace(method='GET',url=t['request_url'],frame=s.page.main_frame,
        redirected_from=None,is_navigation_request=lambda:True)
    s.page.boundary(SimpleNamespace(request=request,abort=lambda:decision.append('abort'),
        continue_=lambda:decision.append('continue')))
    assert decision==['abort']
    if status==200:
        result,meta=mod.validate_capture(t,r,html)
        assert result['status']=='SUCCESS' and meta['status_code']==200
    else:
        with pytest.raises(ValueError):mod.validate_capture(t,r,html)
    with pytest.raises(ValueError,match='ALREADY_CONSUMED'):mod.capture_once(s,b,a,t,ref)
    assert len(calls)==1


@pytest.mark.parametrize('bad',['gate','approved_hash','budget','deadline','success_checkpoint','scope'])
def test_fail_closed_before_transport(tmp_path,monkeypatch,bad):
    s,b,a,t,ref,calls,h=sandbox(tmp_path,monkeypatch)
    if bad=='gate':(b.root/'access_gate.json').write_text('{}')
    elif bad=='approved_hash':ref['sha256']='bad'
    elif bad=='budget':a['max_requests']=3
    elif bad=='deadline':a['request_deadline']='2021-01-01T00:00:00+00:00'
    elif bad=='success_checkpoint':
        from amazon_es_bestseller.multisource.storage import atomic_json
        atomic_json(b.root/'tasks'/t['task_id']/'checkpoint.json',sealed({'status':'SUCCESS','receipt_path':str(b.root/'receipts/original.json')}))
    elif bad=='scope':t={**t,'request_url':'https://www.amazon.es/dp/B000000001'}
    with pytest.raises((ValueError,KeyError)):mod.capture_once(s,b,a,t,ref)
    assert not calls and len(list((b.root/'receipts').glob('*.json')))==1


@pytest.mark.parametrize('bad',['challenge','missing_http','wrong_final','wrong_task','wrong_run','html_changed','hidden403'])
def test_normal_looking_page_cannot_wash_recovery_errors(tmp_path,monkeypatch,bad):
    s,b,a,t,ref,calls,h=sandbox(tmp_path,monkeypatch)
    rp,r,h=mod.capture_once(s,b,a,t,ref)
    if bad=='challenge':h+='<form action="/errors_page/validateCaptcha"></form>';r['html_sha256']=hashlib.sha256(h.encode()).hexdigest()
    elif bad=='missing_http':r['status_code']=None
    elif bad=='wrong_final':r['final_url']='https://www.amazon.es/'
    elif bad=='wrong_task':r['task_id']='different'
    elif bad=='wrong_run':r['run_id']='different'
    elif bad=='html_changed':h+='changed'
    elif bad=='hidden403':r['document_responses']=[{'status_code':403}]
    with pytest.raises(ValueError):mod.validate_capture(t,r,h)


@pytest.mark.parametrize('bad',[None,'success_changed','gate_changed','unknown_http','wrong_approval','already_success'])
def test_acceptance_preserves_prior_success_failure_and_gates(tmp_path,monkeypatch,bad):
    from amazon_es_bestseller.multisource.storage import atomic_json
    s,b,a,t,ref,calls,h=sandbox(tmp_path,monkeypatch)
    rp,r,h=mod.capture_once(s,b,a,t,ref)
    oldcp=b.root/'tasks'/t['task_id']/'checkpoint.json'; oldbytes=oldcp.read_bytes()
    prior=b.root/'tasks/prior/checkpoint.json'; prior_html=b.root/'prior.html';prior_html.write_text('prior')
    immutable_json(prior,sealed({'status':'SUCCESS','html_path':str(prior_html)}))
    baseline={'prior':{'checkpoint_sha256':file_sha(prior),'html_sha256':file_sha(prior_html)}}
    oldgates={p:p.read_bytes() for p in [b.root/'access_gate.json',b.root/'shared.json']}
    saves=[]
    b.save_result=lambda task,result:saves.append((task,result))
    if bad=='success_changed':prior_html.write_text('changed')
    elif bad=='gate_changed':atomic_json(b.root/'shared.json',sealed({'state':'BLOCKED'}))
    elif bad=='unknown_http':r['status_code']=None;atomic_json(rp,sealed(r))
    elif bad=='wrong_approval':ref={**ref,'sha256':'bad'}
    elif bad=='already_success':atomic_json(oldcp,sealed({'status':'SUCCESS','receipt_path':str(rp)}))
    if bad:
        with pytest.raises((ValueError,KeyError)):mod.accept_capture(b,t,rp,ref,baseline)
        assert saves==[] and not (b.root/'access_recovery_1.accepted.json').exists()
    else:
        proof=mod.accept_capture(b,t,rp,ref,baseline)
        assert len(saves)==1 and saves[0][1]['status']=='SUCCESS'
        assert oldcp.with_name('checkpoint.challenge.original.json').read_bytes()==oldbytes
        assert proof['human_handled'] is False and proof['prior_success_count']==1
        for p,v in oldgates.items():
            current=read_sealed(p)
            assert current['state']=='NORMAL' and current['previous_gate']==json.loads(v)['payload']
        assert prior_html.read_text()=='prior'
