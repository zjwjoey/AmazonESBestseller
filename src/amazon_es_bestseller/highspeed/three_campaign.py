"""Merge separately authorized, re-audited fresh batches from one campaign.

No prior T2/historical replay is a campaign seed. Batch receipts remain in their
original stores and every supplied batch receives full DOM/receipt review again.
"""
import csv,hashlib,json,sqlite3,uuid
from pathlib import Path
from copy import deepcopy
from ..multisource.storage import immutable_json,immutable_bytes,canonical_bytes
from .three_navigation import CONTRACT,FAMILIES,build_navigation_tree
from .three_contract import validate_three_plan
from .three_metrics import measure_three,qualify_three
from .three_report import completeness_three
from .three_release import release_signals,annotate_release
from .v4_capacity import audit_capacity
from .v4_quality import build_candidates_v4
from .v4_workflows import csv_bytes
from .v4_supplement import _read


def aggregate_campaign(batch_references,output):
    if not batch_references:raise ValueError('THREE_CAMPAIGN_BATCH_REFERENCES_REQUIRED')
    batches=[];all_rows=[];all_pages=[];sources={};run_ids=set();identity=None;audits=[];evidence={}
    for reference in batch_references:
        pack=_read(reference)
        if pack.get('contract')!=CONTRACT or not pack.get('CSV_JSONL_independently_verified'):
            raise ValueError('THREE_ONLY_FORMAL_CAMPAIGN_BATCH_REPORTS_ALLOWED')
        execution=pack['execution'];plan=execution['plan'];validate_three_plan(plan)
        current=(plan['campaign_id'],plan['campaign_created_at'],plan['task_reference'])
        if identity is None:identity=current
        if current!=identity or plan['run_id'] in run_ids:
            raise ValueError('THREE_SAME_CAMPAIGN_UNIQUE_BATCHES_REQUIRED')
        run_ids.add(plan['run_id']);directory=Path(reference['path']).parent
        for f in pack['files']:
            if Path(f['name']).name!=f['name'] or hashlib.sha256((directory/f['name']).read_bytes()).hexdigest()!=f['sha256']:
                raise ValueError('THREE_BATCH_EXPORT_SHA_MISMATCH')
        root=Path(execution['run_directory']).parent.parent
        db=root/'index.sqlite3';connection=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            pages=[{**json.loads(r[0]),'page_key':r[1],'journal_path':r[2]} for r in connection.execute(
                'SELECT data_json,page_key,journal_path FROM pages WHERE run_id=?',(plan['run_id'],))]
            stored=[json.loads(r[0]) for r in connection.execute('SELECT data_json FROM observations WHERE run_id=? ORDER BY rowid',(plan['run_id'],))]
        finally:connection.close()
        rows=[json.loads(line) for line in (directory/'全部原始商品观察.jsonl').read_text('utf8').splitlines()]
        views={r['observation_id']:r for r in rows}
        if len(views)!=len(rows) or len(rows)!=len(stored) or set(views)!={r['observation_id'] for r in stored}:
            raise ValueError('THREE_BATCH_JSONL_OBSERVATION_MISMATCH')
        enriched={'possible_duplicate_key','target_group_key','target_eligible','url_identity_status','final_product_url',
            'current_normalizer_version','possible_duplicate_key_version','source_evidence_eligible','dedup_status'}
        for raw in stored:
            if any(views[raw['observation_id']].get(k)!=v for k,v in raw.items() if k not in enriched):
                raise ValueError('THREE_BATCH_STORED_SOURCE_FIELD_MISMATCH')
        audit=audit_capacity(pages,rows,plan['sources'],execution,three_source=True)
        metrics=measure_three(rows,plan['run_id'])
        if not audit['diagnostic_passed'] or audit!=pack['receipt_audit'] or metrics!=pack['metrics']:
            raise ValueError('THREE_BATCH_CENTRAL_DOM_OR_RECEIPT_REVALIDATION_FAILED')
        all_rows.extend(rows);all_pages.extend(pages)
        for s in plan['sources']:
            if s['source_id'] not in sources:sources[s['source_id']]=deepcopy(s)
            else:
                old=sources[s['source_id']]
                if any(old[k]!=s[k] for k in ('actual_source_url','source_type','source_subtype')):
                    raise ValueError('THREE_CROSS_BATCH_SOURCE_CONFLICT')
                old['page_urls']=list(dict.fromkeys([*old['page_urls'],*s['page_urls']]))
        raw_index=json.loads((directory/'原始证据索引.json').read_text('utf8'))
        for item in raw_index['files']:
            path=Path(item['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                raise ValueError('THREE_BATCH_ORIGINAL_EVIDENCE_SHA_MISMATCH')
            evidence[str(path)]=item
        evidence[reference['path']]=reference
        audits.append({'run_id':plan['run_id'],'audit':audit,'metrics':metrics,'reference':reference})
        batches.append(pack)
    if len({r['observation_id'] for r in all_rows})!=len(all_rows):raise ValueError('THREE_DUPLICATE_CROSS_BATCH_OBSERVATION_IDS')
    first=batches[0]['run_id'];metrics=measure_three(all_rows,first,allowed_run_ids=run_ids)
    for own in metrics['sources'].values():
        own['sample_new_groups_per_hour']=None;own['rate_scope']='Use independent batch rates; inter-batch pauses are not active collection.'
    _,qualified,_,_=qualify_three(all_rows,first,allowed_run_ids=run_ids)
    tree=build_navigation_tree(all_pages,list(sources.values()))
    complete={'batches':[{'run_id':p['run_id'],'report':json.loads((Path(ref['path']).parent/'类目采集完整性报告.json').read_text('utf8'))}
        for p,ref in zip(batches,batch_references)],'global_source_completeness':'UNKNOWN','global_tree_complete':False}
    accepted=metrics['target_met'];acceptance='ACCEPTED — THREE_SOURCE_5001_CANDIDATES_QA_PASSED' if accepted else 'NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET'
    artifacts={};expected={}
    release=release_signals(all_rows)
    for kind in FAMILIES:
        own=annotate_release(build_candidates_v4([r for r in qualified if r['source_type']==kind]),release);name=kind+'_商品候选.csv'
        artifacts[name]=csv_bytes(own);expected[name]=own
    name='三来源统一去重候选主表.csv';expected[name]=annotate_release(build_candidates_v4(qualified),release);artifacts[name]=csv_bytes(expected[name])
    artifacts.update({'全部原始商品观察.jsonl':b''.join(canonical_bytes(r)+b'\n' for r in all_rows),
        'Amazon类目导航树.json':canonical_bytes(tree),'类目采集完整性报告.json':canonical_bytes(complete),
        '来源去重与SKU身份复核报告.json':canonical_bytes(metrics),
        '评分_评价数_月购买量质量报告.json':canonical_bytes({'sources':metrics['sources'],'all_batches_DOM_QA_passed':True,
            'new_release_signal_evidence':release}),
        '采集速度与请求成本报告.json':canonical_bytes({'batch_reports':audits,
            'actual_requests':sum(a['audit']['actual_requests'] for a in audits),'actual_PAGE':sum(a['audit']['actual_PAGE'] for a in audits),
            'rate_scope':'Per-batch capture windows; do not divide inherited or duplicate totals by last-batch time.'}),
        '原始证据索引.json':canonical_bytes({'files':list(evidence.values()),'batch_references':batch_references})})
    text=['# 三来源专项多批次验收','',acceptance,'',f'专项：{identity[0]}。已复审批次：{len(batches)}。']
    for kind in FAMILIES:
        own=metrics['sources'][kind];text.append(f'- {kind}：{own["E_qualified_review_groups"]}个合格保守组；缺口{own["shortfall"]}。')
    text += ['',f'全局保守组：{metrics["F_global_conservative_review_groups"]}。',
        '独立可购买SKU身份尚未验证，已确认数量为0。来源范围与全站完整性未知。',
        '只统计同一专项内重新核验过的真实LIVE批次；旧T2、历史3万及重放记录均未用于补足。']
    artifacts['最终验收报告.md']=('\n'.join(text)+'\n').encode('utf8')
    directory=Path(output)/('three_source_campaign_'+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    for name,body in artifacts.items():immutable_bytes(directory/name,body)
    for name,wanted in expected.items():
        with (directory/name).open(encoding='utf-8-sig',newline='') as fh:actual=list(csv.DictReader(fh))
        if len(actual)!=len(wanted) or {r['normalized_product_url'] for r in actual}!={r['normalized_product_url'] for r in wanted}:
            raise ValueError('THREE_CAMPAIGN_INDEPENDENT_CSV_MISMATCH')
        for a,w in zip(actual,wanted):
            if a['target_review_group']!=w['target_review_group'] or json.loads(a['source_memberships'])!=w['source_memberships']:
                raise ValueError('THREE_CAMPAIGN_MEMBERSHIP_OR_GROUP_MISMATCH')
    if [json.loads(line) for line in (directory/'全部原始商品观察.jsonl').read_text('utf8').splitlines()]!=all_rows:
        raise ValueError('THREE_CAMPAIGN_INDEPENDENT_JSONL_MISMATCH')
    immutable_json(directory/'manifest.json',{'contract':CONTRACT,'campaign_id':identity[0],'batch_references':batch_references,
        'accepted':accepted,'acceptance':acceptance,'metrics':metrics,'all_supplied_batches_reaudited':True,
        'CSV_JSONL_independently_verified':True,'global_source_completeness':'UNKNOWN',
        'files':[{'name':name,'sha256':hashlib.sha256((directory/name).read_bytes()).hexdigest()} for name in artifacts]})
    return {'directory':str(directory),'accepted':accepted,'acceptance':acceptance,'metrics':metrics}
