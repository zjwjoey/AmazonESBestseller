"""Offline protocol fixtures only; mocks never establish LIVE stage acceptance."""
import json
from pathlib import Path
import pytest
from amazon_es_bestseller.multisource.storage import digest
from amazon_es_bestseller.highspeed.contracts import DEFAULT_CONFIG
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.v3_workflows import replay_v3,report_replay_v3,prepare_v3,readonly_store,validate_replay_receipt,archive_history_v3,recover_history_supplement,verified_pending_navigation

def old_capture(tmp_path,v3=False):
    root=tmp_path/'old';store=RunStore(root);url='https://www.amazon.es/gp/bestsellers/home'
    source={'source_id':'home','source_type':'BEST_SELLERS','actual_source_url':url,'page_urls':[url],'research_category':None}
    plan={**DEFAULT_CONFIG,'mode':'HIGH_SPEED_V2','run_id':'old','snapshot_id':'old','stage':'T1','origin':'LIVE_CAPTURE','sources':[source]}
    mapping=tmp_path/'mapping.json';mapping.write_text('{"rules":[]}',encoding='utf-8')
    if v3:
        from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
        source['parser_profile']='V3'
        plan.update(snapshot_contract_version=3,normalizer_version=NORMALIZER_VERSION)
        plan['category_mapping_config_reference']={'path':str(mapping),'sha256':digest(mapping.read_bytes())}
    store.begin_run(plan)
    html='<h1>Los más vendidos</h1><div id="zg"><div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/product/one"><div data-product-title>Exact source title</div></a></div></div>'
    observed='2026-10-14T00:00:00Z'
    request=store.reserve_request('old',{'kind':'PAGE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
    receipt=store.save_response('old',request,{'text':html,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
    parsed=parse_page(html,source=source,run_id='old',page_url=url,page_number=1,observed_at=observed,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
    if v3:
        for row in parsed['observations']:
            row.update(origin='LIVE_CAPTURE',capture_run_id='old',source_completeness_status='UNKNOWN',
                capture_receipt_reference={'path':receipt['evidence_path'],'sha256':digest(Path(receipt['evidence_path']).read_bytes())})
    store.commit_page('old','page',{**parsed['page'],'page_key':'page','source':source},parsed['observations']);store.close()
    mapping=tmp_path/'mapping.json';mapping.write_text('{"rules":[]}',encoding='utf-8')
    return root,mapping

def test_replay_is_independent_derivation_with_real_receipt_reference_and_no_requests(tmp_path):
    root,mapping=old_capture(tmp_path);before=digest((root/'index.sqlite3').read_bytes())
    result=replay_v3(root,'old',tmp_path/'new',mapping_config=mapping)
    assert result['counts']=={'pages':1,'observations':1,'original_latest_page_observations':1}
    assert result['live_requests']==0 and result['request_reservations']==0
    assert digest((root/'index.sqlite3').read_bytes())==before
    with readonly_store(result['store_root']) as store:
        rows=list(store.iter_observations(result['run_id']));source=store.get_run(result['run_id'])['plan']['sources'][0]
    assert rows[0]['origin']=='HISTORICAL_REPLAY'
    assert rows[0]['capture_run_id']=='old'
    assert rows[0]['observed_at']=='2026-10-14T00:00:00Z'
    assert rows[0]['historical_observation_ids']
    assert validate_replay_receipt(rows[0],source)
    report=report_replay_v3(result['store_root'],result['run_id'],tmp_path/'reports')
    assert report['manifest']['qa_ok']
    assert report['manifest']['matrix_cells']==75
    assert report['manifest']['candidate_records']==1
    assert report['manifest']['T1_T2_T3_passed'] is False

def test_old_receipt_hash_corruption_cannot_verify_projection(tmp_path):
    root,mapping=old_capture(tmp_path);result=replay_v3(root,'old',tmp_path/'new',mapping_config=mapping)
    with readonly_store(result['store_root']) as store:
        row=next(store.iter_observations(result['run_id']));source=store.get_run(result['run_id'])['plan']['sources'][0]
    row['capture_receipt_reference']['sha256']='0'*64
    with pytest.raises(ValueError,match='REFERENCE_HASH'):validate_replay_receipt(row,source)


def test_all_history_keeps_older_actual_capture_membership_and_rating_tuple(tmp_path):
    root,mapping=old_capture(tmp_path)
    store=RunStore(root);source=store.get_run('old')['plan']['sources'][0];url=source['actual_source_url']
    html='<h1>Los más vendidos</h1><div id="zg"><div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/product/one"><div data-product-title>Changed visible title</div></a></div></div>'
    observed='2026-10-14T01:00:00Z'
    request=store.reserve_request('old',{'kind':'PAGE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
    receipt=store.save_response('old',request,{'text':html,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
    parsed=parse_page(html,source=source,run_id='old',page_url=url,page_number=1,observed_at=observed,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
    store.commit_page('old','page',{**parsed['page'],'page_key':'page','source':source},parsed['observations']);store.close()
    replay=replay_v3(root,'old',tmp_path/'new',mapping_config=mapping)
    history=archive_history_v3(root,'old',replay['store_root'],replay['run_id'],tmp_path/'history')
    assert history['manifest']['counts']['original_index_rows']==2
    assert history['manifest']['counts']['declared_parser_dom_capture_bound']==2
    assert len(history['manifest']['missing_current_ids_from_latest_view'])==1
    with readonly_store(replay['store_root']) as store:run=store.get_run(replay['run_id'])
    rows,qa,pages=recover_history_supplement(Path(history['directory'])/'manifest.json',run)
    assert qa['ok'] and len(rows)==1 and len(pages)==1
    assert rows[0]['observed_at']=='2026-10-14T00:00:00Z'
    report=report_replay_v3(replay['store_root'],replay['run_id'],tmp_path/'report',history_manifest=Path(history['directory'])/'manifest.json')
    assert report['manifest']['candidate_records']==1
    assert report['manifest']['supplemental_historical_capture_observations']==1
    candidate=json.loads(Path(report['manifest']['artifacts']['candidates']['jsonl']['path']).read_text(encoding='utf-8').splitlines()[0])
    assert len(candidate['source_memberships'])==2 and len(candidate['rating_history'])==2
    artifacts=report['manifest']['artifacts']
    combined=json.loads(Path(artifacts['qa']['path']).read_text(encoding='utf-8'))
    central=json.loads(Path(artifacts['source_quality']['path']).read_text(encoding='utf-8'))['card_field_quality']
    assert central['counts']['observations']==1 and combined['counts']['observations']==2
    assert sum(r['observations'] for r in combined['source_scope'])==2
    for field in ('rating','review_count'):
        assert sum(r['observations'] for r in combined['field_status_by_reverified_page_state'] if r['field']==field)==2
        assert any(r['page_state']=='HISTORICAL_SUPPLEMENT_COMPLETENESS_UNVERIFIED' for r in combined['field_status_by_reverified_page_state'] if r['field']==field)
    # Reproduce the earlier metadata alias defect without changing source files.
    from copy import deepcopy
    from amazon_es_bestseller.multisource.storage import immutable_json,sealed
    from amazon_es_bestseller.highspeed.v3_workflows import reconcile_report_scope
    source_quality=json.loads(Path(artifacts['source_quality']['path']).read_text(encoding='utf-8'))
    supplement=report['manifest']['all_history_coverage_qa']['supplement_field_qa']
    for key,value in supplement['counts'].items():
        if isinstance(value,int):source_quality['card_field_quality']['counts'][key]+=value
    source_quality['card_field_quality']['source_scope'].extend(supplement['source_scope'])
    bad_scope=tmp_path/'bad_scope.json';immutable_json(bad_scope,source_quality)
    original_projection=deepcopy(report['manifest']);original_projection['artifacts']['source_quality']={'path':str(bad_scope),'sha256':digest(bad_scope.read_bytes())}
    old_manifest=tmp_path/'old_manifest.json';immutable_json(old_manifest,sealed(original_projection))
    reconciled=reconcile_report_scope(old_manifest,tmp_path/'reconciled')
    fixed_source=json.loads(Path(reconciled['manifest']['artifacts']['source_quality']['path']).read_text(encoding='utf-8'))
    fixed_qa=json.loads(Path(reconciled['manifest']['artifacts']['qa']['path']).read_text(encoding='utf-8'))
    assert fixed_source['card_field_quality']['counts']['observations']==1
    assert fixed_qa['counts']['observations']==2
    assert reconciled['manifest']['artifacts']['candidates']==artifacts['candidates']
    assert reconciled['manifest']['independent_all_export_fields_reverified']


def test_pending_navigation_pack_rebuilds_exact_literal_dom_and_rejects_forgery(tmp_path):
    from amazon_es_bestseller.highspeed.sources import discover_sources
    url='https://www.amazon.es/gp/most-wished-for/home';html='<div id="zg_browseRoot"><a href="/gp/most-wished-for/home/123">Real category</a></div>'
    path=tmp_path/'page.html';path.write_text(html,encoding='utf-8')
    sources=discover_sources(html,'MOST_WISHED_FOR',url,evidence_path=str(path),observed_at='2026-10-14T00:00:00Z',origin='HISTORICAL_REPLAY')
    pack=tmp_path/'pack.json';pack.write_text(json.dumps({'approved':False,'live_enabled':False,'sources':sources}),encoding='utf-8')
    rows,reference=verified_pending_navigation(pack)
    assert rows[0]['page_urls']==[sources[0]['actual_source_url']] and rows[0]['current_availability_verified'] is False
    root,mapping=old_capture(tmp_path/'original')
    pending=prepare_v3(root,'old',tmp_path/'prepared',mapping_config=mapping,pending_source_pack=pack)
    capacity=pending['manifest']['source_capacity_estimate_reference']
    from amazon_es_bestseller.multisource.storage import read_sealed
    capacity_data=read_sealed(Path(capacity['path']))
    assert capacity['sha256']==digest(Path(capacity['path']).read_bytes())
    assert capacity_data['live_requests']==0 and capacity_data['target_supply_sufficiency']=='UNVERIFIED'
    assert capacity_data['current_capacity_upper_bound'] is None
    sources[0]['discovery_evidence'][0]['raw_href']='/gp/most-wished-for/home/FAKE'
    pack.write_text(json.dumps({'approved':False,'live_enabled':False,'sources':sources}),encoding='utf-8')
    with pytest.raises(ValueError,match='LITERAL_DOM'):verified_pending_navigation(pack)

def test_replay_never_overwrites_existing_or_historical_directory(tmp_path):
    root,mapping=old_capture(tmp_path)
    with pytest.raises(ValueError,match='SEPARATE'):replay_v3(root,'old',root/'nested',mapping_config=mapping)
    with pytest.raises(ValueError,match='ALREADY_EXISTS'):replay_v3(root,'old',tmp_path,mapping_config=mapping)

def test_pending_templates_are_new_unapproved_live_disabled_and_literal_sources(tmp_path):
    root,mapping=old_capture(tmp_path);result=prepare_v3(root,'old',tmp_path/'plans',mapping_config=mapping,runtime_root=tmp_path/'machine_runtime')
    launcher=Path(result['manifest']['launcher']['path']).read_text(encoding='utf-8-sig')
    assert '--profile-dir $ProfilePath' in launcher
    for stage,item in result['manifest']['stages'].items():
        plan=json.loads(Path(item['plan']['path']).read_text(encoding='utf-8'))
        auth=json.loads(Path(item['authorization']['path']).read_text(encoding='utf-8'))
        assert not plan['live_enabled'] and not auth['approved']
        assert plan['probe_all_reviewed_page_urls'] is (stage=='T1')
        assert auth['probe_all_reviewed_page_urls'] is plan['probe_all_reviewed_page_urls']
        assert Path(auth['store_root']).parents[1]==tmp_path/'machine_runtime'
        assert not Path(auth['store_root']).exists()
        assert item['browser_profile']==str(Path(auth['store_root']).parent/'browser_profile')
        assert plan['run_id']!='old' and plan['origin']=='LIVE_CAPTURE'
        assert plan['sources'][0]['page_urls']==['https://www.amazon.es/gp/bestsellers/home']
        assert plan['request_pacing_seconds']==3 and plan['parallelism']==1
        assert plan['sources'][0]['parser_profile']=='V3'
        assert not plan['sources_reviewed']


def test_pending_missing_types_probe_new_deep_sources_before_page_budget_exhaustion():
    from amazon_es_bestseller.highspeed.v3_workflows import pending_source_order_v3
    sources=[];new=[]
    for kind,path in [('BEST_SELLERS','bestsellers'),('NEW_RELEASES','new-releases'),
                      ('MOST_WISHED_FOR','most-wished-for'),('MOST_GIFTED','most-gifted')]:
        for slot in range(40):
            url='https://www.amazon.es/gp/'+path+('' if slot==0 else '/department'+str(slot))
            sources.append({'source_id':kind+str(slot),'source_type':kind,'actual_source_url':url,'page_urls':[url]})
        if kind in {'MOST_WISHED_FOR','MOST_GIFTED'}:
            for slot in range(2):
                url='https://www.amazon.es/gp/'+path+'/real-node/'+str(slot)
                row={'source_id':kind+'deep'+str(slot),'source_type':kind,'actual_source_url':url,'page_urls':[url],'enabled':False}
                sources.append(row);new.append(row)
    ordered=pending_source_order_v3(sources,new)
    assert {s['source_id'] for s in ordered}=={s['source_id'] for s in sources}
    assert len(ordered)==len(sources)
    for kind in ('MOST_WISHED_FOR','MOST_GIFTED'):
        queue=[s for s in ordered if s['source_type']==kind]
        assert queue[0]['source_id']==kind+'0'
        assert [s['source_id'] for s in queue[1:3]]==[kind+'deep0',kind+'deep1']
        assert all(not s['enabled'] for s in queue[1:3])
    assert {s['source_id'] for s in new}<={s['source_id'] for s in ordered[:150]}

def test_live_v3_standard_report_exports_full_contract_from_sealed_same_run_receipt(tmp_path):
    from amazon_es_bestseller.highspeed.reports import export_report
    from amazon_es_bestseller.highspeed.v3_workflows import validate_live_receipt
    root,mapping=old_capture(tmp_path,v3=True)
    with readonly_store(root) as store:
        row=next(store.iter_observations('old'));source=store.get_run('old')['plan']['sources'][0]
        assert validate_live_receipt(row,source)
        report=export_report(store,'old',tmp_path/'report')
    assert report['manifest']['origin']=='LIVE_CAPTURE'
    assert report['manifest']['qa_ok']
    reference=report['manifest']['artifacts']['candidates']['jsonl']
    candidate=json.loads(Path(reference['path']).read_text(encoding='utf-8'))
    assert candidate['source_memberships'] and candidate['rating_history']
    assert candidate['product_title_es']=='Exact source title'
    assert candidate['rating_status']=='NOT_DISPLAYED'
    row['capture_run_id']='another-run'
    assert not validate_live_receipt(row,source)

def test_per_audit_receipt_cache_invalidates_changed_original_evidence(tmp_path):
    from collections import OrderedDict
    root,mapping=old_capture(tmp_path);result=replay_v3(root,'old',tmp_path/'new',mapping_config=mapping)
    with readonly_store(result['store_root']) as store:
        row=next(store.iter_observations(result['run_id']));source=store.get_run(result['run_id'])['plan']['sources'][0]
    cache=OrderedDict();assert validate_replay_receipt(row,source,cache=cache)
    assert validate_replay_receipt(row,source,cache=cache)
    assert cache
    Path(row['evidence_path']).write_text('Changed source body',encoding='utf-8')
    assert not validate_replay_receipt(row,source,cache=cache)

def test_missing_v3_mapping_snapshot_rejects_before_browser_or_request(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from test_highspeed_runner import plan,Store,NOW
    from amazon_es_bestseller.highspeed import cli
    from amazon_es_bestseller.highspeed.runner import HighSpeedRunner
    from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
    from amazon_es_bestseller.access.browser import BrowserSession
    p=plan(tmp_path,'T1');p.update(snapshot_contract_version=3,normalizer_version=NORMALIZER_VERSION)
    p['sources'][0]['parser_profile']='V3'
    monkeypatch.setattr(cli,'validate_authorization',lambda *a,**kw:True)
    monkeypatch.setattr(HighSpeedRunner,'_gate',lambda *a,**kw:{'state':'NORMAL'})
    monkeypatch.setattr(BrowserSession,'__enter__',lambda self:(_ for _ in ()).throw(AssertionError('Browser must not be opened')))
    store=Store(tmp_path/'store')
    with pytest.raises(ValueError,match='V3_MAPPING_CONFIG_REFERENCE_REQUIRED'):
        cli._run_live_locked(SimpleNamespace(live=True,offline=False,operation='run'),store,p,{},entry_time=NOW.isoformat())
    assert not store.requests and store.run is None

def test_large_nested_candidate_history_roundtrips_without_csv_truncation(tmp_path):
    import csv
    from amazon_es_bestseller.highspeed.v3_workflows import bounded_csv_fields,_write_records
    huge=[{'membership':'x'*140000,'raw_href':'/product/source'}]
    artifact=_write_records(tmp_path,'large',[{'candidate_id':'c','source_memberships':huge,'rating_history':huge}])
    previous=csv.field_size_limit()
    with bounded_csv_fields(),Path(artifact['csv']['path']).open(encoding='utf-8-sig',newline='') as h:
        row=next(csv.DictReader(h))
        assert json.loads(row['source_memberships'])==huge
        assert json.loads(row['rating_history'])==huge
        assert csv.field_size_limit()==64*1024*1024
    assert csv.field_size_limit()==previous
    with pytest.raises(RuntimeError):
        with bounded_csv_fields():raise RuntimeError('must restore reader state')
    assert csv.field_size_limit()==previous
