"""Freeze a finite three-source proposal from current and archived DOM proofs."""
from copy import deepcopy
import hashlib,json,uuid
from pathlib import Path
from .three_navigation import CONTRACT,FAMILIES,FAVORITES_ROOT,discover_navigation,build_navigation_tree,category_key,verify_chain
from .three_contract import source_from_navigation
from .three_archive import historical_context
from .v4_authorization import _verify_prerequisite as verify_stage_reference
from .v4_sources import SEEDS,source_for_url,RANKED
from .v4_authorization import validate_plan_v4
from .v4_supplement import _read
from .authorization import STOP_CONDITIONS
from .budgets import LIMIT_FIELDS,utc_text
from ..multisource.storage import immutable_json,digest


def prepare_three(t2_reference,archive_reference,selection_reference,task_reference,output,runtime_root,shared_gate,*,campaign_reference=None):
    # Task text is source material, not code; bind its exact bytes only.
    task=Path(task_reference['path'])
    if hashlib.sha256(task.read_bytes()).hexdigest()!=task_reference['sha256']:
        raise ValueError('THREE_TASK_SHA_MISMATCH')
    selection=_read(selection_reference)
    if selection.get('kind')!='THREE_SOURCE_5001_SELECTION' or not selection.get('reason'):
        raise ValueError('THREE_EXPLICIT_SELECTION_REQUIRED')
    for key in ('seed_urls','prior_source_ids','historical_source_urls','navigation_source_urls','pagination_urls'):
        if (not isinstance(selection.get(key),list) or any(not isinstance(u,str) for u in selection[key])
                or len(set(selection[key]))!=len(selection[key])):
            raise ValueError('THREE_EXACT_UNIQUE_SELECTION_REQUIRED:'+key)
    stage=verify_stage_reference(t2_reference,'T2')
    approvals=[_read(r) for r in stage['capacity_execution']['authorization_references']]
    if not approvals or any(a['prerequisites']!=approvals[0]['prerequisites'] for a in approvals):
        raise ValueError('THREE_PRIOR_APPROVAL_LINEAGE_REQUIRED')
    priors=approvals[0]['prerequisites'];verify_stage_reference(priors['T0'],'T0');verify_stage_reference(priors['T1'],'T1')
    archive=_read(archive_reference)
    if (archive.get('kind')!='HISTORICAL_NAVIGATION_ONLY' or archive.get('contract')!=CONTRACT
            or archive.get('approved') is not False or archive.get('new_network_requests')!=0
            or archive.get('historical_products_added_to_target')!=0):
        raise ValueError('THREE_ARCHIVE_NAVIGATION_ONLY_REQUIRED')
    source_map={s['source_id']:s for s in stage['sources'] if s['source_type'] in FAMILIES}
    contexts=[]
    for p in stage['pages']:
        if p['source_id'] in source_map:contexts.append((source_map[p['source_id']],p))
    archived={}
    for c in archive['captures']:
        s=c['source'];archived[s['actual_source_url']]=c
        contexts.append((s,c['page']))
    pending={}
    for s,p in contexts:
        for ref in discover_navigation(p,s):
            if ref['status']=='PENDING_REVIEW':pending.setdefault(ref['url'],[]).append((ref,s,p))
    allowed_seeds={s['url'] for s in SEEDS if s['source_type'] in FAMILIES}|{FAVORITES_ROOT}
    selected=[];scope_evidence={};review=[]
    for url in selection['seed_urls']:
        if url not in allowed_seeds:raise ValueError('THREE_TASK_ROOT_OR_KNOWN_ENTRY_REQUIRED')
        s=source_for_url(url);s.update(page_urls=[url],enabled=True);selected.append(s)
        # The referral-free task root is explicit but has not been captured at
        # that exact URL. Its task bytes are evidence of entry authorization,
        # never evidence of current products or HTTP status.
        p=next((p for _,p in contexts if p['page_url']==url),None)
        scope_evidence[s['source_id']]={'path':p['evidence_path'],'sha256':p['evidence_sha256']} if p else task_reference
        review.append({'url':url,'kind':'EXPLICIT_ENTRY','supply':'PREVIOUSLY_OBSERVED' if p else 'UNVERIFIED_AT_EXACT_URL'})
    for sid in selection['prior_source_ids']:
        if sid not in source_map:raise ValueError('THREE_PRIOR_SOURCE_NOT_IN_FORMAL_T2')
        s=deepcopy(source_map[sid]);s.update(enabled=True,page_urls=[s['actual_source_url']]);s.pop('pagination_evidence',None)
        p=next(p for _,p in contexts if p['source_id']==sid and p['page_url']==s['actual_source_url'])
        if p['status']!='VERIFIED_WITH_PRODUCTS':raise ValueError('THREE_PRIOR_SOURCE_PRODUCTS_UNVERIFIED')
        selected.append(s);scope_evidence[sid]={'path':p['evidence_path'],'sha256':p['evidence_sha256']}
        review.append({'url':s['actual_source_url'],'kind':'FORMAL_T2_CONTEXT_NOT_NEW_DATA','supply':'HISTORICAL_ONLY'})
    for url in selection['historical_source_urls']:
        if url not in archived:raise ValueError('THREE_HISTORICAL_EXACT_URL_NOT_IN_INVENTORY')
        stored=archived[url];s,p=historical_context(stored['source']['historical_capture_reference'],expected_url=url,
            expected_type=stored['source']['source_type'])
        s['enabled']=True;selected.append(s)
        scope_evidence[s['source_id']]={'path':p['evidence_path'],'sha256':p['evidence_sha256']}
        review.append({'url':url,'kind':'ACTUAL_HISTORICAL_PAGE_RECAPTURE_PROPOSAL',
            'receipt':s['historical_capture_reference'],'supply':'HISTORICAL_ONLY_NOT_CURRENT_CAPACITY'})
    for url in selection['navigation_source_urls']:
        choices=[x for x in pending.get(url,[]) if x[0]['relationship']!='NEXT_PAGE']
        if not choices:raise ValueError('THREE_LITERAL_OWNED_PENDING_NAVIGATION_REQUIRED:'+url)
        ref,parent,page=choices[0]
        # Recheck selected historical parents against the actual receipt/body.
        if parent.get('historical_capture_reference'):
            checked,p=historical_context(parent['historical_capture_reference'],expected_url=parent['actual_source_url'])
            if ref not in discover_navigation(p,checked):raise ValueError('THREE_ARCHIVE_NAVIGATION_RECOMPUTATION_FAILED')
        s=source_from_navigation(ref,parent);selected.append(s)
        scope_evidence[s['source_id']]={'path':page['evidence_path'],'sha256':page['evidence_sha256']}
        review.append({'url':url,'kind':ref['relationship'],'provenance':ref,'supply':'UNVERIFIED'})
    for url in selection['pagination_urls']:
        choices=[x for x in pending.get(url,[]) if x[0]['relationship']=='NEXT_PAGE']
        found=None
        for ref,parent,page in choices:
            found=next((s for s in selected if s['source_id']==parent['source_id'] and s['page_urls'][-1]==ref['page_url']),None)
            if found:break
        if found is None:raise ValueError('THREE_NEXT_MUST_EXTEND_SELECTED_EXACT_CHAIN:'+url)
        found.setdefault('pagination_evidence',[]).append(ref);found['page_urls'].append(url);verify_chain(found)
        review.append({'url':url,'kind':'ORDERED_NEXT_PAGE','provenance':ref,'supply':'UNVERIFIED'})
    ranked=[category_key(s['actual_source_url']) for s in selected if s['source_type'] in RANKED]
    if len(set(ranked))!=len(ranked):raise ValueError('THREE_DUPLICATE_RANK_CATEGORY_REVIEW_ALIAS')
    selected.sort(key=lambda s:FAMILIES.index(s['source_type']))
    run_id='three_source_batch_'+uuid.uuid4().hex[:12]
    if campaign_reference:
        previous=_read(campaign_reference)
        validate_plan_v4(previous)
        if previous['collection_contract']!=CONTRACT or previous['task_reference']!=task_reference:
            raise ValueError('THREE_SAME_TASK_CAMPAIGN_REQUIRED')
        campaign_id=previous['campaign_id'];created=previous['campaign_created_at']
    else:campaign_id='three_source_5001_'+uuid.uuid4().hex[:12];created=utc_text()
    budgets=selection['source_budgets'];plan=deepcopy(stage['capacity_execution']['plan'])
    allowed_options={'max_duration_hours','max_native_requests_per_source','max_raw_observations_per_run','history_observation_limit'}
    if set(selection['limits'])!=allowed_options:raise ValueError('THREE_EXACT_LIMIT_FIELDS_REQUIRED')
    plan.update(selection['limits']);plan.update(stage='T3',collection_contract=CONTRACT,run_id=run_id,snapshot_id=run_id,
        campaign_id=campaign_id,campaign_created_at=created,task_reference=task_reference,
        source_targets={k:5001 for k in FAMILIES},target_unique_links=15003,sources=selected,live_enabled=False,
        source_budgets=budgets,max_requests_per_run=sum(b['requests'] for b in budgets.values()),
        max_requests_per_day=sum(b['requests'] for b in budgets.values()),max_pages_per_run=sum(b['PAGE'] for b in budgets.values()),
        request_pacing_seconds=3,parallelism=1,category_cooldown_seconds=0,shared_access_gate_path=str(Path(shared_gate).resolve()))
    validate_plan_v4(plan)
    scopes={}
    for s in selected:
        scopes[s['source_id']]={k:s[k] for k in ('source_type','source_subtype','page_urls','parser_profile')}
        scopes[s['source_id']].update(allow_native_acp=s['source_type'] in RANKED,
            allow_held_native_carousel=s['source_type'] in RANKED,allow_native_dynamic=False,
            evidence={**scope_evidence[s['source_id']],'reviewed':True})
    authorization={'approved':False,'stage':'T3','snapshot_contract_version':4,'run_id':run_id,
        'plan_sha256':digest(plan),'store_root':str((Path(runtime_root)/run_id/'store').resolve()),
        'shared_access_gate_path':plan['shared_access_gate_path'],'sources':scopes,
        'prerequisites':{**priors,'T2':t2_reference},'approved_by':None,'approval_id':None,'reviewed_at':None,'expires_at':None,
        'access_gate_state':'NORMAL','stop_conditions':sorted(STOP_CONDITIONS)}
    for k in (*LIMIT_FIELDS,'target_unique_links','max_duration_hours','request_pacing_seconds','parallelism',
              'category_cooldown_seconds','collection_contract','source_targets','source_budgets',
              'campaign_id','campaign_created_at','task_reference'):authorization[k]=plan[k]
    # The complete discovered graph is retained, including unselected URLs and
    # unknown hierarchy. Counts of historical products do not become supply.
    unique_sources={s['source_id']:s for s,p in contexts}
    for s in selected:
        # Existing context scope may include a prior saved page 2. Keep it for
        # historical tree inspection; new execution scope remains in plan only.
        if s['source_id'] not in unique_sources:unique_sources[s['source_id']]=s
    tree=build_navigation_tree([p for s,p in contexts],list(unique_sources.values()))
    urls=[u for s in selected for u in s['page_urls']]
    summary={'run_id':run_id,'campaign_id':campaign_id,'source_count':len(selected),'exact_PAGE_count':len(urls),
        'sources_by_family':{k:sum(s['source_type']==k for s in selected) for k in FAMILIES},
        'PAGE_by_family':{k:sum(len(s['page_urls']) for s in selected if s['source_type']==k) for k in FAMILIES},
        'source_targets':plan['source_targets'],'source_budgets':budgets,'max_duration_hours':plan['max_duration_hours'],
        'pending_plan_sha256':digest(plan),'enabled_plan_sha256_preview':digest({**plan,'live_enabled':True}),
        'live_started':False,'new_network_requests':0,'new_campaign_candidates':0,'historical_candidates_added':0,
        'current_source_capacity':'UNKNOWN','global_tree_complete':False,'formal_T0_T1_T2_reverified':True,
        'acceptance':'NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET'}
    directory=Path(output)/('三来源采集准备_'+run_id);directory.mkdir(parents=True,exist_ok=False)
    for name,value in [('plan.pending.json',plan),('authorization.pending.json',authorization),('summary.json',summary),
        ('Amazon类目导航树_历史证据待审.json',tree),('来源选择与证据.json',{'selection':selection,'selection_reference':selection_reference,
            'task_reference':task_reference,'t2_reference':t2_reference,'archive_reference':archive_reference,
            'campaign_reference':campaign_reference,'reviews':review})]:immutable_json(directory/name,value)
    with (directory/'精确PAGE_URL清单.txt').open('x',encoding='utf8',newline='\n') as fh:fh.write('\n'.join(urls)+'\n')
    immutable_json(directory/'manifest.json',{'contract':CONTRACT,'approved':False,'live_enabled':False,'new_network_requests':0,
        'files':[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(directory.iterdir())]})
    return {'directory':str(directory),**summary}
