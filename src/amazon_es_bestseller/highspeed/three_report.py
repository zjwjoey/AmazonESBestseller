"""Receipt/DOM-gated three-source exports and independent interchange checks."""
import csv
import hashlib
import json
from pathlib import Path
import uuid
from collections import Counter
from bs4 import BeautifulSoup
from ..multisource.storage import immutable_json, immutable_bytes, canonical_bytes, read_sealed
from .three_contract import validate_three_plan
from .three_navigation import FAMILIES, CONTRACT, build_navigation_tree, discover_navigation
from .three_metrics import measure_three,qualify_three
from .three_release import release_signals,annotate_release
from .v4_quality import build_candidates_v4
from .v4_workflows import csv_bytes
from .v4_capacity import audit_capacity,execution_for_store


def completeness_three(pages,sources,loading,*,tree=None,observations=()):
    by_id={s['source_id']:s for s in sources};visited={p['page_url'] for p in pages};result=[]
    for p in pages:
        s=by_id[p['source_id']];refs=discover_navigation(p,s)
        pending=[r['url'] for r in refs if r['relationship']=='NEXT_PAGE' and r['url'] not in visited]
        finite=[c for c in loading['finite_component_coverage'] if c['source_id']==p['source_id']
            and c['capture_sha256']==p['evidence_sha256'] and c.get('is_current_capture')]
        if s['source_type'] in {'BEST_SELLERS','NEW_RELEASES'}:
            body=Path(p['evidence_path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=p['evidence_sha256']:raise ValueError('THREE_COMPLETENESS_DOM_SHA_MISMATCH')
            soup=BeautifulSoup(body,'lxml')
            grids=soup.select('.p13n-desktop-grid[data-client-recs-list]')
            if len(grids)==1:
                entries=json.loads(grids[0]['data-client-recs-list']);declared=[]
                for entry in entries:
                    raw=(entry.get('metadataMap') or {}).get('render.zg.rank')
                    if isinstance(raw,str) and raw.isascii() and raw.isdigit() and int(raw)>0:declared.append(int(raw))
                valid_declared=len(declared)==len(entries) and len(set(declared))==len(declared) and bool(entries)
                frames={p['evidence_sha256'],*[a['evidence_sha256'] for a in p.get('acp_captures',[])]}
                ranks={r['ranking_position'] for r in observations if r['source_id']==p['source_id']
                    and r['page_url']==p['page_url'] and r['evidence_sha256'] in frames
                    and r.get('is_valid') and r.get('product_title_es') and not r.get('binding_doubts')
                    and r.get('ranking_position') is not None}
                finite.append({'source_id':p['source_id'],'component_type':'LIST_GRID',
                    'capture_sha256':p['evidence_sha256'],'expected_metadata_ranks':declared,
                    'observed_unique_ranks':len(ranks),'missing_ranks':sorted(set(declared)-ranks),
                    'outside_metadata_ranks':sorted(ranks-set(declared)),
                    'status':'COMPLETE_WITHIN_OBSERVED_METADATA' if valid_declared and ranks==set(declared)
                        else 'PARTIAL' if valid_declared else 'UNVERIFIED',
                    'global_category_capacity_confirmed':False})
        if p['status']!='VERIFIED_WITH_PRODUCTS':state=p['status']
        elif finite and all(c['status']=='COMPLETE_WITHIN_OBSERVED_METADATA' for c in finite) and not pending:
            state='COMPLETE_WITHIN_VERIFIED_SCOPE'
        else:state='PARTIAL'
        # Favorites terminal status requires an affirmative disabled-Next DOM
        # marker, never mere absence of a link or a cached result-count label.
        terminal=None
        if s['source_type']=='CUSTOMER_FAVORITES' and s['source_subtype']=='CUSTOMER_FAVORITES_FILTERED_SEARCH':
            soup=BeautifulSoup(Path(p['evidence_path']).read_bytes(),'lxml')
            terminal=soup.select_one('.s-pagination-next.s-pagination-disabled[aria-disabled="true"], .a-pagination .a-last.a-disabled')
            if terminal is not None and not pending and p['status']=='VERIFIED_WITH_PRODUCTS':state='COMPLETE_WITHIN_VERIFIED_SCOPE'
        if p.get('execution_status')!='APPROVED_SOURCE_PAGE_SCOPE_EXECUTED' and state=='COMPLETE_WITHIN_VERIFIED_SCOPE':state='PARTIAL'
        result.append({'source_type':s['source_type'],'source_id':p['source_id'],'page_url':p['page_url'],
            'status':state,'canonical_page_status':p['status'],'evidence_path':p['evidence_path'],
            'evidence_sha256':p['evidence_sha256'],'actual_product_positions':p.get('actual_card_count',0),
            'finite_component_coverage':finite,'unverified_next_URLs':list(dict.fromkeys(pending)),
            'terminal_statement_raw':terminal.get_text(' ',strip=True) if terminal is not None else None,
            'global_category_complete':False,'loading_stop_reason':p.get('loading_stop_reason')})
    tree=tree if tree is not None else build_navigation_tree(pages,sources)
    return {'contract':CONTRACT,'pages':result,'global_source_completeness':'UNKNOWN',
        'approved_PAGE_count':sum(len(s['page_urls']) for s in sources),'visited_exact_PAGE_count':len(visited),
        'by_source':{k:{'page_states':dict(Counter(p['status'] for p in result if p['source_type']==k)),
            'discovered_category_or_theme_nodes':sum(n['source_type']==k and any(t in n['node_kinds'] for t in ('CHILD_CATEGORY','THEME_LINK','DECLARED_SOURCE_CONTEXT')) for n in tree['nodes']),
            'discovered_facet_nodes':sum(n['source_type']==k and 'FACET_LINK' in n['node_kinds'] for n in tree['nodes']),
            'unvisited_nodes':sum(n['source_type']==k and n['visit_status']=='UNVISITED' for n in tree['nodes']),
            'unverified_pagination_count':sum(len(p['unverified_next_URLs']) for p in result if p['source_type']==k),
            'unfinished_finite_components':sum(c['status']!='COMPLETE_WITHIN_OBSERVED_METADATA' for p in result
                if p['source_type']==k for c in p['finite_component_coverage'])} for k in FAMILIES}}


def export_three(store,run_id,output,*,progress=None):
    # A fresh independent export proof, never the collector's warmed session.
    # Within it identical navigation/grid proofs need one DOM review plus byte
    # checks, while central current-run DOM and CSV/JSONL checks still run.
    from .v4_authorization import prerequisite_review_session
    with prerequisite_review_session():
        return _export_three(store,run_id,output,progress=progress)


def _export_three(store,run_id,output,*,progress=None):
    def mark(phase):
        if progress is not None:progress(phase)
    mark('EXPORT_STORE_READ_STARTED')
    run=store.get_run(run_id);plan=run['plan'];validate_three_plan(plan)
    pages=store.pages(run_id);rows=list(store.iter_observations(run_id));sources=plan['sources']
    execution=execution_for_store(store,run_id)
    mark('EXPORT_DOM_RECEIPT_AUDIT_STARTED')
    audit=audit_capacity(pages,rows,sources,execution,three_source=True)
    mark('EXPORT_DOM_RECEIPT_AUDIT_FINISHED')
    metrics=measure_three(rows,run_id,target_policy=plan.get('target_policy'));current,qualified,invalid,excluded=qualify_three(rows,run_id)
    accepted=metrics['target_met'] and audit['diagnostic_passed']
    from .global_target import acceptance as target_acceptance
    acceptance=target_acceptance(metrics,audit['diagnostic_passed'])
    mark('EXPORT_NAVIGATION_AND_COMPLETENESS_STARTED')
    tree=build_navigation_tree(pages,sources)
    completeness=completeness_three(pages,sources,audit['loading_measurements'],tree=tree,observations=rows)
    release=release_signals(rows)
    candidates=annotate_release(build_candidates_v4(qualified),release)
    for candidate in candidates:
        candidate['campaign_id']=plan['campaign_id']
        candidate['export_QA_status']='CENTRAL_DOM_RECEIPTS_PASSED' if audit['diagnostic_passed'] else 'QA_FAILED_DO_NOT_USE'
    mark('EXPORT_SERIALIZATION_STARTED')
    artifacts={}; csv_expected={}
    for kind in FAMILIES:
        own=annotate_release(build_candidates_v4([r for r in qualified if r['source_type']==kind]),release)
        for r in own:r.update(campaign_id=plan['campaign_id'],export_QA_status='CENTRAL_DOM_RECEIPTS_PASSED' if audit['diagnostic_passed'] else 'QA_FAILED_DO_NOT_USE')
        name=kind+'_商品候选.csv';artifacts[name]=csv_bytes(own);csv_expected[name]=own
    name='三来源统一去重候选主表.csv';artifacts[name]=csv_bytes(candidates);csv_expected[name]=candidates
    artifacts['全部原始商品观察.jsonl']=b''.join(canonical_bytes(r)+b'\n' for r in rows)
    artifacts['Amazon类目导航树.json']=canonical_bytes(tree)
    artifacts['类目采集完整性报告.json']=canonical_bytes(completeness)
    artifacts['来源去重与SKU身份复核报告.json']=canonical_bytes(metrics)
    artifacts['评分_评价数_月购买量质量报告.json']=canonical_bytes({'central_quality':audit['central_quality'],
        'new_release_status_counts':dict(Counter(r['release_status'] for r in release.values())),
        'new_release_signal_evidence':release,
        'source_quality':{k:metrics['sources'][k] for k in FAMILIES},'unqualified_observations':len(invalid)})
    performance={'receipt_audit':audit,'sources':{},'collection_and_QA_seconds':audit['elapsed_wall_seconds'],
        'ten_hour_30000_demonstrated':False}
    reservations=store.requests(run_id)
    for kind in FAMILIES:
        qs=[q for q in reservations if q['source_type']==kind];own=metrics['sources'][kind]
        performance['sources'][kind]={'requests':len(qs),'PAGE':sum(q['kind']=='PAGE' for q in qs),
            'native':sum(q['kind']=='ACP' for q in qs),'new_groups_per_10_requests':own['E_qualified_review_groups']*10/len(qs) if qs else None,
            'sample_new_groups_per_hour':own['sample_new_groups_per_hour'],'rate_scope':own['rate_scope']}
    artifacts['采集速度与请求成本报告.json']=canonical_bytes(performance)
    evidence={}
    def collect(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if isinstance(v,str) and (k=='path' or k.endswith('_path')) and Path(v).is_file():
                    if v not in evidence:evidence[v]={'path':v,'sha256':hashlib.sha256(Path(v).read_bytes()).hexdigest()}
                elif isinstance(v,(list,dict)):collect(v)
        elif isinstance(value,list):
            for v in value:collect(v)
    collect(pages);collect(rows);collect(execution)
    root=Path(execution['run_directory'])
    for folder in ('requests','responses','pages'):
        for p in (root/folder).glob('*.json'):
            evidence[str(p)]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            collect(read_sealed(p))
    for p in (store.root/'navigation_discovery_contracts').glob('*.json'):
        evidence[str(p)]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    artifacts['原始证据索引.json']=canonical_bytes({'run_id':run_id,'campaign_id':plan['campaign_id'],
        'files':list(evidence.values()),'receipt_audit_passed':audit['diagnostic_passed']})
    lines=[('# 全局30,000候选验收报告' if plan.get('target_policy') else '# 三来源5,001候选验收报告'),'',acceptance,'',
        f'批次：{run_id}；专项：{plan["campaign_id"]}。','',
        '| 来源 | 有效规范化URL | 合格保守组 | 距5,001缺口 |','|---|---:|---:|---:|']
    for k in FAMILIES:
        m=metrics['sources'][k];lines.append(f'| {k} | {m["D_unique_normalized_URLs"]} | {m["E_qualified_review_groups"]} | {m["shortfall"]} |')
    lines += ['',f'三来源内组数合计：{metrics["E_sum_source_review_groups"]}；全局保守组：{metrics["F_global_conservative_review_groups"]}。',
        '已确认独立可购买SKU：0；身份核验尚未执行。',
        f'完整DOM/收据审计：{"通过" if audit["diagnostic_passed"] else "未通过"}；请求{audit["actual_requests"]}次，PAGE {audit["actual_PAGE"]}次。',
        '数量验收、批准范围完整性、独立可购SKU身份分别判断。Amazon全站及未访问类目完整性未知。',
        '当前批次不代表整个多批次专项已完成；已批准范围以外的导航和Next全部待审。','',
        '审计问题：'+json.dumps(audit['errors'],ensure_ascii=False)]
    if plan.get('target_policy'):
        lines += ['',f'全局目标30,000；实际{metrics["F_global_conservative_review_groups"]}；缺口{metrics["global_shortfall"]}。',
            '上表各来源5,001为诊断指标；本计划数量验收只使用全局合格保守组并集。达到数量不代表源范围完整或SKU身份已确认。']
    artifacts['最终验收报告.md']=('\n'.join(lines)+'\n').encode('utf8')
    mark('EXPORT_WRITE_AND_INDEPENDENT_ROUNDTRIP_STARTED')
    directory=Path(output)/('three_source_report_'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    for name,body in artifacts.items():immutable_bytes(directory/name,body)
    # Re-read both interchanges and compare identities, groups and memberships.
    for name,expected in csv_expected.items():
        with (directory/name).open(encoding='utf-8-sig',newline='') as fh:actual=list(csv.DictReader(fh))
        if len(actual)!=len(expected) or {r['normalized_product_url'] for r in actual}!={r['normalized_product_url'] for r in expected}:
            raise ValueError('THREE_INDEPENDENT_CSV_COUNT_OR_URL_MISMATCH')
        for saved,want in zip(actual,expected):
            if saved['target_review_group']!=want['target_review_group'] or json.loads(saved['source_memberships'])!=want['source_memberships']:
                raise ValueError('THREE_INDEPENDENT_CSV_GROUP_MEMBERSHIP_MISMATCH')
    history=[json.loads(line) for line in (directory/'全部原始商品观察.jsonl').read_text('utf8').splitlines()]
    if history!=rows:raise ValueError('THREE_INDEPENDENT_JSONL_MISMATCH')
    refs=[{'name':name,'sha256':hashlib.sha256((directory/name).read_bytes()).hexdigest()} for name in artifacts]
    manifest={'schema_version':1,'contract':CONTRACT,'run_id':run_id,'campaign_id':plan['campaign_id'],
        'campaign_created_at':plan['campaign_created_at'],'task_reference':plan['task_reference'],
        'execution':execution,'accepted':accepted,'acceptance':acceptance,'metrics':metrics,
        'receipt_audit':audit,'files':refs,'CSV_JSONL_independently_verified':True,'SKU_identity_status':'NOT_VERIFIED'}
    immutable_json(directory/'manifest.json',manifest)
    mark('EXPORT_INDEPENDENT_ROUNDTRIP_FINISHED')
    return {'directory':str(directory),'acceptance':acceptance,'metrics':metrics,'accepted':accepted}
