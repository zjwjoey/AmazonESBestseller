"""Explicit parallel PAGE entry. Check never launches a browser or writes source DB."""
import argparse,asyncio,json
from pathlib import Path
from uuid import uuid4
from ..multisource.storage import file_lock,immutable_json,digest
from .budgets import utc_text,aware_time
from .store import RunStore,ReadOnlyRunStore
from .page_snapshot import CurrentPageSnapshotRunner
from .parallel_preflight import check_parallel_start,verify_isolated_acceptance
from .parallel_checkpoint import load_parallel_checkpoint
from .parallel_admission import ParallelAdmission
from .parallel_coordinator import ParallelSnapshotCoordinator
from .parallel_dispatch_gate import AsyncDispatchGate
from .snapshot_control import release_pause,pause_pending


def load(path):return json.loads(Path(path).read_text(encoding='utf8'))


async def execute(runner,plan,auth,queue,profile,*,headless):
    from playwright.async_api import async_playwright
    admission=ParallelAdmission(runner,plan,auth,clock=runner.engine.clock)
    gate=AsyncDispatchGate(interval=plan['request_pacing_seconds'],deadline=queue.identity[2],
        prior_next_start=queue.pacing_deadline(),persist_next_start=queue.persist_actual_dispatch_deadline)
    def reason():
        if pause_pending(runner.store.root,plan):return 'USER_PAUSED'
        if runner.engine._gate(plan).get('state')!='NORMAL':return 'GLOBAL_ACCESS_GATE_BLOCKED'
        from .task_clock import request_stop_reason
        return request_stop_reason(plan,auth,runner.store,now=runner.engine.clock()) or runner.target_stop_reason(plan)
    runner._record_approval(plan,auth)
    runner.mark_run_running(plan)
    work=runner.page_work(plan)
    coordinator=ParallelSnapshotCoordinator(runner,plan,queue,gate,admit=admission,stop_reason=reason)
    coordinator.add_work(work)
    if any(r[3] in {'HELD','SAVED','RESERVED'} for r in queue.snapshot()['pages']):
        raise ValueError('QUEUE_REVIEW_REQUIRED_BEFORE_BROWSER_START')
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(profile),headless=headless,service_workers='block')
        try:return await coordinator.run(context)
        finally:await context.close()


def _main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('check','resume'))
    for name in ('plan','authorization','amendment','manifest','root','code-root'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--profile-dir');parser.add_argument('--live',action='store_true')
    parser.add_argument('--alias-reviews')
    parser.add_argument('--release-pause',action='store_true');parser.add_argument('--headful',action='store_true')
    args=parser.parse_args(argv)
    plan=load(args.plan);auth=load(args.authorization);amendment=load(args.amendment);manifest=load(args.manifest)
    if args.operation=='resume' and (not args.live or not args.release_pause or not args.profile_dir):
        raise ValueError('EXPLICIT_LIVE_RESUME_PROFILE_AND_PAUSE_RELEASE_REQUIRED')
    if args.operation=='resume' and amendment.get('acceptance_status')!='ISOLATED_ACCEPTANCE_PASSED':
        raise ValueError('VERIFIED_ISOLATED_ACCEPTANCE_REQUIRED_BEFORE_LIVE')
    if args.operation=='resume':verify_isolated_acceptance(plan,amendment,manifest)
    if args.operation=='check':
        store=ReadOnlyRunStore(args.root)
        try:
            return check_parallel_start(CurrentPageSnapshotRunner(store),plan,auth,amendment,manifest,
                code_root=args.code_root,now=aware_time(None))
        finally:store.close()
    profile=Path(args.profile_dir).resolve()
    if str(profile)!=amendment.get('profile_dir'):raise ValueError('EXACT_ORIGINAL_PROFILE_BINDING_REQUIRED')
    store=RunStore(args.root);runner=CurrentPageSnapshotRunner(store)
    try:
        with store.run_lock(plan['run_id']),file_lock(Path(plan['shared_access_gate_path']).parent/'highspeed_live.lock'):
            check_parallel_start(runner,plan,auth,amendment,manifest,code_root=args.code_root,now=runner.engine.clock())
            queue=load_parallel_checkpoint(runner,plan,auth,store.root/'parallel_queue.sqlite',now=runner.engine.clock())
            if queue.snapshot()['config'][0] not in {None,'USER_PAUSED'}:
                raise ValueError('QUEUE_STOP_REQUIRES_SEPARATE_HUMAN_HANDLING')
            # Initial scope includes immutable admitted navigation; unresolved
            # aliases must be reviewed before any normal-UI preflight requests.
            work=runner.page_work(plan)
            queue.seed([{'url':u,'category':s['collection_category_key'],'family':s['source_type']} for s,u in work])
            if args.alias_reviews:
                from .parallel_alias_review import apply_alias_reviews
                apply_alias_reviews(queue,plan,load(args.alias_reviews))
            if any(r[3] in {'HELD','SAVED','RESERVED'} for r in queue.snapshot()['pages']):
                raise ValueError('QUEUE_REVIEW_REQUIRED_BEFORE_BROWSER_START')
            from ..access.browser import BrowserSession
            from .delivery_preflight import verify_session_delivery
            directory=store.root/'delivery_preflight'/uuid4().hex
            with BrowserSession(headless=not args.headful,profile_dir=str(profile)) as session:
                try:verify_session_delivery(session,directory,profile_dir=profile)
                except Exception:
                    diagnostics=load(directory/'delivery_gate.json')
                    state=diagnostics.get('access_status','UNKNOWN')
                    if state!='NORMAL':
                        store.set_gate(state,run_id=plan['run_id'],evidence_path=str(directory/'delivery_page.html'))
                        runner.engine._persist_shared_stop(plan,state,{'evidence_path':str(directory/'delivery_page.html')})
                    raise
            check_parallel_start(runner,plan,auth,amendment,manifest,code_root=args.code_root,now=runner.engine.clock())
            immutable_json(store.root/'runs'/plan['run_id']/('parallel_'+digest(amendment)[:16]+'.json'),amendment)
            release_pause(store.root,plan)
            if queue.snapshot()['config'][0]=='USER_PAUSED':queue.acknowledge_user_resume(store.root)
            failure=None
            try:
                result=asyncio.run(execute(runner,plan,auth,queue,profile,headless=not args.headful))
            except BaseException as exc:
                failure=exc
                result={'stop_reason':'PARALLEL_EXECUTION_FAILURE:'+type(exc).__name__+':'+str(exc)}
            # Persist a terminal, unaccepted state before potentially lengthy
            # final audit. A review failure must not leave a dead run RUNNING.
            store.update_run(plan['run_id'],status='STOPPED',stop_reason=result['stop_reason'] or 'SOURCE_SCOPE_EXHAUSTED',
                finished_at=utc_text(),quality_gate_passed=False)
            from .v4_quality import audit_v4
            from .three_metrics import measure_three
            from .snapshot_review import snapshot_review_fields,publish_snapshot_review
            pages=store.pages(plan['run_id']);rows=list(store.iter_observations(plan['run_id']))
            qa=audit_v4(pages,rows,plan['sources'],require_live=True)
            run=store.update_run(plan['run_id'],status='STOPPED',stop_reason=result['stop_reason'] or 'SOURCE_SCOPE_EXHAUSTED',
                finished_at=utc_text(),qa=qa)
            metrics=measure_three(rows,plan['run_id'],target_policy=plan['target_policy'])
            publish_snapshot_review(store,plan['run_id'],snapshot_review_fields(run,metrics,qa,len(pages)))
            if failure is not None:raise failure
            return {'stop_reason':result['stop_reason'],'quality':qa,'metrics':metrics}
    finally:store.close()


def main(argv=None):
    from .v4_authorization import prerequisite_review_session
    with prerequisite_review_session():return _main(argv)


if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False))
