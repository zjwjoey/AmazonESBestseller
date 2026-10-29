"""Explicit offline V3 historical replay. Old stores are opened read-only only."""
from __future__ import annotations
from collections import Counter, defaultdict, OrderedDict
from contextlib import contextmanager
import csv
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone
from ..multisource.storage import read_sealed, digest, immutable_json, sealed
from .adapters import parse_page
from .category_mapping import load_mapping_rules, map_category
from .v3_links import normalize_product_url_v3, NORMALIZER_VERSION
from .store import RunStore, ReadOnlyRunStore

@contextmanager
def readonly_store(root):
    store=ReadOnlyRunStore(root)
    try:yield store
    finally:store.close()

def _ref(path):
    path=Path(path).resolve()
    return {'path':str(path),'sha256':digest(path.read_bytes())}

def _read_ref(reference,cache=None):
    path=Path(reference['path'])
    stat=path.stat()
    key=('sealed',str(path.resolve()),reference['sha256'],stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    if cache is not None and key in cache:
        cache.move_to_end(key);return cache[key]
    body=path.read_bytes()
    if digest(body)!=reference['sha256']:raise ValueError('REPLAY_REFERENCE_HASH_MISMATCH')
    value=read_sealed(path)
    if cache is not None:
        cache[key]=value
        while len(cache)>128:cache.popitem(last=False)
    return value

def _body_matches(path,sha,cache=None):
    path=Path(path);stat=path.stat()
    key=('body',str(path.resolve()),sha,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    if cache is not None and key in cache:
        cache.move_to_end(key);return cache[key]
    valid=digest(path.read_bytes())==sha
    if cache is not None:
        cache[key]=valid
        while len(cache)>128:cache.popitem(last=False)
    return valid

def validate_replay_receipt(row,source,*,cache=None):
    """Independently verify sealed original capture; never infer a LIVE replay."""
    if row.get('origin')!='HISTORICAL_REPLAY' or row.get('is_derivation') is not True:return False
    receipt=_read_ref(row['capture_receipt_reference'],cache)
    if (receipt.get('origin')!='LIVE_CAPTURE' or receipt.get('status_code')!=200
            or receipt.get('access_state')!='NORMAL' or receipt.get('run_id')!=row.get('capture_run_id')
            or receipt.get('source_id')!=source['source_id'] or receipt.get('source_type')!=source['source_type']
            or receipt.get('html_path')!=row.get('evidence_path')
            or receipt.get('sha256')!=row.get('evidence_sha256')
            or receipt.get('observed_at')!=row.get('observed_at')):return False
    if not _body_matches(receipt['html_path'],receipt['sha256'],cache):return False
    representation=row.get('representation','')
    if (representation=='SERVER' and (receipt.get('kind')!='PAGE' or receipt.get('request_url')!=row.get('page_url'))
        or representation.startswith('ACP_') and receipt.get('kind')!='ACP'):return False
    original=_read_ref(row['historical_page_journal_reference'],cache)
    seed_reference=row.get('historical_seed_provenance_reference')
    if seed_reference:
        seed=_read_ref(seed_reference,cache)
        if (seed.get('parent_run_id')!=row.get('capture_run_id')
                or row['historical_page_journal_reference'] not in seed.get('journals',[])
                or row['capture_receipt_reference'] not in seed.get('receipts',[])):
            return False
    if original.get('run_id')!=row.get('historical_journal_run_id'):return False
    page=original['page']
    if page.get('page_url')!=row.get('page_url') or page.get('source_id')!=source['source_id']:return False
    if receipt.get('page_key') not in {None,original.get('page_key')}:return False
    bindings=[old for old in original['observations'] if old.get('evidence_path')==row.get('evidence_path')
        and old.get('representation_slot')==row.get('representation_slot')
        and old.get('raw_product_url')==row.get('raw_product_url')
        and old.get('ranking_position')==row.get('ranking_position')
        and old.get('rank_context')==row.get('rank_context')]
    return bool(bindings) and bool(row.get('historical_observation_ids'))

def validate_live_receipt(row,source,*,cache=None):
    """Require an actual sealed, same-run LIVE representation capture."""
    if row.get('origin')!='LIVE_CAPTURE' or row.get('is_derivation') is True:return False
    receipt=_read_ref(row['capture_receipt_reference'],cache)
    if (receipt.get('origin')!='LIVE_CAPTURE' or receipt.get('run_id')!=row.get('run_id')
            or row.get('capture_run_id')!=row.get('run_id') or receipt.get('status_code')!=200
            or receipt.get('access_state')!='NORMAL' or receipt.get('source_id')!=source['source_id']
            or receipt.get('source_type')!=source['source_type'] or receipt.get('html_path')!=row.get('evidence_path')
            or receipt.get('sha256')!=row.get('evidence_sha256') or receipt.get('observed_at')!=row.get('observed_at')):return False
    if not _body_matches(receipt['html_path'],receipt['sha256'],cache):return False
    representation=row.get('representation','')
    if representation=='SERVER':return receipt.get('kind')=='PAGE' and receipt.get('request_url')==row.get('page_url')
    descriptor=receipt.get('native_acp_descriptor') or {}
    return (representation.startswith('ACP_') and receipt.get('kind')=='ACP'
        and descriptor.get('request_contract_parent_page_url')==row.get('page_url')
        and receipt.get('ranking_context_binding')==descriptor.get('ranking_context_binding'))

def _verified_receipts(values,source):
    result=[]
    for indexed in values:
        reference=_ref(indexed['evidence_path']);actual=_read_ref(reference)
        for key in ('request_id','run_id','kind','source_id','source_type','html_path','sha256','observed_at'):
            if actual.get(key)!=indexed.get(key):raise ValueError('REPLAY_INDEX_RECEIPT_CONFLICT:'+key)
        if actual.get('source_id')!=source['source_id'] or actual.get('source_type')!=source['source_type']:
            raise ValueError('REPLAY_RECEIPT_SOURCE_CONFLICT')
        if actual.get('origin')!='LIVE_CAPTURE' or actual.get('status_code')!=200 or actual.get('access_state')!='NORMAL':
            continue
        if digest(Path(actual['html_path']).read_bytes())!=actual['sha256']:raise ValueError('REPLAY_CAPTURE_BODY_HASH_MISMATCH')
        result.append((actual,reference))
    return result

def replay_v3(historical_root,historical_run_id,out,*,mapping_config,progress=None):
    """Create an exclusive derived store without request reservations or browser."""
    historical_root=Path(historical_root).resolve();out=Path(out).resolve()
    if out==historical_root or historical_root in out.parents:raise ValueError('REPLAY_OUTPUT_MUST_BE_SEPARATE')
    if out.exists():raise ValueError('REPLAY_OUTPUT_ALREADY_EXISTS')
    mapping_reference=_ref(mapping_config)
    rules=load_mapping_rules(mapping_config)
    if _ref(mapping_config)!=mapping_reference:raise ValueError('MAPPING_CONFIG_CHANGED_DURING_LOAD')
    started=time.perf_counter();run_id='v3_replay_'+uuid.uuid4().hex[:16]
    with readonly_store(historical_root) as old:
        old_run=old.get_run(historical_run_id)
        pages=old.pages(historical_run_id)
        indexed=[json.loads(r[0]) for r in old.db.execute('SELECT data_json FROM observations WHERE run_id=?',(historical_run_id,))]
        history=defaultdict(list)
        for row in indexed:
            key=(row.get('source_id'),row.get('evidence_path'),row.get('representation_slot'),row.get('raw_product_url'),row.get('ranking_position'),row.get('rank_context'))
            history[key].append(row['observation_id'])
        sources={s['source_id']:{**s,'parser_profile':'V3',**map_category(s,rules)} for s in old_run['plan']['sources']}
        plan={**old_run['plan'],'run_id':run_id,'snapshot_id':run_id,'stage':'T0','origin':'HISTORICAL_REPLAY',
            'snapshot_contract_version':3,'normalizer_version':NORMALIZER_VERSION,'sources':list(sources.values()),
            'live_enabled':False,'approved':False,'parent_run_id':historical_run_id,
            'replay_input_root':str(historical_root),'replay_original_plan_sha256':digest(old_run['plan']),
            'continuation_mode':None,'replay_is_not_new_capture':True}
        out.mkdir(parents=True,exist_ok=False)
        immutable_json(out/'mapping_rules.snapshot.json',{'rules':list(rules),'original_mapping_config_reference':mapping_reference})
        store=RunStore(out/'store');store.begin_run(plan)
        receipts=list(old.iter_responses(historical_run_id));by_page=defaultdict(list)
        for receipt in receipts:by_page[receipt.get('page_key')].append(receipt)
        counts=Counter();failures=[]
        mapping_cache={}
        try:
            for page_index,page in enumerate(pages):
                source=sources[page['source_id']]
                try:
                    imported_reference=_ref(page['journal_path']);journal=_read_ref(imported_reference)
                    original_journal_reference=imported_reference
                    seed_reference=None
                    if page.get('seed_provenance_path'):
                        from .continuation import validate_seed_provenance
                        seed=validate_seed_provenance(page,old_run['plan'],store=old)
                        seed_reference={'path':page['seed_provenance_path'],'sha256':page['seed_provenance_sha256']}
                        original_journal_reference=_ref(seed['provenance']['latest_journal_path'])
                        journal=_read_ref(original_journal_reference)
                        captures=_verified_receipts(seed['receipts'],source)
                    else:captures=_verified_receipts(by_page[page['page_key']],source)
                    original=journal['page'];raw_rows=journal['observations']
                    counts['original_latest_page_observations']+=len(raw_rows)
                    server=[item for item in captures if item[0]['kind']=='PAGE' and item[0]['html_path']==original['evidence_path']
                            and item[0]['request_url']==original['page_url'] and item[0]['observed_at']==original['observed_at']]
                    if not server:raise ValueError('REPLAY_EXACT_SERVER_RECEIPT_REQUIRED')
                    server_receipt,server_ref=max(server,key=lambda item:(item[0]['observed_at'],item[0]['request_id']))
                    fragments=[];receipt_by_body={server_receipt['html_path']:(server_receipt,server_ref)};seen=set()
                    for raw in raw_rows:
                        if not str(raw.get('representation','')).startswith('ACP_') or raw['evidence_path'] in seen:continue
                        seen.add(raw['evidence_path']);matches=[item for item in captures if item[0]['kind']=='ACP' and item[0]['html_path']==raw['evidence_path']]
                        if not matches:raise ValueError('REPLAY_EXACT_ACP_RECEIPT_REQUIRED')
                        receipt,reference=max(matches,key=lambda item:(item[0]['observed_at'],item[0]['request_id']))
                        receipt_by_body[receipt['html_path']]=(receipt,reference)
                        fragments.append({**receipt,'body':Path(receipt['html_path']).read_text(encoding='utf-8'),'evidence_path':receipt['html_path']})
                    parsed=parse_page(Path(server_receipt['html_path']).read_text(encoding='utf-8'),source=source,run_id=run_id,
                        snapshot_id=run_id,page_url=original['page_url'],page_number=original.get('page_number'),
                        observed_at=server_receipt['observed_at'],evidence_path=server_receipt['html_path'],origin='HISTORICAL_REPLAY',acp_responses=fragments)
                    derived=[]
                    for row in parsed['observations']:
                        receipt,reference=receipt_by_body[row['evidence_path']]
                        logical=(row['source_id'],row['evidence_path'],row['representation_slot'],row['raw_product_url'],row['ranking_position'],row['rank_context'])
                        ids=history.get(logical,[])
                        raw_matches=[r for r in raw_rows if r.get('evidence_path')==row['evidence_path'] and r.get('representation_slot')==row['representation_slot'] and r.get('raw_product_url')==row['raw_product_url']]
                        if not raw_matches or not ids:raise ValueError('REPLAY_ORIGINAL_CARD_BINDING_REQUIRED')
                        if any(r.get('ranking_position')!=row['ranking_position'] or r.get('rank_context')!=row['rank_context'] for r in raw_matches):
                            raise ValueError('REPLAY_ORIGINAL_CARD_BINDING_CONFLICT')
                        original_row=raw_matches[-1]
                        ids=list(dict.fromkeys(ids+[r['observation_id'] for r in raw_matches if r.get('observation_id')]))
                        row.update(normalize_product_url_v3(row['raw_product_url'],base_url=row['page_url']))
                        row.update(origin='HISTORICAL_REPLAY',is_derivation=True,
                            observed_at=receipt['observed_at'],rating_observed_at=receipt['observed_at'],capture_run_id=receipt['run_id'],
                            capture_receipt_reference=reference,historical_page_journal_reference=original_journal_reference,
                            historical_imported_journal_reference=imported_reference,historical_journal_run_id=journal['run_id'],
                            historical_seed_provenance_reference=seed_reference,
                            historical_capture_receipt_references=[ref for value,ref in captures if value['html_path']==row['evidence_path']],
                            capture_selection_rule='LATEST_NORMAL_RECEIPT_PER_EXACT_BODY_SHA_AND_SOURCE_PAGE',
                            historical_observation_ids=ids,historical_observed_at=original_row.get('observed_at'),
                            original_product_title_raw=original_row.get('product_title_raw'),source_completeness_status='UNKNOWN',
                            historical_source_status=original.get('status'),live_requests=0)
                        from .category_mapping import map_observation_category
                        try:row.update(map_observation_category(row,source,rules,parent_html_path=server_receipt['html_path'],
                            parent_html_sha256=server_receipt['sha256'],cache=mapping_cache))
                        except (ValueError,OSError,KeyError,TypeError) as exc:
                            row.update(research_category=None,category_mapping_status='UNKNOWN',category_mapping_reason=str(exc),
                                category_mapping_evidence=[],category_mapping_candidates=[])
                        derived.append(row)
                    value={**parsed['page'],'source':source,'page_key':page['page_key'],'origin':'HISTORICAL_REPLAY','is_derivation':True,
                        'historical_page_journal_reference':original_journal_reference,'historical_page_status':original.get('status')}
                    store.commit_page(run_id,page['page_key'],value,derived)
                    counts['pages']+=1;counts['observations']+=len(derived)
                    if progress:progress({'pages':counts['pages'],'total_pages':len(pages),'observations':counts['observations']})
                except (ValueError,KeyError,OSError,TypeError) as exc:
                    failures.append({'page_key':page['page_key'],'page_url':page.get('page_url'),'reason':str(exc)})
            store.update_run(run_id,status='HISTORICAL_REPLAY_COMPLETED' if not failures else 'HISTORICAL_REPLAY_WITH_FAILURES',
                live_requests=0,replay_wall_seconds=time.perf_counter()-started,
                finished_at=datetime.now(timezone.utc).isoformat(),replay_completed_at=datetime.now(timezone.utc).isoformat())
            manifest={'run_id':run_id,'store_root':str(store.root),'historical_root':str(historical_root),'historical_run_id':historical_run_id,
                'indexed_historical_observations':len(indexed),'latest_historical_pages':len(pages),'counts':dict(counts),'failures':failures,
                'origin':'HISTORICAL_REPLAY','is_derivation':True,'live_requests':0,'request_reservations':len(store.requests(run_id)),
                'mapping_config_reference':mapping_reference,'mapping_snapshot_reference':_ref(out/'mapping_rules.snapshot.json'),
                'stage':'T0_OFFLINE_EVIDENCE_REPLAY_NOT_LIVE_ACCEPTANCE',
                'wall_seconds':time.perf_counter()-started}
            immutable_json(out/'replay_manifest.json',sealed(manifest));return manifest
        finally:store.close()


def _write_records(directory,name,rows):
    jsonl=directory/(name+'.jsonl');csv_path=directory/(name+'.csv')
    keys=list(dict.fromkeys(key for row in rows for key in row))
    with jsonl.open('x',encoding='utf-8') as handle:
        for row in rows:handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    with csv_path.open('x',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=keys);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    return {'jsonl':_ref(jsonl),'csv':_ref(csv_path),'rows':len(rows)}

@contextmanager
def bounded_csv_fields():
    previous=csv.field_size_limit()
    csv.field_size_limit(64*1024*1024)
    try:yield
    finally:csv.field_size_limit(previous)

def combined_field_qa(central,supplement):
    """Aggregate fields without changing central full-page completeness proofs."""
    result=deepcopy(central)
    for key,value in supplement['counts'].items():
        if isinstance(value,int):result['counts'][key]=result['counts'].get(key,0)+value
    scoped={}
    for item in result['source_scope']+supplement['source_scope']:
        key=(item['source_type'],item['source_id']);values=scoped.setdefault(key,{'source_type':key[0],'source_id':key[1]})
        for field,count in item.items():
            if isinstance(count,int):values[field]=values.get(field,0)+count
    result['source_scope']=list(scoped.values())
    for field in ('rating','review_count'):
        for status in ('VALID','NOT_DISPLAYED','PARSE_FAILED','SOURCE_CONFLICT','UNVERIFIED'):
            count=supplement['counts'].get(field+'_'+status,0)
            if count:result.setdefault('field_status_by_reverified_page_state',[]).append({'field':field,'field_status':status,
                'page_state':'HISTORICAL_SUPPLEMENT_COMPLETENESS_UNVERIFIED','observations':count})
    result['page_state_metric_denominator']='All latest full-page reverified observations plus independently bound historical supplement cards; supplement page completeness unverified'
    result['metric_scope']={'central_full_page_observations':central['counts']['observations'],
        'historical_supplement_observations':supplement['counts']['observations'],'combined_observations':result['counts']['observations']}
    result['ok']=central['ok'] and supplement['ok'];result['findings'].extend(supplement['findings'])
    return result


def reconcile_report_scope(report_manifest,out):
    """New metadata projection over independently rechecked sealed exports."""
    from itertools import zip_longest
    from .budgets import utc_text,aware_time
    started=time.perf_counter();previous_reference=_ref(report_manifest);manifest=_read_ref(previous_reference)
    artifacts=deepcopy(manifest['artifacts'])
    def load(name):
        reference=artifacts[name]
        if _ref(reference['path'])!=reference:raise ValueError('SCOPE_RECONCILIATION_ARTIFACT_HASH_CONFLICT:'+name)
        return json.loads(Path(reference['path']).read_text(encoding='utf-8'))
    for name,reference in artifacts.items():
        if 'rows' not in reference:
            if _ref(reference['path'])!=reference:raise ValueError('SCOPE_RECONCILIATION_ARTIFACT_HASH_CONFLICT:'+name)
            continue
        for kind in ('csv','jsonl'):
            if _ref(reference[kind]['path'])!=reference[kind]:raise ValueError('SCOPE_RECONCILIATION_EXPORT_HASH_CONFLICT')
        count=0
        with bounded_csv_fields(),Path(reference['jsonl']['path']).open(encoding='utf-8') as jh,Path(reference['csv']['path']).open(encoding='utf-8-sig',newline='') as ch:
            for line,record in zip_longest(jh,csv.DictReader(ch)):
                if line is None or record is None:raise ValueError('SCOPE_RECONCILIATION_EXPORT_COUNT_CONFLICT')
                value=json.loads(line);count+=1
                for key,item in value.items():
                    actual=record.get(key)
                    if isinstance(item,(dict,list)):
                        if json.loads(actual)!=item:raise ValueError('SCOPE_RECONCILIATION_EXPORT_FIELD_CONFLICT:'+key)
                    elif actual!=('' if item is None else str(item)):raise ValueError('SCOPE_RECONCILIATION_EXPORT_FIELD_CONFLICT:'+key)
                if any(item!='' for key,item in record.items() if key not in value):raise ValueError('SCOPE_RECONCILIATION_EXTRA_CSV_FIELD')
        if count!=reference['rows']:raise ValueError('SCOPE_RECONCILIATION_EXPORT_COUNT_CONFLICT')
    source_quality=load('source_quality');history=load('history_coverage_qa');supplement=history['supplement_field_qa']
    latest=manifest['latest_replay_observations'];central=deepcopy(source_quality['card_field_quality'])
    if supplement['counts']['observations']>0 and central['counts']['observations']==latest+supplement['counts']['observations']:
        for key,value in supplement['counts'].items():
            if isinstance(value,int):central['counts'][key]-=value
        size=len(supplement['source_scope'])
        if central['source_scope'][-size:]!=supplement['source_scope']:raise ValueError('SCOPE_RECONCILIATION_CENTRAL_SCOPE_SUFFIX_CONFLICT')
        central['source_scope']=central['source_scope'][:-size]
    if central['counts']['observations']!=latest:raise ValueError('SCOPE_RECONCILIATION_CENTRAL_DENOMINATOR_CONFLICT')
    source_quality['card_field_quality']=central
    qa=combined_field_qa(central,supplement)
    if qa['counts']['observations']!=manifest['observations']:raise ValueError('SCOPE_RECONCILIATION_COMBINED_DENOMINATOR_CONFLICT')
    for domain,total in ((central,latest),(qa,manifest['observations'])):
        if sum(r['observations'] for r in domain['source_scope'])!=total:raise ValueError('SCOPE_RECONCILIATION_SOURCE_DENOMINATOR_CONFLICT')
        for field in ('rating','review_count'):
            if sum(r['observations'] for r in domain['field_status_by_reverified_page_state'] if r['field']==field)!=total:
                raise ValueError('SCOPE_RECONCILIATION_FIELD_STATE_DENOMINATOR_CONFLICT')
    directory=Path(out)/('final_v3_scope_'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    for name,value in (('qa',qa),('source_quality',source_quality)):
        path=directory/(name+'.json');immutable_json(path,value);artifacts[name]=_ref(path)
    timing=load('performance');verified_at=utc_text();elapsed=time.perf_counter()-started
    timing['original_report_verified_at']=timing.get('final_report_verified_at');timing['final_delivery_verified_at']=verified_at
    timing['scope_reconciliation_seconds']=elapsed
    if timing.get('finished_at'):
        timing.update(finished_at=verified_at,final_report_verified_at=verified_at,
            total_wall_seconds=(aware_time(verified_at)-aware_time(timing['started_at'])).total_seconds())
        if timing.get('fresh_unique_per_hour') is not None:
            rate=timing['fresh_new_unique_links']*3600/timing['total_wall_seconds']
            timing.update(fresh_unique_per_hour=rate,unique_per_hour=rate,unique_per_minute=rate/60)
        timing['ten_hour_target_passed']=timing['ten_hour_target_passed'] and timing['total_wall_seconds']<=36000
    timing['stages']['scope_reconciliation']={'count':1,'total_seconds':elapsed,'mean_seconds':elapsed,'p50_seconds':elapsed,'p95_seconds':elapsed}
    if timing.get('timing_breakdown',{}).get('qa_seconds') is not None:timing['timing_breakdown']['qa_seconds']+=elapsed
    performance_path=directory/'performance.json';immutable_json(performance_path,timing);artifacts['performance']=_ref(performance_path)
    manifest.update(artifacts=artifacts,scope_projection_version='separate_central_and_combined_denominators_v3.1',
        original_report_manifest_reference=previous_reference,scope_reconciliation_verified_at=verified_at,
        independent_all_export_fields_reverified=True,central_source_page_observations=latest,
        combined_field_observations=qa['counts']['observations'],original_DOM_audits_reused_with_verified_artifact_SHA=True)
    immutable_json(directory/'manifest.json',sealed(manifest))
    return {'directory':str(directory),'manifest':manifest}


def report_replay_v3(root,run_id,out,*,mapping_config=None,history_manifest=None):
    from .candidate_contract import build_candidate_records
    from .quality import audit_run
    from .sources import detailed_coverage_matrix
    started=time.perf_counter()
    with readonly_store(root) as store:
        run=store.get_run(run_id);pages=store.pages(run_id);rows=list(store.iter_observations(run_id));sources=run['plan']['sources']
        origin=run['plan'].get('origin')
        if run['plan'].get('snapshot_contract_version')!=3 or origin not in {'HISTORICAL_REPLAY','LIVE_CAPTURE'}:
            raise ValueError('V3_SNAPSHOT_RUN_REQUIRED')
        historical=origin=='HISTORICAL_REPLAY'
        source_quality=audit_run(store,run_id)
        qa=deepcopy(source_quality['card_field_quality'])
        latest_row_count=len(rows);history_qa=None;supplement_pages={}
        if history_manifest:
            added,history_qa,supplement_pages=recover_history_supplement(history_manifest,run)
            rows.extend(added)
            qa=combined_field_qa(qa,history_qa['supplement_field_qa'])
            if not history_qa['ok']:
                qa['ok']=False;qa['findings'].extend(history_qa['findings'])
        replay_manifest=read_sealed(Path(root).parent/'replay_manifest.json') if historical else {}
        if replay_manifest.get('failures'):
            qa['ok']=False
            qa['replay_page_failures']=replay_manifest['failures']
            qa['findings'].extend({'code':'HISTORICAL_PAGE_REPLAY_INCOMPLETE','severity':'ERROR',**failure}
                for failure in replay_manifest['failures'])
        from .category_mapping import map_observation_category
        rules_ref=_ref(mapping_config) if mapping_config else (replay_manifest.get('mapping_snapshot_reference') or run['plan'].get('category_mapping_config_reference'))
        if not rules_ref or _ref(rules_ref['path'])!=rules_ref:raise ValueError('V3_REPORT_MAPPING_RULES_REFERENCE_REQUIRED')
        rules=load_mapping_rules(rules_ref['path']);source_map={s['source_id']:s for s in sources}
        page_map={p['page_key']:p for p in pages}
        observation_pages={}
        if not historical:
            from .capture_history import live_observation_capture_pages
            observation_pages=live_observation_capture_pages(store,run_id,rows)
        else:
            for page in pages:
                for row in read_sealed(page['journal_path'])['observations']:observation_pages[row['observation_id']]=page
        observation_pages.update(supplement_pages)
        projection=[];mapping_errors=[];mapping_cache={}
        def derive(row,cache):
            parent=observation_pages[row['observation_id']]
            return map_observation_category(row,source_map[row['source_id']],rules,
                parent_html_path=parent['evidence_path'],parent_html_sha256=parent['evidence_sha256'],cache=cache)
        for row in rows:
            previous={k:v for k,v in row.items() if k.startswith('category_mapping_') or k in {'research_category','category_path','browse_node_id','original_research_category','source_conflict'}}
            try:corrected=derive(row,mapping_cache)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                corrected={'research_category':None,'category_mapping_status':'UNKNOWN','category_mapping_reason':str(exc),
                    'category_mapping_evidence':[],'category_mapping_candidates':[]}
                mapping_errors.append({'observation_id':row['observation_id'],'reason':str(exc)})
            row.update(corrected,previous_category_projection=previous,category_projection_version='offline_card_context_projection_v3.1',
                category_projection_rules_reference=rules_ref)
            projection.append({'observation_id':row['observation_id'],'previous_category_projection':previous,'category_projection':corrected,
                'rules_reference':rules_ref,'projection_version':'offline_card_context_projection_v3.1'})
        checked_mapping_cache={};mapping_mismatches=[]
        for row,item in zip(rows,projection):
            try:
                expected=derive(row,checked_mapping_cache)
                if expected!=item['category_projection']:mapping_mismatches.append(row['observation_id'])
            except (ValueError,OSError,KeyError,TypeError):
                if not any(f['observation_id']==row['observation_id'] for f in mapping_errors):mapping_mismatches.append(row['observation_id'])
        mapping_qa={'ok':not mapping_errors and not mapping_mismatches,'denominator':len(rows),'errors':mapping_errors,
                    'projection_mismatches':mapping_mismatches,'rules_reference':rules_ref,'immutable_original_rows_modified':False}
        eligible=[row for row in rows if row.get('source_evidence_eligible') is not False and row.get('is_valid') is True]
        if history_qa:
            archive=history_qa['original_index_reference']
            if _ref(archive['path'])!=archive:raise ValueError('HISTORY_ORIGINAL_INDEX_ARCHIVE_HASH_CONFLICT')
            original_urls=set()
            with Path(archive['path']).open(encoding='utf-8') as handle:
                for line in handle:
                    old=json.loads(line);normalized=normalize_product_url_v3(old['raw_product_url'],base_url=old['page_url'])
                    if normalized['is_valid'] and old.get('source_evidence_eligible') is not False:original_urls.add(normalized['normalized_product_url'])
            current_urls={row['normalized_product_url'] for row in eligible}
            history_qa.update(original_canonical_urls=len(original_urls),exported_canonical_urls=len(current_urls),
                canonical_url_sets_equal=original_urls==current_urls,missing_canonical_urls=sorted(original_urls-current_urls),
                extra_canonical_urls=sorted(current_urls-original_urls))
            if original_urls!=current_urls:
                history_qa['ok']=False;qa['ok']=False;qa['findings'].append({'code':'HISTORY_CANONICAL_URL_SET_CONFLICT','severity':'ERROR'})
        excluded=[row for row in rows if row.get('source_evidence_eligible') is False or row.get('is_valid') is not True]
        candidates=build_candidate_records(eligible,snapshot_id=run['snapshot_id'],batch_id=run_id)
        matrix=detailed_coverage_matrix(sources,pages,[dict(r,target_group_id=r.get('normalized_product_url'),link_valid=r.get('is_valid') is True) for r in rows])
        mapping=Counter(r.get('category_mapping_status','UNKNOWN') for r in rows)
        source_status=Counter(p.get('page_content_state','UNKNOWN') for p in pages)
        anomalies=[{k:r.get(k) for k in ('observation_id','raw_product_url','normalized_product_url','normalization_evidence','unknown_query_parameters','preserved_identity_parameters','unreviewed_path_suffix','url_identity_status','dedup_status')}
                   for r in rows if not r.get('is_valid') or r.get('unknown_query_parameters') or r.get('unreviewed_path_suffix')]
        directory=Path(out)/('report_v3_'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
        artifacts={'observations':_write_records(directory,'observations_v3',rows),
                   'candidates':_write_records(directory,'candidate_snapshot_v3',candidates),
                   'excluded_pending':_write_records(directory,'excluded_pending_observations',excluded),
                   'mapping_projection':_write_records(directory,'category_mapping_projection',projection),
                   'coverage':_write_records(directory,'five_lists_15_categories',matrix),
                   'url_anomalies':_write_records(directory,'url_anomalies',anomalies)}
        summaries={'qa':qa,'source_quality':source_quality,'mapping_projection_qa':mapping_qa,'category_qa':{'denominator':len(rows),'mapping_status_counts':dict(mapping),
                    'scope':'all batch observations; category unknown is not a product filter'},
            'source_qa':{'denominator_pages':len(pages),'page_content_states':dict(source_status)}}
        if history_qa:summaries['history_coverage_qa']=history_qa
        for name,data in summaries.items():
            path=directory/(name+'.json');immutable_json(path,data);artifacts[name]=_ref(path)
        for name,reference in artifacts.items():
            if 'rows' in reference:
                for kind in ('jsonl','csv'):
                    if digest(Path(reference[kind]['path']).read_bytes())!=reference[kind]['sha256']:raise ValueError('V3_EXPORT_HASH_CONFLICT')
                with Path(reference['jsonl']['path']).open(encoding='utf-8') as h:
                    if sum(1 for _ in h)!=reference['rows']:raise ValueError('V3_EXPORT_COUNT_CONFLICT')
                with bounded_csv_fields(), Path(reference['csv']['path']).open(encoding='utf-8-sig',newline='') as h:
                    if sum(1 for _ in csv.DictReader(h))!=reference['rows']:raise ValueError('V3_EXPORT_COUNT_CONFLICT')
                with bounded_csv_fields(), Path(reference['jsonl']['path']).open(encoding='utf-8') as json_handle, Path(reference['csv']['path']).open(encoding='utf-8-sig',newline='') as csv_handle:
                    for line,csv_row in zip(json_handle,csv.DictReader(csv_handle)):
                        record=json.loads(line)
                        for key,value in record.items():
                            actual=csv_row.get(key)
                            if isinstance(value,(dict,list)):
                                if json.loads(actual)!=value:raise ValueError('V3_EXPORT_FIELD_CONFLICT:'+key)
                            elif actual!=('' if value is None else str(value)):raise ValueError('V3_EXPORT_FIELD_CONFLICT:'+key)
        text=('V3历史网页离线恢复结果；不是新的3万官网采集。\n' if historical else 'V3真实采集批次的离线质量复核。阶段通过必须独立严格验收。\n')+f'候选记录：{len(candidates)}；原始观察：{len(rows)}；DOM与原始捕获收据QA：{qa["ok"]}。\n'+ '保留真实空页、导航页、未知完整性、评分冲突和未映射类目。独立可购SKU和父子关系尚未确认。T1/T2/T3没有据此通过。\n'
        text_path=directory/'历史恢复说明.txt';text_path.write_text(text,encoding='utf-8');artifacts['scope_readme']=_ref(text_path)
        from .performance import summarize_v3_metrics
        from .budgets import utc_text
        verified_at=utc_text();worker_finished=run.get('finished_at') or run.get('ended_at')
        terminal=run.get('status') not in {'RUNNING','CREATED','NEW','USER_PAUSED'}
        offline_sealed_complete=(historical and replay_manifest.get('run_id')==run_id
            and replay_manifest.get('request_reservations')==0 and run.get('status') in {'HISTORICAL_REPLAY_COMPLETED','HISTORICAL_REPLAY_WITH_FAILURES'})
        endpoint=verified_at if terminal and (worker_finished or offline_sealed_complete) else None
        report_elapsed=time.perf_counter()-started
        events=list(store.events(run_id))+[{'kind':'REPORT_QA_EXPORT_VERIFIED','stage':'qa','duration_seconds':report_elapsed}]
        timing=summarize_v3_metrics(events,observations=rows,started_at=run.get('started_at'),finished_at=endpoint,
            target=run['plan']['target_unique_links'],origin=origin,
            qa_passed=qa['ok'] and mapping_qa['ok'] and source_quality['quality_gate_passed'] and terminal)
        timing.update(worker_finished_at=worker_finished,final_report_verified_at=verified_at,
            report_qa_export_seconds=report_elapsed,live_requests=0 if historical else len(store.requests(run_id)),
            replay_seconds=run.get('replay_wall_seconds'),run_completed=terminal and bool(worker_finished or offline_sealed_complete),
            timing_scope='Original run start through complete report DOM, receipts, CSV/JSONL parity and SHA verification' if endpoint else 'Incomplete or paused run; no final-wall throughput claim',
            current_live_feasibility='UNVERIFIED' if historical else 'Measured completed run only; future supply unverified')
        performance_path=directory/'performance.json';immutable_json(performance_path,timing);artifacts['performance']=_ref(performance_path)
        manifest={'run_id':run_id,'origin':origin,'is_derivation':historical,'live_requests':0 if historical else len(store.requests(run_id)),
            'stage':'T0_HISTORICAL_FIELD_RECOVERY_NOT_NEW_LIVE_ACCEPTANCE' if historical else run['plan']['stage'],'qa_ok':qa['ok'],
            'fields_qa_ok':qa['ok'],'source_quality_gate_passed':source_quality['quality_gate_passed'],
            'mapping_projection_qa_ok':mapping_qa['ok'],
            'observations':len(rows),'candidate_records':len(candidates),'matrix_cells':len(matrix),
            'latest_replay_observations':latest_row_count,'supplemental_historical_capture_observations':len(rows)-latest_row_count,
            'all_history_coverage_qa':history_qa,
            'historical_capture_run_ids':sorted({r.get('capture_run_id') for r in rows if r.get('capture_run_id')}),
            'independent_purchasable_SKU_confirmed':False,'T1_T2_T3_passed':False,'artifacts':artifacts,
            'csv_jsonl_all_fields_verified':True,
            'wall_seconds':time.perf_counter()-started}
        immutable_json(directory/'manifest.json',sealed(manifest))
        return {'directory':str(directory),'manifest':manifest}

def verified_pending_navigation(pack_path):
    """Rebuild each pending literal navigation source from its real saved DOM."""
    from .sources import discover_sources
    reference=_ref(pack_path);pack=json.loads(Path(pack_path).read_text(encoding='utf-8'))
    if pack.get('approved') is not False or pack.get('live_enabled') is not False:
        raise ValueError('PENDING_NAVIGATION_PACK_MUST_NOT_AUTHORIZE_LIVE')
    cache={};result=[];seen=set()
    for item in pack['sources']:
        evidence=item['discovery_evidence'][0];key=(evidence['evidence_path'],evidence['evidence_sha256'],item['source_type'],evidence['page_url'])
        if key not in cache:
            body=Path(evidence['evidence_path']).read_bytes()
            if digest(body)!=evidence['evidence_sha256']:raise ValueError('PENDING_NAVIGATION_BODY_HASH_CONFLICT')
            cache[key]=discover_sources(body.decode('utf-8'),item['source_type'],evidence['page_url'],
                evidence_path=evidence['evidence_path'],observed_at=evidence['observed_at'],origin='HISTORICAL_REPLAY')
        matches=[source for source in cache[key] if source['actual_source_url']==item['actual_source_url'] and source['source_id']==item['source_id']
            and any(proof['raw_href']==evidence['raw_href'] and proof['label_raw']==evidence['label_raw'] for proof in source['discovery_evidence'])]
        if len(matches)!=1:raise ValueError('PENDING_NAVIGATION_LITERAL_DOM_BINDING_CONFLICT')
        key=(item['source_type'],item['actual_source_url'])
        if key in seen:continue
        seen.add(key);result.append({**matches[0],'page_urls':[item['actual_source_url']], 'parser_profile':'V3',
            'review_status':'PENDING_NEW_EXACT_SCOPE_REVIEW','current_availability_verified':False,'pending_navigation_pack_reference':reference})
    if _ref(pack_path)!=reference:raise ValueError('PENDING_NAVIGATION_PACK_CHANGED')
    return result,reference


def pending_source_order_v3(sources,new_sources):
    """Probe literal new deep navigation before exhausted department scopes.

    Preserve every exact source and five-type round robin. Within the two
    historically cardless navigation types, the global root remains first,
    then newly evidenced deep links. This is ordering, not source validation.
    """
    from urllib.parse import urlsplit
    from .schedule import source_order
    newly_discovered={source['source_id'] for source in new_sources}
    def priority(source):
        if source['source_type'] not in {'MOST_WISHED_FOR','MOST_GIFTED'}:return 0
        parts=[part for part in urlsplit(source['actual_source_url']).path.split('/') if part]
        if len(parts)==2:return 0
        return 1 if source['source_id'] in newly_discovered else 2
    return source_order(sorted(sources,key=priority),policy='FIVE_TYPE_ROUND_ROBIN_V3')


def prepare_v3(historical_root,historical_run_id,out,*,mapping_config,pending_source_pack=None,runtime_root=None):
    """Literal prior navigation plan only; new LIVE batches remain unapproved."""
    from .contracts import DEFAULT_CONFIG
    from .schedule import source_order
    from .budgets import utc_text
    mapping_reference=_ref(mapping_config)
    batch_runtime=Path(runtime_root).resolve() if runtime_root else Path(historical_root).resolve().parents[1]/'highspeed_v3_fresh_batches'
    rules=load_mapping_rules(mapping_config)
    if _ref(mapping_config)!=mapping_reference:raise ValueError('MAPPING_CONFIG_CHANGED_DURING_LOAD')
    with readonly_store(historical_root) as store:
        prior=store.get_run(historical_run_id)
        prior_ref=_ref(store.root/'runs'/historical_run_id/'plan.json')
        sources=[]
        for old in prior['plan']['sources']:
            source={k:v for k,v in old.items() if k not in {'offline_pages','offline_metadata','seed_reference'}}
            source.update(map_category(old,rules),parser_profile='V3')
            sources.append(source)
        new_sources,pending_reference=verified_pending_navigation(pending_source_pack) if pending_source_pack else ([],None)
        existing={(s['source_type'],s['actual_source_url']) for s in sources}
        for source in new_sources:
            key=(source['source_type'],source['actual_source_url'])
            if key not in existing:
                sources.append({**source,**map_category(source,rules)});existing.add(key)
        sources=pending_source_order_v3(sources,new_sources)
    directory=Path(out)/('pending_v3_'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    outputs={}
    for stage,count,requests,pages in [('T1',5,50,20),('T2',len(sources),500,150),('T3',len(sources),8000,2000)]:
        selected=[];kinds=set()
        for source in sources:
            if stage=='T1' and source['source_type'] in kinds:continue
            selected.append(source);kinds.add(source['source_type'])
            if len(selected)>=count:break
        if stage=='T1':
            from urllib.parse import urlsplit
            selected=[next((candidate for candidate in sources if candidate['source_type']==source['source_type']
                and len([part for part in urlsplit(candidate['actual_source_url']).path.split('/') if part])==2),source)
                for source in selected]
            for kind in ('MOST_WISHED_FOR','MOST_GIFTED'):
                selected.extend([s for s in new_sources if s['source_type']==kind][:2])
        run_id='v3_'+stage.lower()+'_'+uuid.uuid4().hex[:12]
        plan={**DEFAULT_CONFIG,'mode':'HIGH_SPEED_V2','snapshot_contract_version':3,'normalizer_version':NORMALIZER_VERSION,
            'run_id':run_id,'snapshot_id':run_id,'stage':stage,'origin':'LIVE_CAPTURE','live_enabled':False,'approved':False,
            'sources_reviewed':False,'sources':selected,'source_order_policy':'FIVE_TYPE_ROUND_ROBIN_V3',
            'source_scope_priority_policy':'ROOT_THEN_NEW_LITERAL_DEEP_BEFORE_HISTORICAL_DEPARTMENT_NAV_v3.1',
            'max_duration_hours':10,'request_pacing_seconds':3,'parallelism':1,'category_cooldown_seconds':0,
            'max_requests_per_run':requests,'max_requests_per_day':requests,'max_pages_per_run':pages,
            'max_raw_observations_per_run':150000,'history_observation_limit':1000000,
            'target_unique_links':30000 if stage=='T3' else 500 if stage=='T1' else 5000,
            'probe_all_reviewed_page_urls':stage=='T1',
            'source_navigation_plan_reference':prior_ref,'source_pack_sha256':digest(selected),
            'pending_navigation_pack_reference':pending_reference,'pending_navigation_availability_verified':False,
            'category_mapping_config_reference':mapping_reference,
            'new_batch_required':True,'historical_evidence_is_not_new_capture':True,
            'review_status':'PENDING_EXACT_SOURCE_BUDGET_GATE_AND_STAGE_REVIEW','prior_stage_artifacts':[],
            'shared_access_gate_path':prior['plan'].get('shared_access_gate_path')}
        runtime=batch_runtime/run_id/'store'
        authorization={**{k:v for k,v in plan.items() if k!='sources'},'approved':False,'approved_by':None,
            'approval_id':None,'store_root':str(runtime),'reviewed_at':None,'expires_at':None,
            'access_gate_state':'NORMAL','stop_conditions':['CHALLENGE','BLOCKED','RATE_LIMITED','UNKNOWN','NETWORK_ERROR'],
            'prerequisites':{s:None for s in ({'T1':[],'T2':['T0','T1'],'T3':['T0','T1','T2']}[stage])},
            'sources':{s['source_id']:{'source_type':s['source_type'],'research_category':s.get('research_category'),
                'parser_profile':'V3',
                'page_urls':s['page_urls'],'allow_native_acp':True,'evidence':{'reviewed':False,**prior_ref},
                'navigation_evidence':s.get('discovery_evidence',[])} for s in selected}}
        plan_path=directory/(stage+'.plan.pending.json');auth_path=directory/(stage+'.authorization.pending.json')
        immutable_json(plan_path,plan);immutable_json(auth_path,authorization)
        outputs[stage]={'plan':_ref(plan_path),'authorization':_ref(auth_path),'run_id':run_id,'store_root':str(runtime),'browser_profile':str(runtime.parent/'browser_profile')}
    script=directory/'Start-Reviewed-V3.ps1'
    script.write_text("param([Parameter(Mandatory=$true)][string]$PlanPath,[Parameter(Mandatory=$true)][string]$AuthorizationPath,[Parameter(Mandatory=$true)][string]$StoreRoot,[Parameter(Mandatory=$true)][string]$PythonPath,[Parameter(Mandatory=$true)][string]$WorktreePath)\n$ErrorActionPreference='Stop'\n$plan=Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json\n$auth=Get-Content -LiteralPath $AuthorizationPath -Raw | ConvertFrom-Json\nif ($plan.live_enabled -ne $true -or $auth.approved -ne $true) { throw 'Pending templates require explicit source/budget/stage approval before activation.' }\n$env:PYTHONPATH=Join-Path $WorktreePath 'src'\n$env:PYTHONDONTWRITEBYTECODE='1'\n$env:PYTHONIOENCODING='utf-8'\nRemove-Item Env:RUN_LIVE -ErrorAction SilentlyContinue\n$ProfilePath=Join-Path (Split-Path -Parent $StoreRoot) 'browser_profile'\n& $PythonPath -m amazon_es_bestseller.cli highspeed run --root $StoreRoot --plan $PlanPath --authorization $AuthorizationPath --run-id $plan.run_id --profile-dir $ProfilePath --headful --live\nif ($LASTEXITCODE -ne 0) { throw 'Reviewed V3 run failed; retain logs and evidence.' }\n",encoding='utf-8-sig')
    capacity_reference=None
    if pending_source_pack:
        from .v3_capacity import export_capacity
        capacity_path=directory/'source_capacity_estimate.json'
        export_capacity(historical_root,historical_run_id,outputs['T2']['plan']['path'],pending_source_pack,capacity_path)
        capacity_reference=_ref(capacity_path)
    manifest={'origin':'OFFLINE_PREPARATION','live_requests':0,'approved':False,'generated_at':utc_text(),
        'source_navigation_plan_reference':prior_ref,'mapping_config_reference':mapping_reference,'stages':outputs,'launcher':_ref(script),
        'pending_navigation_pack_reference':pending_reference,'new_literal_navigation_sources':len(new_sources),
        'fresh_batch_runtime_root':str(batch_runtime),'runtime_directories_created':False,
        'source_capacity_estimate_reference':capacity_reference,
        'T1_T2_T3_passed':False,'stage_gate_prerequisites_required':True}
    immutable_json(directory/'manifest.json',sealed(manifest));return {'directory':str(directory),'manifest':manifest}


def archive_history_v3(historical_root,historical_run_id,replay_root,replay_run_id,out):
    """All original indexed rows; version-specific title/DOM and receipt audit."""
    from bs4 import BeautifulSoup
    from .adapters import _parse_cards
    from .continuation import _verify_original_titles
    directory=Path(out)/('history_v3_'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    with readonly_store(replay_root) as replay:
        replay_rows=list(replay.iter_observations(replay_run_id))
    represented=defaultdict(list)
    for row in replay_rows:
        for old_id in row.get('historical_observation_ids',[]):represented[old_id].append(row['observation_id'])
    counts=Counter();findings=[];supplement=[];missing_captures=[];references=OrderedDict();parsed_cache=OrderedDict()
    replay_captures=defaultdict(list)
    for item in replay_rows:
        key=(item.get('source_id'),item.get('evidence_sha256'),item.get('representation_slot'),item.get('raw_product_url'),item.get('ranking_position'),item.get('rank_context'),item.get('observed_at'))
        replay_captures[key].append(item['observation_id'])
    with readonly_store(historical_root) as old:
        original_run=old.get_run(historical_run_id);source_map={s['source_id']:s for s in original_run['plan']['sources']}
        indexed=[(r[0],r[1],json.loads(r[2])) for r in old.db.execute('SELECT observation_id,page_key,data_json FROM observations WHERE run_id=? ORDER BY rowid',(historical_run_id,))]
        original_plan_sha=digest(original_run['plan']);groups=defaultdict(list)
        for _,_,item in indexed:
            groups[(item.get('evidence_path'),item.get('source_id'),str(item.get('representation','')).startswith('ACP_'))].append(item)
        captures=defaultdict(list)
        runs={historical_run_id}|{r.get('parent_run_id') for _,_,r in indexed if r.get('parent_run_id')}
        for capture_run in sorted(runs):
            for receipt in old.iter_responses(capture_run):
                if receipt.get('origin')=='LIVE_CAPTURE' and receipt.get('status_code')==200 and receipt.get('access_state')=='NORMAL':
                    captures[(capture_run,receipt['source_id'],receipt['html_path'],receipt['kind'])].append(receipt)
        history_path=directory/'all_original_index_rows.jsonl';audit_path=directory/'all_original_index_binding_audit.jsonl'
        with history_path.open('x',encoding='utf-8') as history_handle,audit_path.open('x',encoding='utf-8') as audit_handle:
            for index,(oid,page_key,row) in enumerate(indexed):
                history_handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
                counts['original_index_rows']+=1
                assessment={'observation_id':oid,'original_run_id':historical_run_id,'represented_by_latest_replay_observation_ids':represented.get(oid,[]),
                    'historical_source_evidence_eligible':row.get('source_evidence_eligible'),'original_parser_version':row.get('parser_version'),
                    'is_revision':'_rev_' in oid,'status':'UNVERIFIED','live_requests':0}
                try:
                    source=source_map[row['source_id']];path=row['evidence_path'];sha=row['evidence_sha256'];fragment=str(row.get('representation','')).startswith('ACP_')
                    key=(path,sha,digest(source),row['page_url'],fragment)
                    if key not in parsed_cache:
                        body=Path(path).read_bytes()
                        if digest(body)!=sha:raise ValueError('HISTORY_BODY_HASH_CONFLICT')
                        soup=BeautifulSoup(body.decode('utf-8'),'lxml')
                        parsed=_parse_cards(soup,source=source,run_id=historical_run_id,snapshot_id=row.get('snapshot_id'),
                            page_url=row['page_url'],page_number=row.get('page_number'),observed_at=row['observed_at'],
                            evidence_path=path,sha=sha,representation=row.get('representation'),fragment=fragment)
                        # Each body is bounded; declared legacy titles are reproduced separately.
                        group=groups[(path,row['source_id'],fragment)]
                        _verify_original_titles(group)
                        parsed_cache[key]=parsed
                        while len(parsed_cache)>8:parsed_cache.popitem(last=False)
                    parsed_cache.move_to_end(key)
                    matches=[r for r in parsed_cache[key] if r['representation_slot']==row.get('representation_slot')
                        and r['raw_product_url']==row['raw_product_url'] and r['ranking_position']==row.get('ranking_position')
                        and r['rank_context']==row.get('rank_context') and r['rank_raw']==row.get('rank_raw')]
                    if len(matches)!=1:raise ValueError('HISTORY_RAW_CARD_BINDING_CONFLICT')
                    capture_run=row.get('parent_run_id') if row.get('is_derivation') else historical_run_id
                    kind='ACP' if fragment else 'PAGE'
                    receipts=captures[(capture_run,row['source_id'],path,kind)]
                    if kind=='PAGE':receipts=[r for r in receipts if r.get('request_url')==row['page_url'] and r.get('observed_at')==row.get('observed_at')]
                    if not receipts:raise ValueError('HISTORY_ORIGINAL_NORMAL_RECEIPT_REQUIRED')
                    receipt=max(receipts,key=lambda r:(r['observed_at'],r['request_id']))
                    reference=_ref(receipt['evidence_path']);actual=_read_ref(reference,references)
                    if (actual.get('sha256')!=sha or actual.get('run_id')!=capture_run
                        or actual.get('source_id')!=source['source_id'] or actual.get('source_type')!=source['source_type']
                        or actual.get('status_code')!=200 or actual.get('access_state')!='NORMAL'
                        or actual.get('origin')!='LIVE_CAPTURE' or actual.get('kind')!=kind
                        or actual.get('html_path')!=path):raise ValueError('HISTORY_RECEIPT_BODY_OR_RUN_CONFLICT')
                    if row.get('is_derivation'):
                        seed_reference={'path':row['seed_provenance_path'],'sha256':row['seed_provenance_sha256']}
                        seed=_read_ref(seed_reference,references)
                        if seed.get('parent_run_id')!=capture_run or seed.get('successor_run_id')!=historical_run_id or seed.get('successor_plan_sha256')!=original_plan_sha:
                            raise ValueError('HISTORY_PARENT_SEED_BINDING_CONFLICT')
                        if reference not in seed.get('receipts',[]):raise ValueError('HISTORY_RECEIPT_NOT_IN_PARENT_PROVENANCE')
                    assessment.update(status='DECLARED_PARSER_DOM_AND_ORIGINAL_CAPTURE_BOUND',capture_run_id=capture_run,
                        capture_receipt_reference=reference,capture_observed_at=receipt['observed_at'])
                    counts['declared_parser_dom_capture_bound']+=1
                    exact_key=(row['source_id'],sha,row.get('representation_slot'),row['raw_product_url'],row.get('ranking_position'),row.get('rank_context'),receipt['observed_at'])
                    assessment['represented_by_exact_capture_observation_ids']=replay_captures.get(exact_key,[])
                    if not replay_captures.get(exact_key):
                        missing_captures.append(oid)
                        counts['valid_or_raw_records_not_in_latest_representation']+=1
                        journals=seed.get('journals',[]) if row.get('is_derivation') else [_ref(p) for p in (old.root/'runs'/historical_run_id/'pages').glob('*.json')]
                        matched_journals=[]
                        for journal_reference in journals:
                            journal=_read_ref(journal_reference,references)
                            if journal.get('run_id')!=capture_run or journal['page'].get('source_id')!=row['source_id'] or journal['page'].get('page_url')!=row['page_url']:continue
                            if any(r.get('evidence_path')==path and r.get('representation_slot')==row.get('representation_slot')
                                and r.get('raw_product_url')==row['raw_product_url'] and r.get('ranking_position')==row.get('ranking_position')
                                and r.get('rank_context')==row.get('rank_context') for r in journal['observations']):
                                matched_journals.append((journal,journal_reference))
                        if not matched_journals:raise ValueError('HISTORY_SUPPLEMENT_ORIGINAL_JOURNAL_REQUIRED')
                        journal,journal_reference=matched_journals[-1]
                        supplement.append({'historical_row':row,'historical_page_key':page_key,'capture_receipt':actual,'capture_receipt_reference':reference,
                            'historical_page_journal_reference':journal_reference,'historical_journal_run_id':journal['run_id'],
                            'historical_seed_provenance_reference':seed_reference if row.get('is_derivation') else None})
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    assessment['reason']=str(exc);findings.append(assessment);counts['unverified']+=1
                audit_handle.write(json.dumps(assessment,ensure_ascii=False,allow_nan=False)+'\n')
    manifest={'historical_run_id':historical_run_id,'replay_run_id':replay_run_id,'origin':'HISTORICAL_REPLAY','live_requests':0,
        'counts':dict(counts),'findings':findings,'all_history_index_archived':len(indexed)==counts['original_index_rows'],
        'current_run_index_ids':len(indexed),'latest_replay_rows':len(replay_rows),
        'referenced_parent_ids_not_current_namespace':sorted(set(represented)-{oid for oid,_,_ in indexed}),
        'missing_current_ids_from_latest_id_references':[oid for oid,_,_ in indexed if oid not in represented],
        'missing_current_ids_from_latest_view':missing_captures,
        'artifacts':{'original_index':_ref(history_path),'binding_audit':_ref(audit_path)}}
    supplement_path=directory/'historical_capture_supplement_inputs.jsonl'
    with supplement_path.open('x',encoding='utf-8') as handle:
        for row in supplement:handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    manifest['artifacts']['supplement_inputs']=_ref(supplement_path)
    immutable_json(directory/'manifest.json',sealed(manifest))
    return {'directory':str(directory),'manifest':manifest}


def recover_history_supplement(history_manifest,run):
    """Reparse missing actual historical captures; never create a LIVE receipt."""
    from .v3_quality import audit_observations
    manifest_reference=_ref(history_manifest);manifest=_read_ref(manifest_reference)
    if manifest['replay_run_id']!=run['run_id'] or manifest.get('live_requests')!=0:
        raise ValueError('HISTORY_REPLAY_SCOPE_CONFLICT')
    inputs_reference=manifest['artifacts']['supplement_inputs']
    if _ref(inputs_reference['path'])!=inputs_reference:raise ValueError('HISTORY_SUPPLEMENT_INPUT_HASH_CONFLICT')
    inputs=[json.loads(line) for line in Path(inputs_reference['path']).read_text(encoding='utf-8').splitlines()]
    sources={s['source_id']:s for s in run['plan']['sources']};rows=[];page_by_id={};cache={};receipt_cache=OrderedDict()
    represented_missing=set();findings=[]
    for item in inputs:
        old=item['historical_row'];source=sources[old['source_id']]
        journal=_read_ref(item['historical_page_journal_reference'],receipt_cache);parent=journal['page']
        receipt=_read_ref(item['capture_receipt_reference'],receipt_cache)
        key=(item['historical_page_journal_reference']['sha256'],source['source_id'])
        if key not in cache:
            server_path=parent['evidence_path']
            if not _body_matches(server_path,parent['evidence_sha256'],receipt_cache):raise ValueError('HISTORY_SUPPLEMENT_PARENT_HASH_CONFLICT')
            if receipt['kind']!='PAGE':raise ValueError('HISTORY_SUPPLEMENT_ACP_FULL_CAPTURE_REQUIRED')
            parsed=parse_page(Path(server_path).read_text(encoding='utf-8'),source=source,run_id=run['run_id'],
                snapshot_id=run['snapshot_id'],page_url=parent['page_url'],page_number=parent.get('page_number'),
                observed_at=receipt['observed_at'],evidence_path=server_path,origin='HISTORICAL_REPLAY')
            cache[key]=parsed['observations']
        matches=[r for r in cache[key] if r['evidence_path']==old['evidence_path'] and r['representation_slot']==old['representation_slot']
            and r['raw_product_url']==old['raw_product_url'] and r['ranking_position']==old.get('ranking_position') and r['rank_context']==old.get('rank_context')]
        if len(matches)!=1:
            findings.append({'code':'HISTORY_SUPPLEMENT_CARD_BINDING_CONFLICT','severity':'ERROR','historical_observation_id':old['observation_id']});continue
        value=dict(matches[0]);logical=(value['source_id'],value['evidence_sha256'],value['representation_slot'],value['raw_product_url'],receipt['observed_at'])
        existing=next((r for r in rows if r.get('historical_capture_tuple')==list(logical)),None)
        if existing:
            existing['historical_observation_ids'].append(old['observation_id']);represented_missing.add(old['observation_id']);continue
        value.update(observation_id='historical_supplement_'+digest(logical)[:32],origin='HISTORICAL_REPLAY',is_derivation=True,
            observed_at=receipt['observed_at'],rating_observed_at=receipt['observed_at'],capture_run_id=receipt['run_id'],
            capture_receipt_reference=item['capture_receipt_reference'],historical_page_journal_reference=item['historical_page_journal_reference'],
            historical_journal_run_id=item['historical_journal_run_id'],historical_seed_provenance_reference=item['historical_seed_provenance_reference'],
            historical_observation_ids=[old['observation_id']],historical_observed_at=old.get('observed_at'),
            original_product_title_raw=old.get('product_title_raw'),historical_capture_tuple=list(logical),
            history_recovery_manifest_reference=manifest_reference,source_completeness_status='UNKNOWN',live_requests=0)
        rows.append(value);page_by_id[value['observation_id']]=parent;represented_missing.add(old['observation_id'])
    audit=audit_observations(rows,run['plan']['sources'],receipt_validator=lambda row,source:validate_replay_receipt(row,source,cache=receipt_cache))
    expected=set(manifest['missing_current_ids_from_latest_view'])
    missing=sorted(expected-represented_missing)
    findings.extend(audit['findings'])
    if missing:findings.append({'code':'HISTORY_INDEX_IDS_NOT_RECOVERED','severity':'ERROR','observation_ids':missing})
    if manifest['counts'].get('unverified',0):findings.append({'code':'ORIGINAL_HISTORY_BINDING_UNVERIFIED','severity':'ERROR','count':manifest['counts']['unverified']})
    qa={'ok':audit['ok'] and not findings,'findings':findings,'historical_index_rows':manifest['counts']['original_index_rows'],
        'supplement_observations':len(rows),'recovered_historical_ids':len(represented_missing),'missing_current_ids':missing,
        'all_original_history_binding_audit_reference':manifest['artifacts']['binding_audit'],'history_manifest_reference':manifest_reference,
        'original_index_reference':manifest['artifacts']['original_index'],'supplement_field_qa':audit,
        'live_requests':0,'scope':'Additional old actual captures; members and ratings retained, never fresh network observations'}
    return rows,qa,page_by_id
