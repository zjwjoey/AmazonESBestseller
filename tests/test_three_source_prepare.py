"""Planning tests stub formal review, never impersonate real LIVE prerequisites."""
from copy import deepcopy
import json,hashlib
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed import three_prepare as prepare
from amazon_es_bestseller.highspeed.three_navigation import FAVORITES_ROOT,FAMILIES,discover_navigation
from amazon_es_bestseller.multisource.storage import immutable_json,digest
from test_highspeed_v4_supplement import saved, reference


def args(tmp_path,monkeypatch,saved):
    pack,newrow,nextrow,_,priors=deepcopy(saved)
    t2=tmp_path/'TEST_ONLY_T2.json';immutable_json(t2,{'TEST_ONLY':True})
    archive=tmp_path/'archive.json';immutable_json(archive,{'kind':'HISTORICAL_NAVIGATION_ONLY',
        'contract':'V4_THREE_SOURCE_5001','approved':False,'new_network_requests':0,'historical_products_added_to_target':0,'captures':[]})
    task=tmp_path/'task.txt';task.write_text('TEST_ONLY Three source target task','utf8')
    selection={'kind':'THREE_SOURCE_5001_SELECTION','reason':'TEST_ONLY exact saved navigation proposal',
        'seed_urls':['https://www.amazon.es/gp/bestsellers','https://www.amazon.es/gp/new-releases',FAVORITES_ROOT],
        'prior_source_ids':[],'historical_source_urls':[],'navigation_source_urls':[newrow['url']],
        'pagination_urls':[],'source_budgets':{k:{'requests':40,'PAGE':10,'native':0 if k=='CUSTOMER_FAVORITES' else 30} for k in FAMILIES},
        'limits':{'max_duration_hours':1,'max_native_requests_per_source':20,'max_raw_observations_per_run':50000,'history_observation_limit':100000}}
    path=tmp_path/'selection.json';immutable_json(path,selection);seen=[]
    def verify(ref,stage):
        seen.append(stage)
        if stage=='T2':return pack
        assert ref==priors[stage]
        return {}
    monkeypatch.setattr(prepare,'verify_stage_reference',verify)
    return [reference(t2),reference(archive),reference(path),reference(task),tmp_path/'out',tmp_path/'runtime',tmp_path/'gate.json'],selection,seen


def replace_selection(arguments,value):
    p=Path(arguments[2]['path']);p.write_text(json.dumps(value),'utf8');arguments[2]=reference(p)


def test_disabled_new_campaign_binds_task_exact_urls_and_independent_targets(tmp_path,monkeypatch,saved):
    arguments,selection,seen=args(tmp_path,monkeypatch,saved)
    result=prepare.prepare_three(*arguments);directory=Path(result['directory'])
    plan=json.loads((directory/'plan.pending.json').read_text('utf8'));auth=json.loads((directory/'authorization.pending.json').read_text('utf8'))
    assert seen==['T2','T0','T1'] and result['source_count']==4 and result['exact_PAGE_count']==4
    assert plan['source_targets']=={k:5001 for k in FAMILIES} and plan['target_unique_links']==15003
    assert plan['campaign_id'].startswith('three_source_5001_') and plan['run_id'].startswith('three_source_batch_')
    assert plan['task_reference']==arguments[3] and not plan['live_enabled'] and not auth['approved']
    assert not auth['reviewed_at'] and not auth['expires_at'] and not auth['approval_id']
    assert result['historical_candidates_added']==result['new_campaign_candidates']==result['new_network_requests']==0
    assert auth['prerequisites']['T2']==arguments[0] and auth['campaign_id']==plan['campaign_id']
    assert result['pending_plan_sha256']==digest(plan)==auth['plan_sha256']
    assert result['enabled_plan_sha256_preview']==digest({**plan,'live_enabled':True})
    manifest=json.loads((directory/'manifest.json').read_text('utf8'))
    assert all(reference(directory/f['name'])['sha256']==f['sha256'] for f in manifest['files'])
    assert not Path(auth['store_root']).exists()


@pytest.mark.parametrize('problem',['invented_nav','invented_archive','unrelated_seed','duplicate','next_jump','budget','task_changed'])
def test_scope_evidence_and_budget_errors_do_not_publish_proposal(tmp_path,monkeypatch,saved,problem):
    arguments,selection,_=args(tmp_path,monkeypatch,saved)
    if problem=='invented_nav':selection['navigation_source_urls']=['https://www.amazon.es/gp/bestsellers/invented']
    elif problem=='invented_archive':selection['historical_source_urls']=['https://www.amazon.es/gp/bestsellers/not_seen']
    elif problem=='unrelated_seed':selection['seed_urls'].append('https://www.amazon.es/deals')
    elif problem=='duplicate':selection['seed_urls']*=2
    elif problem=='next_jump':selection['pagination_urls']=[saved[2]['url']]
    elif problem=='budget':selection['source_budgets']['BEST_SELLERS']['requests']=True
    else:Path(arguments[3]['path']).write_text('changed task','utf8')
    replace_selection(arguments,selection)
    with pytest.raises((ValueError,KeyError)):prepare.prepare_three(*arguments)
    assert not Path(arguments[4]).exists()


def test_offline_preparation_has_no_live_switch(tmp_path,monkeypatch):
    from amazon_es_bestseller.highspeed.v4_cli import main
    path=tmp_path/'TEST_ONLY.json';immutable_json(path,{})
    monkeypatch.setattr(prepare,'prepare_three',lambda *a,**kw:{'new_network_requests':0})
    argv=['prepare-three-source']
    for key in ('t2','archive','selection','task','out','runtime-root','shared-gate'):argv.extend(['--'+key,str(path)])
    assert main(argv)['new_network_requests']==0
    with pytest.raises(SystemExit):main([*argv,'--live'])
