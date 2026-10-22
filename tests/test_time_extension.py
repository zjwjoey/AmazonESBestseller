from pathlib import Path
from datetime import timedelta
from copy import deepcopy
import hashlib,json
import pytest
from test_highspeed_v4_execution import NOW
from amazon_es_bestseller.multisource.storage import immutable_json,digest
from amazon_es_bestseller.highspeed.task_clock import start_task_clock,validate_task_clock
from amazon_es_bestseller.highspeed.budgets import budget_reason

def setup(tmp):
 p={'run_id':'TEST_ONLY','target_policy':{'test':True},'task_clock_path':str((tmp/'clock.json').resolve()),'max_duration_hours':8.65,'max_requests_per_run':6000,'max_requests_per_day':6000,'max_pages_per_run':1200,'max_raw_observations_per_run':150000,'history_observation_limit':1000000}
 ref=start_task_clock(p['task_clock_path'],p,'TEST',now=NOW);at=NOW+timedelta(hours=11);end=at+timedelta(hours=12)
 a={'run_id':p['run_id'],'plan_sha256':digest(p),'store_root':str(tmp/'store'),'task_clock_reference':ref,'expires_at':end.isoformat()}
 e={'kind':'EXPLICIT_SAME_RUN_TIME_EXTENSION','approved':True,'approved_by':'TEST_USER','approval_id':'TEST','run_id':p['run_id'],'plan_sha256':digest(p),'store_root':a['store_root'],'original_clock_reference':ref,'reviewed_at':at.isoformat(),'request_deadline':(at+timedelta(hours=8)).isoformat(),'deadline':end.isoformat(),'preserve_cumulative_budgets':True,'preserve_started_at':True}
 path=tmp/'extension.json';immutable_json(path,e);a['time_extension_reference']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};return p,a,e,at

def test_extension_keeps_original_clock_and_cumulative_caps(tmp_path):
 p,a,e,at=setup(tmp_path);before=Path(p['task_clock_path']).read_bytes();c=validate_task_clock(p,a,now=at)
 assert c['original_deadline']!=c['deadline'];assert Path(p['task_clock_path']).read_bytes()==before
 assert budget_reason(p,{'requests':4},started_at=NOW,now=at,authorization=a) is None
 assert budget_reason(p,{'requests':6000},started_at=NOW,now=at,authorization=a)=='RUN_REQUEST_BUDGET_EXHAUSTED'
 assert budget_reason(p,{'requests':4},started_at=NOW,now=at+timedelta(hours=8),authorization=a)=='MAX_DURATION_REACHED'

def test_no_extension_cannot_resume_old_clock(tmp_path):
 p,a,e,at=setup(tmp_path);a.pop('time_extension_reference')
 with pytest.raises(ValueError,match='DEADLINE'):validate_task_clock(p,a,now=at)
 assert budget_reason(p,{},started_at=NOW,now=at)=='MAX_DURATION_REACHED'

@pytest.mark.parametrize('field,value',[('approved',False),('run_id','OTHER'),('preserve_cumulative_budgets',False),('preserve_started_at',False)])
def test_invalid_extension_fails_closed(tmp_path,field,value):
 p,a,e,at=setup(tmp_path);e[field]=value;path=tmp_path/'bad.json';immutable_json(path,e);a['time_extension_reference']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 with pytest.raises(ValueError):validate_task_clock(p,a,now=at)

def test_expired_or_modified_extension_cannot_extend_again(tmp_path):
 p,a,e,at=setup(tmp_path)
 with pytest.raises(ValueError,match='DEADLINE'):validate_task_clock(p,a,now=at+timedelta(hours=12))
 a['time_extension_reference']['sha256']='0'*64
 with pytest.raises(ValueError,match='SHA'):validate_task_clock(p,a,now=at)

def test_different_plan_never_reuses_extension(tmp_path):
 p,a,e,at=setup(tmp_path);p['max_requests_per_run']=6001
 with pytest.raises(ValueError):validate_task_clock(p,a,now=at)
