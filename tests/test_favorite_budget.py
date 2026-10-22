from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib,sqlite3,json
from types import SimpleNamespace
import pytest
from amazon_es_bestseller.multisource.storage import digest,immutable_json,sealed
from amazon_es_bestseller.highspeed.favorite_budget import KEY,validate_amendment,effective_budgets,page_limit
from amazon_es_bestseller.highspeed.three_contract import family_budget_for_store
from amazon_es_bestseller.highspeed.page_navigation import extend_source

def reference(p):return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

def attach_amendment(tmp_path,plan,auth,now):
    original=tmp_path/'original_auth.json';immutable_json(original,auth)
    caps=deepcopy(plan['source_budgets']);caps['CUSTOMER_FAVORITES']={'requests':400,'PAGE':400,'native':0}
    a={'kind':'FAVORITE_PAGE_BUDGET_AMENDMENT_V1','approved':True,'approved_by':'TEST_ONLY',
       'authorization_text':'TEST_ONLY_EXPLICIT_BUDGET_INCREASE','run_id':plan['run_id'],'plan_sha256':digest(plan),
       'original_authorization_reference':reference(original),'original_source_budgets':deepcopy(plan['source_budgets']),
       'effective_source_budgets':caps,'max_favorite_pages_per_source':400,'preserve_counters_and_task_clock':True,
       'allowed_root_source_ids':[s['source_id'] for s in plan['sources'] if s['source_type']=='CUSTOMER_FAVORITES'],
       'store_root':auth['store_root'],'expires_at':auth['expires_at'],'reviewed_at':now.isoformat()}
    path=tmp_path/'amendment.json';immutable_json(path,a)
    auth[KEY]=reference(path)
    return a,path

def scope(tmp_path):
    now=datetime.now(timezone.utc)
    plan={'run_id':'TEST_ONLY','collection_contract':'CURRENT_THREE_SOURCE_PAGE_SNAPSHOT_V1',
          'source_budgets':{'BEST_SELLERS':{'requests':600,'PAGE':600,'native':0},
           'NEW_RELEASES':{'requests':450,'PAGE':450,'native':0},'CUSTOMER_FAVORITES':{'requests':150,'PAGE':150,'native':0}},
          'max_requests_per_run':1200,'max_pages_per_run':1200,'sources':[{'source_id':'fav','source_type':'CUSTOMER_FAVORITES'}]}
    auth={'run_id':plan['run_id'],'plan_sha256':digest(plan),'store_root':str(tmp_path/'store'),
          'expires_at':(now+timedelta(days=1)).isoformat(),'access_gate_state':'NORMAL'}
    return now,plan,auth

def test_amendment_keeps_original_plan_global_cap_and_main_budgets(tmp_path):
    now,p,a=scope(tmp_path);old=deepcopy(p);attach_amendment(tmp_path,p,a,now)
    caps=effective_budgets(p,a,now=now)
    assert caps['CUSTOMER_FAVORITES']['PAGE']==400 and p==old
    assert caps['BEST_SELLERS']==p['source_budgets']['BEST_SELLERS']
    changed=deepcopy(a);changed['access_gate_state']='DISABLED'
    with pytest.raises(ValueError):validate_amendment(p,changed,now=now)
    with pytest.raises(ValueError):validate_amendment(p,a,now=now+timedelta(days=2))

def test_cumulative_family_count_is_not_reset_by_amendment(tmp_path):
    now,p,a=scope(tmp_path);attach_amendment(tmp_path,p,a,now)
    db=sqlite3.connect(':memory:');db.execute('create table requests(run_id text,kind text,data_json text)')
    db.executemany('insert into requests values(?,?,?)',[(p['run_id'],'PAGE',json.dumps({'source_type':'CUSTOMER_FAVORITES'}))]*170)
    store=SimpleNamespace(db=db);source={'source_type':'CUSTOMER_FAVORITES'}
    assert family_budget_for_store(store,p,source,'PAGE') is not None
    assert family_budget_for_store(store,p,source,'PAGE',a,now=now) is None
    db.executemany('insert into requests values(?,?,?)',[(p['run_id'],'PAGE',json.dumps({'source_type':'CUSTOMER_FAVORITES'}))]*230)
    assert family_budget_for_store(store,p,source,'PAGE',a,now=now) is not None
    db.close()

def test_same_run_next_can_pass_160_but_stops_at_400(tmp_path):
    now,p,a=scope(tmp_path);attach_amendment(tmp_path,p,a,now)
    parent={'source_id':'fav','source_type':'CUSTOMER_FAVORITES','page_urls':[str(i) for i in range(160)],
            'collection_category_key':'Hogar','collection_category_evidence':{'TEST_ONLY':True}}
    receipt=tmp_path/'receipt.json';immutable_json(receipt,sealed({'run_id':p['run_id']}))
    nav={'relationship':'NEXT_PAGE','url':'TEST_ONLY_NEXT'}
    assert extend_source(parent,nav,reference(receipt),parent) is None
    child=extend_source(parent,nav,reference(receipt),parent,favorite_budget_reference=a[KEY])
    assert len(child['page_urls'])==161 and page_limit(child,p['run_id'],digest(p))==400
    child['page_urls']=list(map(str,range(400)))
    assert extend_source(child,nav,reference(receipt),parent) is None
    with pytest.raises(ValueError):page_limit(child,'OTHER_RUN')
    child['source_type']='BEST_SELLERS'
    with pytest.raises(ValueError):page_limit(child,p['run_id'])

@pytest.mark.parametrize('mutation',[lambda a:a.update(max_favorite_pages_per_source=100000),
 lambda a:a['effective_source_budgets']['BEST_SELLERS'].update(PAGE=9999),
 lambda a:a['effective_source_budgets']['CUSTOMER_FAVORITES'].update(native=1),
 lambda a:a.update(preserve_counters_and_task_clock=False),lambda a:a.update(run_id='OTHER')])
def test_invalid_amendment_cannot_expand_scope(tmp_path,mutation):
    now,p,auth=scope(tmp_path);a,path=attach_amendment(tmp_path,p,auth,now);mutation(a)
    altered=tmp_path/'altered.json';immutable_json(altered,a);auth[KEY]=reference(altered)
    with pytest.raises(ValueError):validate_amendment(p,auth,now=now)
