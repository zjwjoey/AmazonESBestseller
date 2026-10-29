from copy import deepcopy
import pytest
from test_highspeed_v4_execution import setup_scope,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.parallel_admission import ParallelAdmission


def test_charged_last_request_is_allowed_but_other_claims_remain_counted(tmp_path):
    plan,auth=setup_scope(tmp_path);source=plan['sources'][0]
    plan.update(max_requests_per_run=2,max_requests_per_day=2,max_pages_per_run=2,
        source_budgets={source['source_type']:{'requests':2,'PAGE':2,'native':0}})
    store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat())
        admission=ParallelAdmission(V4T1Runner(store),plan,auth,clock=lambda:NOW)
        # Isolate charged-budget arithmetic; common authorization/clock checks
        # have separate real-plan tests and are not claimed by this test.
        admission._common=lambda source,url,**kwargs:NOW
        items=[]
        for i in range(2):
            url=source['actual_source_url']+f'?pg={i+1}'
            r=store.reserve_request(plan['run_id'],{'kind':'PAGE','url':url,'source_id':source['source_id'],
                'source_type':source['source_type'],'observed_at':NOW.isoformat(),'limits':plan})
            items.append({'url':url,'source':source,'reservation':r})
        assert admission(1,items[0]) and admission(2,items[1])
        with pytest.raises(ValueError,match='BUDGET'):
            admission.before_reserve(source,source['actual_source_url'])
        bad=deepcopy(items[1]);bad['url']=items[0]['url']
        with pytest.raises(ValueError,match='EXACT_UNSENT'):
            admission(2,bad)
    finally:store.close()
