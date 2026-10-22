"""Offline-only, DOM-evidenced T2 proposal. Never creates approval."""
from copy import deepcopy
import hashlib,json,uuid
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from ..multisource.storage import immutable_json,digest
from .v4_quality import verify_stage_reference
from .v4_sources import source_for_url,RANKED
from .v4_navigation import verify_discovered_source,verify_pagination_reference
from .v4_authorization import validate_plan_v4


def prepare_t2(t0_reference,t1_reference,output,runtime_root,shared_gate):
    verify_stage_reference(t0_reference,'T0')
    t1=verify_stage_reference(t1_reference,'T1')
    sources=[];scopes={};excluded=[]
    for old in t1['sources']:
        page=next(p for p in t1['pages'] if p['source_id']==old['source_id'] and p['page_url']==old['actual_source_url'])
        if page['status']=='EXPLICIT_EMPTY':
            excluded.append({'url':page['page_url'],'status':page['status'],'evidence_path':page['evidence_path'],
                             'sha256':page['evidence_sha256']});continue
        s=deepcopy(old);s['page_urls']=[s['actual_source_url']];s.pop('pagination_evidence',None)
        if s['source_subtype']=='CUSTOMER_FAVORITES_FILTERED_SEARCH':
            soup=BeautifulSoup(Path(page['evidence_path']).read_bytes(),'lxml')
            anchors=soup.select('a.s-pagination-next[href]')
            if not anchors:raise ValueError('T2_ACTUAL_FAVORITES_NEXT_REQUIRED')
            a=anchors[0];ref={'page_url':s['actual_source_url'],'url':urljoin(s['actual_source_url'],a['href']),
                'raw_href':a['href'],'label_raw':a.get_text(' ',strip=True),
                'evidence_path':page['evidence_path'],'evidence_sha256':page['evidence_sha256']}
            verify_pagination_reference(ref,s);s['pagination_evidence']=[ref];s['page_urls'].append(ref['url'])
        sources.append(s)
        if s['source_type'] in RANKED:
            soup=BeautifulSoup(Path(page['evidence_path']).read_bytes(),'lxml');chosen=set()
            prefix='/gp/bestsellers/' if s['source_type']=='BEST_SELLERS' else '/gp/new-releases/'
            # Sample categories whose root carousel was actually observed in
            # T1, rather than arbitrary sidebar destinations or root aliases.
            for a in soup.select("[data-card-metrics-id^='p13n-zg-list-carousel'] a[href]"):
                url=urljoin(page['page_url'],a['href']);path=urlsplit(url).path
                category=path.removeprefix(prefix).split('/')[0]
                if (not path.startswith(prefix) or not category or category.startswith('ref=')
                        or category in chosen or not a.get_text(strip=True)):continue
                evidence={'page_url':page['page_url'],'raw_href':a['href'],'label_raw':a.get_text(' ',strip=True),
                    'evidence_path':page['evidence_path'],'evidence_sha256':page['evidence_sha256']}
                deep=source_for_url(url,navigation_evidence=evidence,source_type=s['source_type'],source_subtype=s['source_subtype'])
                deep.update(enabled=True,page_urls=[url]);verify_discovered_source(deep)
                sources.append(deep);chosen.add(category)
                if len(chosen)==2:break
            if len(chosen)!=2:raise ValueError('T2_TWO_REAL_CATEGORY_HREFS_REQUIRED')
    run_id='v4_t2_'+uuid.uuid4().hex[:12]
    # Read sealed plan rather than importing wrapper fields into a new plan.
    from ..multisource.storage import read_sealed
    plan=read_sealed(Path(t1['pages'][0]['capture_receipt_reference']['path']).parent.parent/'plan.json')
    plan.update(stage='T2',run_id=run_id,snapshot_id=run_id,live_enabled=False,sources=sources,
        max_requests_per_run=200,max_requests_per_day=200,max_pages_per_run=20,max_duration_hours=1,
        max_raw_observations_per_run=20000,history_observation_limit=40000,target_unique_links=2000,
        max_native_requests_per_source=20,max_dynamic_requests_per_source=20,
        shared_access_gate_path=str(Path(shared_gate).resolve()))
    validate_plan_v4(plan)
    for s in sources:
        parent=next(p for p in t1['pages'] if p['page_url']==(s.get('navigation_evidence') or {}).get('page_url',s['actual_source_url']))
        scopes[s['source_id']]={k:s[k] for k in ('source_type','source_subtype','page_urls','parser_profile')}
        scopes[s['source_id']].update(allow_native_acp=s['source_type'] in RANKED,
            allow_held_native_carousel=s['source_type'] in RANKED,
            allow_native_dynamic=s['source_type'] in {'DEALS','AMAZON_HAUL'},
            evidence={'path':parent['evidence_path'],'sha256':parent['evidence_sha256'],'reviewed':True})
    authorization={'approved':False,'stage':'T2','snapshot_contract_version':4,'run_id':run_id,
        'plan_sha256':digest(plan),'store_root':str((Path(runtime_root)/run_id/'store').resolve()),
        'shared_access_gate_path':plan['shared_access_gate_path'],'sources':scopes,
        'prerequisites':{'T0':t0_reference,'T1':t1_reference},'approved_by':None,'approval_id':None,
        'reviewed_at':None,'expires_at':None,'note':'Pending exact T2 approval. No T3 and no historical seed.'}
    for k in ('max_requests_per_run','max_requests_per_day','max_pages_per_run','max_duration_hours',
              'max_raw_observations_per_run','history_observation_limit','target_unique_links',
              'request_pacing_seconds','parallelism','category_cooldown_seconds'):authorization[k]=plan[k]
    directory=Path(output)/('T2准备_'+run_id);directory.mkdir(parents=True,exist_ok=False)
    immutable_json(directory/'plan.pending.json',plan)
    immutable_json(directory/'authorization.pending.json',authorization)
    immutable_json(directory/'真实空源保留.json',excluded)
    return {'directory':str(directory),'run_id':run_id,'source_count':len(sources),
        'exact_PAGE_URLs':[u for s in sources for u in s['page_urls']],
        'max_requests':200,'max_PAGE':20,'max_hours':1,'live_started':False,'T3_enabled':False}
