"""Offline new-task configuration, immutable clocks and 100k accounting."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import pytest
from amazon_es_bestseller.highspeed.global_target import configurable_policy,is_global_policy,MAIN_PRIORITY_50000_POLICY
from amazon_es_bestseller.highspeed.snapshot_cli import task_config,validate_config,main
from amazon_es_bestseller.highspeed.task_clock import start_task_clock,validate_task_clock
from amazon_es_bestseller.multisource.storage import read_sealed


@pytest.mark.parametrize(('target','hours'),[(100,2),(23750,6),(100000,24),(100000,72),(100000,168)])
def test_independent_target_duration_and_immutable_resume_clock(tmp_path,target,hours):
    config=task_config(target=target,hours=hours);validate_config(config)
    assert config['target_policy']['target']==target and config['max_duration_hours']==hours
    assert not config['approved'] and not config['live_enabled']
    path=tmp_path/'clock.json';now=datetime(2026,10,29,tzinfo=timezone.utc)
    plan={'run_id':'TEST_ONLY','target_policy':config['target_policy'],'task_clock_path':str(path.resolve())}
    ref=start_task_clock(path,plan,'TEST_ONLY',now=now);before=path.read_bytes()
    start_task_clock(path,plan,'TEST_ONLY',now=now+timedelta(hours=1))
    assert path.read_bytes()==before
    auth={'task_clock_reference':ref}
    clock=validate_task_clock(plan,auth,now=now+timedelta(minutes=1))
    assert datetime.fromisoformat(clock['deadline'].replace('Z','+00:00'))-now==timedelta(hours=hours)
    bad={**plan,'target_policy':configurable_policy(target,hours+1 if hours<168 else hours-1)}
    with pytest.raises(ValueError,match='BINDING'):start_task_clock(path,bad,'TEST_ONLY',now=now)
    assert path.read_bytes()==before


def test_100k_template_and_legacy_50k_remain_distinct():
    old=deepcopy(MAIN_PRIORITY_50000_POLICY);old_config=task_config()
    large=task_config('main-priority-100000');validate_config(large)
    assert large['target_policy']['target']==100000 and large['max_duration_hours']==72
    assert sum(v['PAGE'] for v in large['source_budgets'].values())==10000
    assert old_config['config_contract']=='CURRENT_PAGE_TASK_CONFIG_V1'
    assert old_config['max_duration_hours']==24 and sum(v['PAGE'] for v in old_config['source_budgets'].values())==5000
    assert MAIN_PRIORITY_50000_POLICY==old
    tampered=deepcopy(old);tampered['target']=100000
    with pytest.raises(ValueError):is_global_policy(tampered)


def test_custom_policy_cannot_mutate_legacy_source_role_lists():
    from amazon_es_bestseller.highspeed.global_target import MAIN_PRIORITY_POLICY
    old=deepcopy(MAIN_PRIORITY_POLICY)
    custom=configurable_policy(1000,2);custom['primary_sources'].clear()
    assert MAIN_PRIORITY_POLICY==old
    assert configurable_policy(1000,2)['primary_sources']==old['primary_sources']


@pytest.mark.parametrize(('target','hours'),[(True,24),(0,24),(100001,24),(1000,True),(1000,1),(1000,169),(1000,float('inf'))])
def test_unbounded_or_invalid_custom_policies_fail_closed(target,hours):
    with pytest.raises(ValueError):configurable_policy(target,hours)


def test_explicit_source_minima_budgets_and_scope_validation():
    minima={'BEST_SELLERS':100,'NEW_RELEASES':100,'CUSTOMER_FAVORITES':10}
    budgets={'BEST_SELLERS':20,'NEW_RELEASES':20,'CUSTOMER_FAVORITES':5}
    config=task_config(target=1000,hours=3,source_minima=minima,page_budgets=budgets,source_ids=['reviewed-root'])
    validate_config(config);assert config['source_ids']==['reviewed-root']
    bad=deepcopy(config);bad['max_duration_hours']=24
    with pytest.raises(ValueError,match='DURATION'):validate_config(bad)
    bad=deepcopy(config);bad['source_ids']=['same','same']
    with pytest.raises(ValueError,match='UNIQUE_SOURCE'):validate_config(bad)
    bad=deepcopy(config);bad['source_budgets']['BEST_SELLERS']={'requests':60000,'PAGE':60000,'native':0}
    with pytest.raises(ValueError,match='ENVELOPE'):validate_config(bad)
    with pytest.raises(ValueError):task_config(target=1000,hours=3,source_minima={**minima,'CUSTOMER_FAVORITES':1001})


def test_cli_profile_custom_duration_does_not_start_clock_or_browser(tmp_path,monkeypatch):
    from amazon_es_bestseller.access import browser
    monkeypatch.setattr(browser,'BrowserSession',lambda *a,**k:pytest.fail('BROWSER_STARTED'))
    path=tmp_path/'pending.json'
    result=main(['profile','--target-count','100000','--duration-hours','48','--out',str(path)])
    assert result==json.loads(path.read_text(encoding='utf8'))
    assert result['target_policy']['total_task_hours']==48 and not result['approved']
    assert list(tmp_path.iterdir())==[path]


def test_one_hundred_thousand_unique_groups_boundary_without_network():
    # Synthetic in-memory counter stress only, not source or live acceptance.
    from test_three_source_metrics import real_rows
    from amazon_es_bestseller.highspeed.snapshot_metrics import SnapshotMetricsIndex
    from amazon_es_bestseller.highspeed.v3_links import normalize_product_url_v3
    base=real_rows()[0];index=SnapshotMetricsIndex('TEST_ONLY',task_config('main-priority-100000')['target_policy'])
    def row(i):
        url=f'https://www.amazon.es/dp/B0{i:08d}'
        norm=normalize_product_url_v3(url,base['page_url'])
        return {**base,**norm,'raw_product_url':url,'observation_id':f'TEST_ONLY_{i}',
            'source_type':'BEST_SELLERS' if i<60000 else 'NEW_RELEASES' if i<97000 else 'CUSTOMER_FAVORITES'}
    for offset in range(0,99999,1000):index.add([row(i) for i in range(offset,min(offset+1000,99999))])
    metrics=index.measure()
    assert metrics['F_global_conservative_review_groups']==99999 and not metrics['target_met']
    assert metrics['sources']['CUSTOMER_FAVORITES']['shortfall']==1
    index.add([row(99999)]);metrics=index.measure()
    assert metrics['F_global_conservative_review_groups']==100000 and metrics['target_met']
    index.add([{**row(0),'source_type':'NEW_RELEASES','observation_id':'TEST_ONLY_CROSS_SOURCE_DUPLICATE'}])
    assert index.measure()['F_global_conservative_review_groups']==100000
