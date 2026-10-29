"""Import/reopen a PAGE queue without changing the sealed plan or task clock."""
import hashlib,json,sqlite3
from pathlib import Path
from collections import Counter
from .parallel_page_ledger import ParallelPageLedger
from .budgets import aware_time
from .favorite_budget import effective_budgets
from ..multisource.storage import digest,read_sealed


def load_parallel_checkpoint(runner,plan,authorization,path,*,now):
    store=runner.store;run=store.get_run(plan['run_id']);path=Path(path)
    if run['plan']!=plan:raise ValueError('IMMUTABLE_RUN_PLAN_MISMATCH')
    requests=store.requests(plan['run_id']);receipts=list(store.iter_responses(plan['run_id']))
    if any(r['status']=='RESERVED' for r in requests):raise ValueError('AMBIGUOUS_UNFINISHED_REQUESTS_NO_AUTO_RETRY')
    if any(r['kind']!='PAGE' for r in requests):raise ValueError('PAGE_ONLY_CHECKPOINT_REQUIRED')
    if {r['request_id'] for r in requests}!={r['request_id'] for r in receipts}:
        raise ValueError('REQUEST_RESPONSE_RECONCILIATION_REQUIRED')
    pages=store.pages(plan['run_id']);urls={p['page_url'] for p in pages}
    if urls!={r['request_url'] for r in receipts}:raise ValueError('SAVED_RESPONSE_REPLAY_REQUIRED')
    for r in receipts:
        if r['access_state']!='NORMAL' or r['status_code']!=200:
            raise ValueError('HISTORICAL_ACCESS_STOP_REQUIRES_HANDLING')
        if hashlib.sha256(Path(r['html_path']).read_bytes()).hexdigest()!=r['sha256']:
            raise ValueError('RESPONSE_EVIDENCE_CORRUPT')
    from .v4_quality import audit_v4
    quality=audit_v4(pages,list(store.iter_observations(plan['run_id'])),plan['sources'],require_live=True)
    if not quality['central_full_dom_review_passed']:
        raise ValueError('CHECKPOINT_DOM_REVIEW_FAILED:'+str(quality['errors'][:2]))
    ref=authorization['task_clock_reference'];clock_path=Path(ref['path'])
    if hashlib.sha256(clock_path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('CLOCK_BYTES_CHANGED')
    clock=read_sealed(clock_path)
    if clock['run_id']!=plan['run_id'] or clock['plan_sha256']!=digest(plan):raise ValueError('CLOCK_BINDING_MISMATCH')
    limits={k:v['PAGE'] for k,v in effective_budgets(plan,authorization,now=now).items()}
    if path.exists():
        db=sqlite3.connect(path)
        try:
            row=db.execute('SELECT run_id,plan_sha256,deadline,page_budget,min_interval,prior_consumed,prior_last_start,family_limits,prior_family_consumed,cooldown_seconds,prior_category,prior_cooldown_until FROM config WHERE id=1').fetchone()
            count=db.execute('SELECT count(*) FROM claims').fetchone()[0]
        finally:db.close()
        if row is None or row[0]!=plan['run_id'] or row[1]!=digest(plan) or row[2]!=aware_time(clock['deadline']).timestamp():
            raise ValueError('QUEUE_PLAN_CLOCK_BINDING_MISMATCH')
        if row[5]+count!=len(requests):raise ValueError('QUEUE_STORE_SPLIT_TRANSACTION_REVIEW_REQUIRED')
        prior=row[5];last=row[6];family_prior=json.loads(row[8]);category=row[10];until=row[11]
        if json.loads(row[7])!=limits:raise ValueError('QUEUE_FAMILY_LIMITS_CHANGED')
    else:
        prior=len(requests);counts=Counter(r['source_type'] for r in requests)
        family_prior={k:counts[k] for k in limits}
        last=max((aware_time(r['observed_at']).timestamp()+float(r.get('duration_seconds',0)) for r in receipts),default=None)
        progress=store.root/'snapshot_progress.json'
        if progress.exists():
            value=json.loads(progress.read_text(encoding='utf8'))
            if value.get('run_id')!=plan['run_id']:raise ValueError('PROGRESS_RUN_BINDING_MISMATCH')
            last=max(last or 0,aware_time(value['observed_at']).timestamp())
        schedule=run.get('category_schedule') or {}
        category=schedule.get('next') or schedule.get('current')
        until=aware_time(schedule['deadline']).timestamp() if schedule.get('deadline') else 0
    queue=ParallelPageLedger(path,run_id=plan['run_id'],original_plan_sha256=digest(plan),
        deadline=aware_time(clock['deadline']).timestamp(),page_budget=plan['max_pages_per_run'],
        min_interval=plan['request_pacing_seconds'],prior_consumed=prior,prior_last_start=last,
        family_limits=limits,prior_family_consumed=family_prior,cooldown_seconds=plan['category_cooldown_seconds'],
        prior_category=category,prior_cooldown_until=until)
    if not path.exists():raise AssertionError('QUEUE_CREATION_FAILED')
    # Do not turn a prior pending/saved claim into COMMITTED without the audit
    # transition. On reopen those states must be reconciled separately.
    existing={r[0] for r in queue.snapshot()['pages']}
    queue.seed([{'url':p['page_url'],'category':p['source']['collection_category_key'],
        'family':p['source_type'],'state':'COMMITTED','receipt':p['capture_receipt_reference']['sha256']}
        for p in pages if p['page_url'] not in existing])
    receipt_by_url={r['request_url']:r for r in receipts}
    for row in queue.snapshot()['pages']:
        if row[3]!='SAVED':continue
        receipt=receipt_by_url.get(row[0])
        if not receipt or receipt['sha256']!=row[5] or row[0] not in urls:
            raise ValueError('SAVED_QUEUE_RECEIPT_BINDING_REQUIRED')
        queue.replay_committed(row[0],row[5],verify=lambda u,h:
            u in urls and receipt_by_url[u]['sha256']==h)
    return queue
