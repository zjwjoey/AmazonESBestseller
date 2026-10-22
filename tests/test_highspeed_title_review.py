"""Source title projection is independent of live capture and raw SQL updates."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from bs4 import BeautifulSoup

from amazon_es_bestseller.highspeed import adapters
from amazon_es_bestseller.highspeed.title_review import project_title,review_titles,load_title_projection,supplement_title_review
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.multisource.storage import digest,read_sealed,sealed,immutable_json

URL="https://www.amazon.es/gp/bestsellers/home"
TIME="2026-10-13T01:00:00Z"


def fixture(tmp_path,count=2,representation="SERVER",price_only=False):
    cards=''.join("<div class='ranking-card'><span class='zg-bdg-text'>#%d</span><a href='/product/item-%d'><span class='a-size-base p13n-sc-price'>9,99 €</span>%s</a></div>"%
        (i+1,i,"" if price_only else "<div class='p13n-sc-css-line-clamp-3'>Product %d</div>"%i) for i in range(count))
    html="<h1>Los más vendidos</h1>"+cards
    path=tmp_path/"capture.html";path.write_text(html,encoding="utf-8")
    source={"source_id":"home","source_type":"BEST_SELLERS","actual_source_url":URL,"page_urls":[URL]}
    rows=adapters._parse_cards(BeautifulSoup(html,"lxml"),source=source,run_id="run",snapshot_id="snapshot",
        page_url=URL,page_number=1,observed_at=TIME,evidence_path=str(path),sha=digest(path.read_bytes()),
        representation=representation,fragment=representation.startswith("ACP_"))
    for row in rows:row['product_title_raw']='9,99 €'
    return source,rows


@pytest.mark.parametrize("representation",["SERVER","ACP_0","ACP_2"])
def test_title_correction_exact_url_slot_rank_and_html_binding(tmp_path,representation):
    source,rows=fixture(tmp_path,representation=representation)
    before=deepcopy(rows)
    result=project_title(rows[0],source)
    assert result['review_status']=='CORRECTED' and result['corrected_source_title']=='Product 0'
    assert result['original_product_title_raw']=='9,99 €' and result['source_html_sha256']==rows[0]['evidence_sha256']
    assert result['product_title_evidence']['kind']=='SOURCE_VISIBLE_PRODUCT_TITLE'
    assert rows==before


@pytest.mark.parametrize("field,value",[("raw_product_url","/product/item-1"),("representation_slot",1),
    ("ranking_position",2),("rank_context",URL+'#other'),("evidence_sha256","0"*64),
    ("representation","UNKNOWN"),("source_type","MOST_GIFTED")])
def test_mixed_proof_fails_closed_without_corrected_title(tmp_path,field,value):
    source,rows=fixture(tmp_path);row={**rows[0],field:value}
    result=project_title(row,source)
    assert result['review_status']=='UNVERIFIED' and result['corrected_source_title'] is None


def test_price_only_missing_title_never_uses_price_or_url_guess(tmp_path):
    source,rows=fixture(tmp_path,price_only=True)
    result=project_title(rows[0],source)
    assert result['review_status']=='UNVERIFIED' and result['corrected_source_title'] is None


def test_many_same_html_observations_parse_once_and_scope_cache_is_separate(tmp_path,monkeypatch):
    source,rows=fixture(tmp_path,count=50)
    original=adapters._parse_cards;calls=[]
    def tracked(*args,**kwargs):calls.append(kwargs);return original(*args,**kwargs)
    monkeypatch.setattr(adapters,'_parse_cards',tracked)
    cache={}
    assert all(project_title(row,source,cache=cache)['review_status']=='CORRECTED' for row in rows)
    assert len(calls)==1
    changed={**rows[0],'snapshot_id':'other snapshot','observed_at':'2026-10-13T02:00:00Z'}
    assert project_title(changed,source,cache=cache)['review_status']=='CORRECTED'
    assert len(calls)==2


@pytest.mark.parametrize('damage',[None,'title','rank','url'])
@pytest.mark.parametrize('real_fixture',[False,True])
def test_live_legacy_price_title_is_reported_as_parser_defect_not_identity_mismatch(tmp_path,damage,real_fixture):
    from amazon_es_bestseller.highspeed.adapters import parse_page
    from amazon_es_bestseller.highspeed.quality import audit_run
    source,rows=fixture(tmp_path)
    # The exact old selector requires a-link-normal; model actual saved layout.
    path=Path(rows[0]['evidence_path']);html=path.read_text(encoding='utf8').replace("<a href=","<a class='a-link-normal' href=")
    if real_fixture:html=(Path(__file__).parent/'fixtures/highspeed/real_price_title_collision.html').read_text(encoding='utf8')
    store=RunStore(tmp_path/'store');plan=new_plan('legacy_live',stage='T1',origin='LIVE_CAPTURE',live_enabled=True,sources=[source])
    store.begin_run(plan,started_at=TIME)
    request=store.reserve_request('legacy_live',{'kind':'PAGE','url':URL,'source_id':'home','source_type':'BEST_SELLERS','observed_at':TIME})
    receipt=store.save_response('legacy_live',request,{'text':html,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE',
        'source_id':'home','source_type':'BEST_SELLERS','observed_at':TIME,'final_url':URL})
    parsed=parse_page(html,source=source,run_id='legacy_live',page_url=URL,page_number=1,observed_at=TIME,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
    original_cards=adapters._cards(BeautifulSoup(html,'lxml'))
    for row,card in zip(parsed['observations'],original_cards):
        old_node=card.select_one('[data-product-title], .p13n-sc-truncate-desktop-type2, .p13n-sc-truncate, .a-size-base-plus, .a-link-normal .a-size-base')
        old_title=old_node.get_text(' ',strip=True) if old_node else None
        image=card.select_one('img[alt]')
        if not old_title and image:old_title=image.get('alt') or None
        row.update(product_title_raw=old_title,parser_version='link_first_adapters_v2.1')
    if damage:
        field,value={'title':('product_title_raw','Invented title'),'rank':('ranking_position',99),'url':('raw_product_url','/product/not-here')}[damage]
        parsed['observations'][0][field]=value
    store.commit_page('legacy_live','page',parsed['page'],parsed['observations'])
    before=list(map(tuple,store.db.execute('SELECT * FROM observations')))
    qa=audit_run(store,'legacy_live');codes={f['code'] for f in qa['findings']}
    assert qa['quality_gate_passed'] is False
    if damage is None:
        assert 'KNOWN_LEGACY_TITLE_EXTRACTION_DEFECT' in codes
        assert 'RAW_OBSERVATIONS_NOT_BOUND_TO_REPARSED_CARDS' not in codes
        assert 'PAGE_EVIDENCE_AUDIT_FAILED' not in codes
    else:assert 'PAGE_EVIDENCE_AUDIT_FAILED' in codes or 'RAW_OBSERVATIONS_NOT_BOUND_TO_REPARSED_CARDS' in codes
    assert list(map(tuple,store.db.execute('SELECT * FROM observations')))==before


def test_immutable_review_artifact_does_not_change_raw_identity_or_request_counts(tmp_path):
    source,rows=fixture(tmp_path)
    store=RunStore(tmp_path/'store');plan=new_plan('run',sources=[source]);store.begin_run(plan,started_at=TIME)
    page={'source_id':'home','source_type':'BEST_SELLERS','page_url':URL,'actual_count':2,'source_type_verified':True,
        'source_context_valid':True,'access_status':'NORMAL','observed_at':TIME,'origin':'OFFLINE_FIXTURE'}
    store.commit_page('run','page',page,rows)
    before=list(map(tuple,store.db.execute('SELECT * FROM observations')));summary=store.summary('run')
    result=review_titles(store,'run',tmp_path/'delivery')
    assert result['review_counts']=={'CORRECTED':2} and result['live_requests']==0
    verified=load_title_projection(Path(result['directory'])/'manifest.json',store,'run')
    assert len(verified)==2 and all(r['corrected_source_title'] for r in verified.values())
    assert list(map(tuple,store.db.execute('SELECT * FROM observations')))==before
    assert store.summary('run')['unique_links']==summary['unique_links'] and store.summary('run')['requests']==0
    # A later capture is pending for this snapshot, not silently accepted.
    later={**rows[0],'observation_id':'later_observation'}
    store.commit_page('run','later_page',page,[later])
    assert 'later_observation' not in load_title_projection(Path(result['directory'])/'manifest.json',store,'run')
    base_manifest=Path(result['directory'])/'manifest.json'
    base_bytes=base_manifest.read_bytes();base_data=Path(result['artifacts']['title_corrections']['path']).read_bytes()
    final=supplement_title_review(base_manifest,store,'run',tmp_path/'final_paused')
    assert final['newly_reviewed_observations']==1 and final['base_review_observations']==2
    assert len(load_title_projection(Path(final['directory'])/'manifest.json',store,'run'))==3
    assert base_manifest.read_bytes()==base_bytes and Path(result['artifacts']['title_corrections']['path']).read_bytes()==base_data
    # Hash-correct but semantically altered corrections cannot become trusted.
    manifest=read_sealed(Path(result['directory'])/'manifest.json')
    reference=manifest['artifacts']['title_corrections'];path=Path(reference['path'])
    lines=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
    lines[0]['corrected_source_title']='Invented important attribute'
    path.write_text('\n'.join(json.dumps(row) for row in lines)+'\n',encoding='utf8')
    manifest['artifacts']['title_corrections']['sha256']=digest(path.read_bytes())
    tampered=tmp_path/'tampered_manifest.json';immutable_json(tampered,sealed(manifest))
    with pytest.raises(ValueError,match='BINDING_OR_CORRECTION'):load_title_projection(tampered,store,'run')
