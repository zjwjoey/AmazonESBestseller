"""Offline freeze of an exact global 30k plan; creates no LIVE approval."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,uuid
from ..multisource.storage import digest,immutable_json,immutable_bytes
from .global_target import POLICY
from .three_contract import validate_three_plan
from .three_navigation import FAMILIES
from .v4_authorization import validate_plan_v4,prerequisite_review_session,_verify_prerequisite
from .three_archive import historical_context
from .budgets import utc_text

FAMILY_LIMITS={'BEST_SELLERS':{'requests':4528,'PAGE':928,'native':3600},
               'NEW_RELEASES':{'requests':1445,'PAGE':245,'native':1200},
               'CUSTOMER_FAVORITES':{'requests':27,'PAGE':27,'native':0}}


def read_reference(ref):
    p=Path(ref['path']);body=p.read_bytes()
    if hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('GLOBAL_INPUT_SHA_MISMATCH')
    return json.loads(body)


def build_global_scope(base,prior_auth,proposal,*,run_id,created_at,task_reference):
    """Pure exact-scope construction. Caller must validate real DOM and priors."""
    urls=[u for s in proposal['sources'] for u in s['page_urls']]
    if (proposal.get('kind')!='V4_GLOBAL_30000_TEN_HOUR_SCOPE_PROPOSAL'
            or proposal.get('target_global_conservative_review_groups')!=30000
            or len(proposal['sources'])!=897 or len(urls)!=1102 or len(set(urls))!=1102
            or urls!=proposal.get('exact_page_URLs')
            or proposal.get('suggested_family_limits')!=FAMILY_LIMITS
            or {k:sum(s['source_type']==k for s in proposal['sources']) for k in FAMILIES}
                !={'BEST_SELLERS':677,'NEW_RELEASES':200,'CUSTOMER_FAVORITES':20}):
        raise ValueError('GLOBAL_EXACT_REVIEWED_897_SOURCE_1102_URL_SCOPE_REQUIRED')
    p=deepcopy(base)
    p.update(run_id=run_id,snapshot_id=run_id,campaign_id='three_source_5001_global_'+uuid.uuid4().hex[:12],
        campaign_created_at=created_at,task_reference=task_reference,
        sources=deepcopy(proposal['sources']),target_policy=deepcopy(POLICY),target_unique_links=30000,
        source_budgets=deepcopy(FAMILY_LIMITS),max_requests_per_run=6000,max_requests_per_day=6000,
        max_pages_per_run=1200,max_duration_hours=8.65,max_native_requests_per_source=20,
        max_raw_observations_per_run=200000,history_observation_limit=1000000,
        live_enabled=True,request_pacing_seconds=3,parallelism=1,category_cooldown_seconds=0)
    # Preserve transport contract/source diagnostics and all safety flags.
    for key in ('seed_reference','parent_run_id','continuation_mode'):p.pop(key,None)
    # Serial interleaving gives all three mandatory families early evidence.
    # Literal source/page sets and each source's own page order are unchanged.
    queues={k:[s for s in p['sources'] if s['source_type']==k] for k in FAMILIES}
    p['sources']=[]
    while any(queues.values()):
        for kind in FAMILIES:
            if queues[kind]:p['sources'].append(queues[kind].pop(0))
    a=deepcopy(prior_auth)
    a.update(approved=False,approved_by=None,approval_id=None,reviewed_at=None,expires_at=None,
             sources={},run_id=run_id,plan_sha256=digest(p))
    for k in ('target_unique_links','source_targets','source_budgets','target_policy','campaign_id',
              'campaign_created_at','task_reference','max_requests_per_run','max_requests_per_day',
              'max_pages_per_run','max_duration_hours','max_native_requests_per_source',
              'max_raw_observations_per_run','history_observation_limit'):
        if k in p:a[k]=deepcopy(p[k])
    return p,a


def prepare_global_30000(proposal_ref,base_plan_ref,base_auth_ref,output,runtime_root,*,task_reference,qa_benchmark_reference,progress=None):
    def mark(phase):
        if progress:progress(phase)
    proposal=read_reference(proposal_ref);base=read_reference(base_plan_ref);old=read_reference(base_auth_ref)
    if old['plan_sha256']!=digest(base):raise ValueError('GLOBAL_BASE_PLAN_AUTH_MISMATCH')
    run_id='global30000_'+uuid.uuid4().hex[:12]
    directory=Path(output)/run_id;directory.mkdir(parents=True,exist_ok=False)
    original_task=Path(task_reference['path']);task_bytes=original_task.read_bytes()
    if hashlib.sha256(task_bytes).hexdigest()!=task_reference['sha256']:raise ValueError('GLOBAL_EXACT_CURRENT_TASK_REQUIRED')
    immutable_bytes(directory/'current_task.txt',task_bytes)
    benchmark=read_reference(qa_benchmark_reference)
    raw=benchmark['report']['metrics']['A_raw_observations']
    if not benchmark.get('central_DOM_passed') or not benchmark.get('independent_CSV_JSONL_passed') or raw<=0 or benchmark['seconds']<=0:
        raise ValueError('REAL_FULL_REVIEW_BENCHMARK_REQUIRED')
    task=directory/'expansion_scope.txt'
    immutable_bytes(task,('Prepare finite THREE SOURCE task: BEST>=5001, NEW>=5001, FAVORITES>=5001, GLOBAL>=30000.\n'
        'Execution requires the separately recorded current task approval and NORMAL gates. Exact proposal SHA: '+proposal_ref['sha256']+'\n'
        '897 contexts / 1102 exact PAGE URLs; 6000 requests / 1200 PAGE / 4800 native; serial >=3s.\n'
        'Fresh LIVE observations only; independent buyable SKU identity NOT_VERIFIED.\n'
        'One immutable10h wall-clock includes preflight, stops, repairs, and final QA. Reserve QA from measured costs; never reset.\n').encode())
    task_ref={'path':str((directory/'current_task.txt').resolve()),'sha256':hashlib.sha256(task_bytes).hexdigest()}
    p,a=build_global_scope(base,old,proposal,run_id=run_id,created_at=utc_text(),task_reference=task_ref)
    a['store_root']=str((Path(runtime_root)/run_id/'store').resolve())
    p['task_clock_path']=str((Path(runtime_root)/run_id/'task_clock.json').resolve())
    p['qa_reserve_policy']={'seconds_per_raw':benchmark['seconds']/raw,'safety_factor':1.2,
        'fixed_seconds':900,'minimum_seconds':3600,'benchmark_reference':qa_benchmark_reference}
    a['plan_sha256']=digest(p)
    a['task_clock_reference']=None
    with prerequisite_review_session():
        mark('GLOBAL_FULL_SOURCE_AND_PRIOR_OFFLINE_REVIEW_STARTED')
        validate_plan_v4(p)
        for stage in ('T0','T1','T2'):_verify_prerequisite(a['prerequisites'][stage],stage)
        for s in p['sources']:
            sid=s['source_id']
            if sid in old['sources']:
                scope=deepcopy(old['sources'][sid])
                if scope['page_urls']!=s['page_urls']:raise ValueError('GLOBAL_PRIOR_SCOPE_CHANGED')
            else:
                _,page=historical_context(s['historical_capture_reference'],expected_url=s['actual_source_url'],expected_type=s['source_type'])
                scope={k:deepcopy(s[k]) for k in ('source_type','source_subtype','page_urls','parser_profile')}
                scope.update(allow_native_acp=True,allow_held_native_carousel=True,allow_native_dynamic=False,
                    evidence={'path':page['evidence_path'],'sha256':page['evidence_sha256'],'reviewed':True})
            e=scope['evidence']
            if hashlib.sha256(Path(e['path']).read_bytes()).hexdigest()!=e['sha256']:raise ValueError('GLOBAL_SCOPE_EVIDENCE_CHANGED')
            a['sources'][sid]=scope
    validate_three_plan(p)
    mark('GLOBAL_FINITE_PLAN_FROZEN_PENDING_HUMAN_APPROVAL')
    immutable_json(directory/'plan.pending.json',p);immutable_json(directory/'authorization.pending.json',a)
    immutable_bytes(directory/'exact_URLs.txt',('\n'.join(u for s in p['sources'] for u in s['page_urls'])+'\n').encode())
    summary={'run_id':run_id,'plan_sha256':digest(p),'source_count':897,'exact_URL_count':1102,
        'request_cap':6000,'PAGE_cap':1200,'native_cap':4800,'capture_hours_cap':8.65,
        'total_ten_hour_delivery':'UNPROVEN','approved':False,'live_started':False,
        'target_policy':POLICY,'execution_engine_ready':True,
        'launch_requires':['CURRENT_USER_TASK_CONDITIONS_SATISFIED','CURRENT_COMMIT_CI_SUCCESS',
                           'DATA_CORRECTNESS_AND_NATIVE_PROTOCOL_REVIEW','FRESH_GATES_BUDGET_AUTH_CHECK'],
        'input_references':[proposal_ref,base_plan_ref,base_auth_ref,task_reference,qa_benchmark_reference],
        'joint_quantity_goals':{'BEST_SELLERS':5001,'NEW_RELEASES':5001,'CUSTOMER_FAVORITES':5001,'GLOBAL':30000},
        'qa_reserve_policy':p['qa_reserve_policy'],
        'user_conditional_execution_authorization_recorded':True}
    immutable_json(directory/'summary.json',summary)
    immutable_json(directory/'manifest.json',{'summary':summary,'files':[{'name':f.name,
        'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in sorted(directory.iterdir()) if f.is_file()]})
    return {'directory':str(directory),**summary}
