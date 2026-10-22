"""TEST_ONLY same-campaign batches cannot manufacture extra candidates."""
from copy import deepcopy
from datetime import timedelta
import json,hashlib
from pathlib import Path
import pytest
from test_three_source_runner import execution_scope,Advancing
from test_highspeed_v4_execution import NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
from amazon_es_bestseller.highspeed.three_report import export_three
from amazon_es_bestseller.highspeed.three_campaign import aggregate_campaign
from amazon_es_bestseller.multisource.storage import digest,immutable_json


def ref(path):return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def test_two_fresh_batches_keep_history_and_deduplicate_within_each_source(tmp_path,monkeypatch):
    references=[];first_plan=None
    for i in range(2):
        p,a=execution_scope(tmp_path/str(i),monkeypatch)
        if first_plan:
            for k in ('task_reference','campaign_id','campaign_created_at'):p[k]=a[k]=first_plan[k]
            a['plan_sha256']=digest(p)
        else:first_plan=deepcopy(p)
        store=RunStore(a['store_root']);clock=[NOW+timedelta(minutes=i)]
        try:
            V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=Advancing(p,a,clock))
            report=export_three(store,p['run_id'],tmp_path/'TEST_ONLY_batches')
            references.append(ref(Path(report['directory'])/'manifest.json'))
        finally:store.close()
    one=json.loads(Path(references[0]['path']).read_text('utf8'))
    result=aggregate_campaign(references,tmp_path/'TEST_ONLY_combined')
    assert not result['accepted'] and result['metrics']['A_raw_observations']==2*one['metrics']['A_raw_observations']
    assert result['metrics']['F_global_conservative_review_groups']==one['metrics']['F_global_conservative_review_groups']
    for k in ('BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES'):
        assert result['metrics']['sources'][k]['E_qualified_review_groups']==one['metrics']['sources'][k]['E_qualified_review_groups']
    d=Path(result['directory']);manifest=json.loads((d/'manifest.json').read_text('utf8'))
    assert manifest['all_supplied_batches_reaudited'] and len(manifest['files'])==12
    with pytest.raises(ValueError,match='UNIQUE_BATCHES'):
        aggregate_campaign([references[0],references[0]],tmp_path/'dup')
    assert not (tmp_path/'dup').exists()


def test_old_T2_report_cannot_enter_new_campaign(tmp_path):
    path=tmp_path/'TEST_ONLY_old.json';immutable_json(path,{'stage':'T2','passed':True})
    with pytest.raises(ValueError,match='ONLY_FORMAL_CAMPAIGN'):
        aggregate_campaign([ref(path)],tmp_path/'out')
    assert not (tmp_path/'out').exists()
