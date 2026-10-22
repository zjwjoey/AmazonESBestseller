"""Bounded offline scheduler scenarios; no Amazon capture is simulated as LIVE proof."""
from datetime import timedelta
import hashlib
from pathlib import Path
import pytest
from test_highspeed_v3_probe_scope import setup
from amazon_es_bestseller.multisource.storage import immutable_json,sealed,digest


def reference(path):
    return {'path':str(Path(path).resolve()),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def reverify_setup(tmp_path):
    plan,auth,store,transport,runner=setup(tmp_path)
    plan.update(max_requests_per_run=50,max_requests_per_day=50,max_pages_per_run=20)
    auth.update(max_requests_per_run=50,max_requests_per_day=50,max_pages_per_run=20)
    original_summary=store.summary
    store.summary=lambda run_id:{**original_summary(run_id),'page_requests':sum(r['kind']=='PAGE' for r in store.requests)}
    original_save=store.save_response
    def save(run_id,reservation,response):
        receipt=original_save(run_id,reservation,response)
        # Match the immutable production body writer rather than Windows text newline conversion.
        Path(receipt['html_path']).write_bytes(response['text'].encode('utf-8'))
        receipt.update(observed_at=reservation['observed_at'],run_id=run_id,origin='LIVE_CAPTURE',
            status_code=200,access_state='NORMAL',sha256=hashlib.sha256(Path(receipt['html_path']).read_bytes()).hexdigest())
        return receipt
    store.save_response=save
    original_commit=store.commit_page
    def commit(run_id,key,page,rows):
        source=page['source'];url=source['page_urls'][0]
        receipt=next(r for r in reversed(store.responses) if r['source_id']==source['source_id'] and r['kind']=='PAGE')
        path=store.root/'runs'/run_id/'pages'/('page_'+str(len(store.saved_pages))+'.json')
        page={**page,'source_id':source['source_id'],'source_type':source['source_type'],'page_url':url,
            'observed_at':receipt['observed_at'],'journal_path':str(path)}
        immutable_json(path,sealed({'run_id':run_id,'page_key':key,'page':page,'observations':rows}))
        original_commit(run_id,key,page,rows)
    store.commit_page=commit
    runner.run(plan,authorization=auth,transport=transport)
    store.pages=lambda run_id:list(store.saved_pages)
    store.iter_responses=lambda run_id:iter(store.responses)
    plan_path=store.root/'runs'/plan['run_id']/'plan.json';immutable_json(plan_path,plan)
    original_start=store.run['started_at']
    clock=[runner.clock()+timedelta(minutes=1)]
    runner.clock=lambda:clock[0]
    runner.sleep=lambda seconds:clock.__setitem__(0,clock[0]+timedelta(seconds=seconds))
    review={'kind':'T1_FRESH_REVERIFICATION_APPROVAL','approved':True,'approved_by':'user_in_current_conversation',
        'approval_id':'review_fixture','run_id':plan['run_id'],'store_root':str(store.root.resolve()),
        'reviewed_at':clock[0].isoformat(),'expires_at':auth['expires_at'],'original_started_at':original_start,
        'plan_reference':reference(plan_path),'plan_sha256':digest(plan),
        'source_scopes':[{'source_id':s['source_id'],'source_type':s['source_type'],'page_urls':s['page_urls']} for s in plan['sources']],
        'max_requests_per_run':plan['max_requests_per_run'],'max_pages_per_run':plan['max_pages_per_run'],
        'preserve_evidence':True,'preserve_cumulative_budget':True,'fresh_PAGE_once_per_source':True,
        'no_source_expansion':True,'reserve_PAGE_budget_for_all_reviewed_scopes':True,
        'prior_page_journal_references':[reference(p['journal_path']) for p in store.saved_pages],
        'retained_failed_source_rules':[]}
    return plan,auth,store,transport,runner,review


def approve(tmp_path,auth,review):
    path=tmp_path/'fresh_review.json';immutable_json(path,sealed(review))
    return {**auth,'t1_reverification_review':reference(path)}


def test_explicit_reverification_fetches_exact_nine_once_without_reset(tmp_path):
    p,a,s,t,r,review=reverify_setup(tmp_path)
    old_journals=[reference(page['journal_path']) for page in s.saved_pages]
    original_start=s.run['started_at'];old_raw=len(s.observations)
    a=approve(tmp_path,a,review)
    r.run(p,authorization=a,transport=t,resume=True)
    assert len(t.calls)==18 and len(s.requests)==18
    assert len(s.observations)>old_raw and s.run['started_at']==original_start
    assert all(reference(j['path'])==j for j in old_journals)
    r.run(p,authorization=a,transport=t,resume=True)
    assert len(t.calls)==18 and len(s.requests)==18


def test_reverification_requires_current_matching_review_before_request(tmp_path):
    p,a,s,t,r,review=reverify_setup(tmp_path);review['plan_sha256']='0'*64
    with pytest.raises(ValueError,match='REVERIFICATION'):
        r.run(p,authorization=approve(tmp_path,a,review),transport=t,resume=True)
    assert len(t.calls)==9


def test_no_review_preserves_standard_cached_resume(tmp_path):
    p,a,s,t,r,review=reverify_setup(tmp_path)
    r.run(p,authorization=a,transport=t,resume=True)
    assert len(t.calls)==9


@pytest.mark.parametrize('field,value',[('approved',False),('approved_by','agent'),('run_id','other'),
    ('store_root','other'),('original_started_at','2026-10-01T00:00:00Z'),('preserve_evidence',False),
    ('preserve_cumulative_budget',False),('fresh_PAGE_once_per_source',False),('no_source_expansion',False),
    ('reserve_PAGE_budget_for_all_reviewed_scopes',False),('max_requests_per_run',99999)])
def test_invalid_review_cannot_create_fresh_requests(tmp_path,field,value):
    p,a,s,t,r,review=reverify_setup(tmp_path);review[field]=value
    with pytest.raises(ValueError):r.run(p,authorization=approve(tmp_path,a,review),transport=t,resume=True)
    assert len(t.calls)==9


def test_reserved_fresh_page_without_receipt_is_not_retried_or_replaced_by_old_cache(tmp_path):
    p,a,s,t,r,review=reverify_setup(tmp_path)
    source=p['sources'][0]
    s.requests.append({'kind':'PAGE','url':source['page_urls'][0],'source_id':source['source_id'],
        'observed_at':review['reviewed_at'],'status':'RESERVED'})
    result=r.run(p,authorization=approve(tmp_path,a,review),transport=t,resume=True)
    assert len(t.calls)==9
    assert result['run']['stop_reason']=='AMBIGUOUS_T1_REVERIFICATION_PAGE_ATTEMPT_NO_REPEAT'


def test_normal_fresh_receipt_after_crash_is_reparsed_without_duplicate_page(tmp_path):
    p,a,s,t,r,review=reverify_setup(tmp_path);a=approve(tmp_path,a,review)
    old_parser=r.parser
    r.parser=lambda *args,**kw:(_ for _ in ()).throw(RuntimeError('crash after immutable receipt'))
    with pytest.raises(RuntimeError):r.run(p,authorization=a,transport=t,resume=True)
    assert len(t.calls)==10
    r.parser=old_parser
    r.run(p,authorization=a,transport=t,resume=True)
    assert len(t.calls)==18


def test_original_page_budget_still_stops_reverification(tmp_path):
    p,a,s,t,r,review=reverify_setup(tmp_path)
    # A pre-review charged diagnostic ledger consumes the original PAGE budget.
    for i in range(p['max_pages_per_run']-9):
        s.requests.append({'kind':'PAGE','url':p['sources'][0]['page_urls'][0],'source_id':'0',
            'observed_at':s.run['started_at'],'status':'RESERVED'})
    result=r.run(p,authorization=approve(tmp_path,a,review),transport=t,resume=True)
    assert len(t.calls)==9 and result['run']['stop_reason']=='PAGE_BUDGET_EXHAUSTED'


def test_reverification_respects_shared_gate_and_review_expiry_during_pacing(tmp_path):
    p,a,s,t,r,review=reverify_setup(tmp_path)
    review['expires_at']=(r.clock()+timedelta(seconds=1)).isoformat()
    result=r.run(p,authorization=approve(tmp_path,a,review),transport=t,resume=True)
    assert len(t.calls)==10 and result['run']['stop_reason']=='T1_REVERIFICATION_REVIEW_EXPIRED'


def test_acp_cannot_consume_request_reserved_for_remaining_fresh_pages(tmp_path,monkeypatch):
    import amazon_es_bestseller.highspeed.runner as module
    p,a,s,t,r,review=reverify_setup(tmp_path)
    # Leave exactly ten total requests: nine fresh PAGE plus one ACP.
    for i in range(p['max_requests_per_run']-len(s.requests)-10):
        s.requests.append({'kind':'ACP','url':'https://www.amazon.es/acp/mock','source_id':'0',
            'observed_at':s.run['started_at'],'status':'RESERVED'})
    old_parser=r.parser
    def parser(html,**kw):
        result=old_parser(html,**kw)
        if kw['source']['source_id']=='0':
            count=len(kw.get('acp_responses',[]))
            result['page'].update(status='PARTIAL',completeness='UNKNOWN',page_complete=False,actual_count=501+count)
            result['acp_pending']={'url':'https://www.amazon.es/acp/mock','method':'POST','payload':'literal_mock_payload'}
        return result
    r.parser=parser;a['sources']['0']['allow_native_acp']=True
    original_authorize=module.authorize_request
    monkeypatch.setattr(module,'authorize_request',lambda url,source,auth,**kw:None if kw.get('kind')=='ACP' else original_authorize(url,source,auth,**kw))
    calls=[]
    def ajax(url,**kw):calls.append(url);return {'url':url,'status_code':200,'text':'normal'}
    t.fetch_ajax=ajax
    result=r.run(p,authorization=approve(tmp_path,a,review),transport=t,resume=True)
    assert len(calls)==1 and len(t.calls)==18 and len(s.requests)==p['max_requests_per_run']
    assert any(e['kind']=='T1_ACP_BUDGET_RESERVED_FOR_PAGE_PROBES' for e in s.events)
    assert any('T1_ACP_BUDGET_RESERVED_FOR_PAGE_PROBES' in page.get('flags',[]) and page['page_complete'] is False for page in s.saved_pages)


def test_move_bare_shell_guard_rejects_ordinary_parser_miss_and_new_metadata():
    from amazon_es_bestseller.highspeed.runner import _same_move_empty_dom
    html=(Path(__file__).parent/'fixtures/highspeed/real_v3_movers_shakers_root.html').read_text(encoding='utf-8')
    assert _same_move_empty_dom(html)
    from bs4 import BeautifulSoup
    for fragment in ('<div class="unknown-new-product">A product</div>','<a href="/dp/opaque">New product</a>',
        '<div data-client-recs-list="[]"></div>','<div data-a-carousel-options="{}"></div>',
        '<span class="zg-bdg-text">#1</span>','<div data-card-metrics-id="unknown-product-layout"></div>'):
        soup=BeautifulSoup(html,'lxml');soup.select_one('#zg-right-col').append(BeautifulSoup(fragment,'lxml'))
        assert not _same_move_empty_dom(str(soup))
    soup=BeautifulSoup(html,'lxml');soup.select_one('#zg-left-col').append(BeautifulSoup('<a href="/gp/movers-and-shakers/kitchen">New child source</a>','lxml'))
    assert not _same_move_empty_dom(str(soup))


def attach_actual_move_rule(tmp_path,p,s,review):
    from amazon_es_bestseller.highspeed.adapters import parse_page
    body=(Path(__file__).parent/'fixtures/highspeed/real_v3_movers_shakers_root.html').read_text(encoding='utf-8')
    source=p['sources'][1];url=source['page_urls'][0]
    old=next(page for page in s.saved_pages if page['source_id']==source['source_id'])
    receipt=next(r for r in s.responses if r['source_id']==source['source_id'])
    saved=tmp_path/'real_move_old_capture.html';saved.write_bytes(body.encode('utf-8'))
    receipt.update(html_path=str(saved),evidence_path=str(saved),sha256=reference(saved)['sha256'])
    parsed=parse_page(body,source=source,run_id=p['run_id'],page_url=url,page_number=1,
        observed_at=receipt['observed_at'],evidence_path=str(saved),origin='LIVE_CAPTURE',http_status=200)
    assert parsed['page']['page_content_state']=='PARSER_EMPTY_UNVERIFIED'
    journal_path=tmp_path/'real_move_old_journal.json'
    page={**parsed['page'],'page_key':old['page_key'],'source':source,'journal_path':str(journal_path)}
    immutable_json(journal_path,sealed({'run_id':p['run_id'],'page_key':old['page_key'],'page':page,'observations':[]}))
    s.saved_pages[s.saved_pages.index(old)]=page
    review['prior_page_journal_references']=[reference(page['journal_path']) for page in s.saved_pages]
    review['retained_failed_source_rules']=[{'source_id':source['source_id'],'source_type':'MOVERS_SHAKERS',
        'page_url':url,'retained_state':'PARSER_EMPTY_UNVERIFIED','prior_failure_journal_reference':reference(journal_path),
        'prior_html_reference':reference(saved)}]
    return body,source,page


@pytest.mark.parametrize('new_dom_product',[False,True])
def test_actual_move_fresh_failure_retained_only_for_same_bare_shell(tmp_path,new_dom_product):
    from amazon_es_bestseller.highspeed.adapters import parse_page
    p,a,s,t,r,review=reverify_setup(tmp_path)
    body,source,old=attach_actual_move_rule(tmp_path,p,s,review)
    old_ref=reference(old['journal_path']);old_parser=r.parser;old_fetch=t.fetch_page
    fresh_body=body
    if new_dom_product:
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(body,'lxml');soup.select_one('#zg-right-col').append(BeautifulSoup('<div class="unknown-new-layout">A product</div>','lxml'))
        fresh_body=str(soup)
    def fetch(url):
        if url==source['page_urls'][0]:t.calls.append(url);return {'url':url,'status_code':200,'text':fresh_body}
        return old_fetch(url)
    t.fetch_page=fetch
    def parser(html,**kw):
        if kw['source']['source_id']==source['source_id']:
            return parse_page(html,source=source,run_id=p['run_id'],page_url=kw['page_url'],page_number=1,
                observed_at=kw['observed_at'],evidence_path=kw['evidence_path'],origin='LIVE_CAPTURE',http_status=200)
        return old_parser(html,**kw)
    r.parser=parser
    result=r.run(p,authorization=approve(tmp_path,a,review),transport=t,resume=True)
    assert reference(old['journal_path'])==old_ref
    if new_dom_product:
        assert len(t.calls)==11 and result['run']['stop_reason']=='PAGE_PARSE_OR_ACCESS_FAILED'
        assert not any(e['kind']=='T1_DIAGNOSTIC_FAILED_PAGE_RETAINED' for e in s.events)
    else:
        assert len(t.calls)==18
        events=[e for e in s.events if e['kind']=='T1_DIAGNOSTIC_FAILED_PAGE_RETAINED']
        assert len(events)==1 and events[0]['fresh_html_reference']['sha256']==hashlib.sha256(body.encode()).hexdigest()
        latest=s.saved_pages[-8] # second fresh source is MOVE.
        assert latest['status']=='FAILED' and latest['page_complete'] is False and not latest.get('terminal_for_snapshot')
