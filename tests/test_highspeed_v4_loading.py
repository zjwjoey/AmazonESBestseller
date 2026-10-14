"""Actual saved entry DOM and native contracts; responses are offline simulation."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.v4_navigation import verify_pagination_reference
from amazon_es_bestseller.highspeed.v4_authorization import validate_plan_v4
from amazon_es_bestseller.highspeed.v4_loading import validate_dynamic_descriptor
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.multisource.storage import immutable_json
from test_highspeed_v4_execution import setup_scope,FakeTransport,NOW
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.multisource.storage import digest
from amazon_es_bestseller.highspeed.cli import save_native_contract
from types import SimpleNamespace

ROOT=Path(__file__).parent/'fixtures/highspeed_v4/loading'
PROOFS=json.loads((ROOT/'provenance.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('variant',['actual','legacy','restricted','footer','wrong_title'])
def test_actual_lightning_empty_statement_only_in_owned_normal_grid(variant):
    source=next(p['source'] for p in PROOFS if p['source']['source_subtype']=='LIGHTNING_DEAL_FILTER')
    path=ROOT/'lightning_explicit_empty.html';body=path.read_text(encoding='utf8')
    if variant in {'footer','wrong_title'}:
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(body,'lxml')
        if variant=='footer':
            grid=soup.select_one('[data-testid="discount-asin-grid"]');grid.decompose()
            soup.body.append(BeautifulSoup('<p>No se ha encontrado ninguna coincidencia.</p>','lxml').p)
        else:soup.title.string='An unrelated page'
        body=str(soup)
    r=parse_v4_page(body,source=source,page_url=source['actual_source_url'],run_id='offline-empty',
        observed_at='2026-10-14T23:50:00Z',evidence_path=str(path),http_status=403 if variant=='restricted' else 200,
        parser_profile_version='multisource_v4.2.capture_bound' if variant=='legacy' else None)
    assert r['page']['completeness_status']=='UNKNOWN'
    assert not r['observations']
    assert (r['page']['status']=='EXPLICIT_EMPTY')==(variant=='actual')
    if variant=='restricted':assert r['page']['status']=='ACCESS_RESTRICTED'


def pagination():
    p=deepcopy(PROOFS[0]);ref=p['pagination'];ref['evidence_path']=str((ROOT/p['file']).resolve())
    return p['source'],ref


def test_real_saved_favorites_next_href_same_source_only():
    source,ref=pagination()
    assert verify_pagination_reference(ref,source)==ref
    assert 'page=2' in ref['url']


@pytest.mark.parametrize('bad',['url','href','hash','node','parent','source'])
def test_pagination_cannot_expand_category_or_invent_href(bad):
    source,ref=pagination()
    if bad=='url':ref['url']+='&page=3'
    elif bad=='href':ref['raw_href']=ref['raw_href'].replace('page=2','page=3')
    elif bad=='hash':ref['evidence_sha256']='0'*64
    elif bad=='node':ref['url']=ref['url'].replace('88626813031','1')
    elif bad=='parent':ref['page_url']='https://www.amazon.es/s?i=kitchen&srs=1'
    else:source['source_type']='DEALS'
    with pytest.raises(ValueError):verify_pagination_reference(ref,source)


def test_nine_pages_same_eight_sources_counted_and_bound_offline(tmp_path):
    plan,auth=setup_scope(tmp_path);source,ref=pagination()
    s=next(s for s in plan['sources'] if s['source_id']==source['source_id'])
    s['pagination_evidence']=[ref];s['page_urls'].append(ref['url'])
    auth['sources'][s['source_id']]['page_urls']=s['page_urls']
    auth['plan_sha256']=digest(plan)
    validate_plan_v4(plan)
    class PaginationTransport(FakeTransport):
        def fetch_page(self,url):
            if url==ref['url']:
                self.calls.append(url)
                return {'status_code':200,'url':url,'text':Path(ref['evidence_path']).read_text(encoding='utf8')}
            return super().fetch_page(url)
    store=RunStore(auth['store_root'])
    try:
        t=PaginationTransport(plan,auth)
        result=V4T1Runner(store,clock=lambda:NOW,sleep=lambda n:None).run(plan,authorization=auth,transport=t)
        assert result['summary']['requests']==9
        assert len(t.calls)==9 and len(plan['sources'])==8
        assert result['quality']['central_full_dom_review_passed'],result['quality']['errors']
        page=next(p for p in store.pages(plan['run_id']) if p['page_url']==ref['url'])
        assert page['source_id']==s['source_id'] and page['completeness_status']=='UNKNOWN'
        assert result['run']['quality_gate_passed'] is False
    finally:store.close()


def dynamic(tmp_path,kind):
    p=next(x for x in PROOFS if x['source']['source_type']==kind);s=p['source']
    captured=json.loads((ROOT/('lightning_contract.json' if kind=='DEALS' else 'haul_scroll_contract.json')).read_text(encoding='utf8'))
    # Simulated fresh capture context, retaining the actual literal endpoint/body.
    d={**captured,'source_id':s['source_id'],'source_type':kind,'run_id':'offline-protocol',
       'request_contract_source':'NATIVE_BROWSER_REQUEST_HELD','request_sent':False,
       'parent_html_path':str((ROOT/p['file']).resolve()),'parent_html_sha256':p['sha256'],
       'captured_at':p['observed_at']}
    path=tmp_path/'contract.json';immutable_json(path,d)
    d.update(native_contract_evidence_path=str(path),native_contract_evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return s,d


@pytest.mark.parametrize('kind',['DEALS','AMAZON_HAUL'])
def test_literal_real_loading_contract_requires_saved_product_context(tmp_path,kind):
    s,d=dynamic(tmp_path,kind)
    validate_dynamic_descriptor(d['url'],s,{'allow_native_dynamic':True},{'run_id':d['run_id']},d)


@pytest.mark.parametrize('bad',['url','payload','parent','root','run','approval','hash'])
def test_dynamic_rejects_changed_native_bytes_or_unapproved_scope(tmp_path,bad):
    s,d=dynamic(tmp_path,'DEALS');a={'run_id':d['run_id']};scope={'allow_native_dynamic':True}
    if bad=='url':d['url']+='&startIndex=999'
    elif bad=='payload':d['payload']='changed'
    elif bad=='parent':d['parent_page_url']='https://www.amazon.es/deals'
    elif bad=='root':d['parent_html_sha256']='0'*64
    elif bad=='run':a['run_id']='wrong'
    elif bad=='approval':scope['allow_native_dynamic']=False
    else:d['native_contract_evidence_sha256']='0'*64
    with pytest.raises(ValueError):validate_dynamic_descriptor(d['url'],s,scope,a,d)


def test_dynamic_rendered_frame_requires_actual_reserved_receipt_and_local_delivery(tmp_path):
    plan,auth=setup_scope(tmp_path)
    s=next(x for x in plan['sources'] if x['source_type']=='AMAZON_HAUL')
    plan['sources']=[s];auth['sources']={s['source_id']:auth['sources'][s['source_id']]}
    auth['sources'][s['source_id']]['allow_native_dynamic']=True
    proof=next(p for p in PROOFS if p['source']['source_type']=='AMAZON_HAUL')
    auth['sources'][s['source_id']]['evidence']={'path':str((ROOT/proof['file']).resolve()),'sha256':proof['sha256'],'reviewed':True}
    auth['plan_sha256']=digest(plan)
    store=RunStore(auth['store_root'])
    clock=[NOW]
    def wait(n):
        from datetime import timedelta
        clock[0]+=timedelta(seconds=1)
    class DynamicFake(FakeTransport):
        def __init__(self):
            super().__init__(plan,auth);self.loading_events=[];self.once=False
            self.session=SimpleNamespace(page=SimpleNamespace(wait_for_timeout=wait,
                content=lambda:Path(auth['sources'][s['source_id']]['evidence']['path']).read_text(encoding='utf8')))
        def prepare_dynamic(self,source,receipt):
            if self.once:return None
            self.once=True
            d=json.loads((ROOT/'haul_scroll_contract.json').read_text(encoding='utf8'))
            d.update(source_id=s['source_id'],source_type=s['source_type'],parent_html_path=receipt['html_path'],
                parent_html_sha256=receipt['sha256'],request_contract_source='NATIVE_BROWSER_REQUEST_HELD',request_sent=False)
            save_native_contract(store,plan,d)
            d['captured_at']=json.loads(Path(d['native_contract_evidence_path']).read_text(encoding='utf8'))['captured_at']
            return {**d,'referer':s['actual_source_url']}
        def fetch_ajax(self,url,**kwargs):
            self.calls.append(url);return {'status_code':200,'url':url,'text':'{"offline_simulated":true}'}
        def acknowledge_native_response(self,receipt):
            ref={'path':receipt['evidence_path'],'sha256':hashlib.sha256(Path(receipt['evidence_path']).read_bytes()).hexdigest()}
            self.loading_events.append({'origin':'LOCAL_UI_REPLAY','new_network_request':False,
                'request_id':receipt['request_id'],'receipt_reference':ref})
        def cancel_dynamic(self):pass
    try:
        t=DynamicFake();r=V4T1Runner(store,clock=lambda:clock[0],sleep=lambda n:None).run(plan,authorization=auth,transport=t)
        assert r['summary']['requests']==2 and len(t.calls)==2
        assert r['quality']['central_full_dom_review_passed'],r['quality']['errors']
        assert r['quality']['reviewed_capture_frames']==2
        page=store.pages(plan['run_id'])[0]
        assert page['source_representation']=='RENDERED_DOM_DERIVATION' and page['completeness_status']=='UNKNOWN'
        from amazon_es_bestseller.highspeed.v4_quality import audit_v4
        bad=deepcopy(page);bad['rendered_derivation']['local_ui_deliveries']=[]
        assert not audit_v4([bad],list(store.iter_observations(plan['run_id'])),[s],require_live=True)['central_full_dom_review_passed']
    finally:store.close()
