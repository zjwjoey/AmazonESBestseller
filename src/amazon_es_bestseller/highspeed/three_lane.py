"""Three local-processing lanes, one conservative metered request dispatcher.

Browser objects stay on their creating thread. The exclusive run/live locks are
held by the parent for the complete browser lifetime, never removed per lane.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from queue import Queue, Empty
from threading import Barrier, RLock, get_ident
from pathlib import Path
import copy

from .three_runner import V4ThreeSourceRunner
from .v4_runner import V4T1Runner
from .runner import HighSpeedRunner
from .budgets import utc_text, aware_time
from ..multisource.storage import digest


class OwnerThreadBroker:
    """No worker may touch Sync Playwright; callbacks execute on the owner."""
    METHODS = frozenset({'fetch_page','fetch_ajax','prepare_ajax_descriptor',
        'acknowledge_native_response','abort_held_native_routes','cancel_dynamic'})

    def __init__(self, transports):
        if len(transports) != 3 or len({id(t) for t in transports}) != 3:
            raise ValueError('THREE_DISTINCT_PAGE_TRANSPORTS_REQUIRED')
        self.owner = get_ident()
        self.transports = tuple(transports)
        self.queue = Queue()
        self.closed = False

    def call(self, lane, method, *args, **kwargs):
        if method not in self.METHODS or type(lane) is not int or not 0 <= lane < 3:
            raise ValueError('UNAPPROVED_LANE_BROWSER_OPERATION')
        if self.closed:
            raise ValueError('THREE_LANE_BROKER_CLOSED')
        future = Future()
        self.queue.put((lane, method, args, kwargs, future))
        return future.result()

    def pump(self, timeout=.02):
        if get_ident() != self.owner:
            raise RuntimeError('PLAYWRIGHT_OWNER_THREAD_REQUIRED')
        try:
            lane, method, args, kwargs, future = self.queue.get(timeout=timeout)
        except Empty:
            return False
        try:
            future.set_result(getattr(self.transports[lane], method)(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)  # preserve the actual exception/diagnostics
        return True

    def close(self):
        self.closed = True
        while True:
            try: *_, future = self.queue.get_nowait()
            except Empty: break
            future.set_exception(ValueError('THREE_LANE_BROKER_CLOSED'))

    def proxy(self, lane):
        broker = self
        class Proxy:
            def __getattr__(self, name):
                if name not in broker.METHODS:
                    raise AttributeError(name)
                return lambda *a, **kw: broker.call(lane, name, *a, **kw)
        return Proxy()


class RequestCoordinator:
    """Serialize check→reserve→fetch→save→Gate, including family budgets.

    Local parsers may overlap. Network transactions and SQLite mutations never
    overlap. A failed request seals its receipt/Gate before another can enter.
    """
    def __init__(self):
        self.lock = RLock()
        self.stop_reason = None
        self.last_request = None
        self.generation = 0

    def stop(self, reason):
        with self.lock:
            if self.stop_reason is None: self.stop_reason = str(reason) or 'THREE_LANE_UNEXPECTED_STOP'

    def request(self, engine, *args, **kwargs):
        with self.lock:
            if self.stop_reason:
                raise ValueError(self.stop_reason)
            # Caller-supplied lane-local timestamps cannot weaken global pacing.
            args = list(args)
            actual = engine.store.latest_request_time()
            args[7] = max(filter(None, [self.last_request,
                aware_time(actual) if actual else None]), default=None)
            try:
                result = HighSpeedRunner._request(engine, *args, **kwargs)
                self.last_request = result[2]  # completion is conservative vs start
                return result
            except BaseException as exc:
                if not str(exc).startswith(('THREE_SOURCE_REQUEST_BUDGET_EXHAUSTED:',
                    'THREE_SOURCE_PAGE_BUDGET_EXHAUSTED:', 'THREE_SOURCE_NATIVE_BUDGET_EXHAUSTED:')):
                    self.stop_reason = self.stop_reason or str(exc) or type(exc).__name__
                raise


class LaneStore:
    """Worker-owned connection; serialize writes whose cap checks precede SQL."""
    def __init__(self, store, coordinator):
        self.actual = store
        self.coordinator = coordinator
    def __getattr__(self, name):
        method = getattr(self.actual, name)
        if name in {'commit_page', 'update_run', 'append_event'}:
            def locked(*a, **kw):
                with self.coordinator.lock:
                    result=method(*a, **kw)
                    if name=='commit_page':self.coordinator.generation+=1
                    return result
            return locked
        return method


class _LaneRunner(V4ThreeSourceRunner):
    def __init__(self, store, coordinator, sources, barrier, *, clock=None, sleep=None):
        super().__init__(store,clock=clock,sleep=sleep)
        self.coordinator=coordinator
        self.sources=sources
        self.barrier=barrier
        self._metrics_generation=-1
        original=self.engine
        original._request=lambda *a, **kw: coordinator.request(original,*a,**kw)

    def execution_work(self, plan):
        return [(s,u) for s in self.sources for u in s['page_urls']]

    def execution_started(self, plan):
        self.barrier.wait(timeout=120)

    def _record_approval(self, plan, authorization):
        pass  # one parent record, before workers/reservations exist

    def finish_execution(self, plan, stop):
        if stop: self.coordinator.stop(stop)
        return {'stop_reason': stop}

    def target_stop_reason(self, plan):
        # A lane cannot use another lane's stale page-boundary target cache.
        with self.coordinator.lock:
            if self._metrics_generation!=self.coordinator.generation:
                self._global_target_met=None
            result=super().target_stop_reason(plan)
            self._metrics_generation=self.coordinator.generation
            return result

    def remaining_page_work(self, plan, work, committed, current_url):
        with self.coordinator.lock:
            complete={p['page_url'] for p in self.store.pages(plan['run_id'])
                if p.get('execution_status')=='APPROVED_SOURCE_PAGE_SCOPE_EXECUTED'}
            all_work=[(s,u) for s in plan['sources'] for u in s['page_urls']]
            return super().remaining_page_work(plan,all_work,complete,current_url)

    def after_page(self, *args):
        with self.coordinator.lock:
            return super().after_page(*args)


def lane_sources(plan):
    sources=plan['sources']
    ids=[s['source_id'] for s in sources]
    urls=[u for s in sources for u in s['page_urls']]
    if len(ids)!=len(set(ids)) or len(urls)!=len(set(urls)):
        raise ValueError('THREE_LANE_DUPLICATE_SOURCE_OR_PAGE')
    return [sources[i::3] for i in range(3)]


def validate_lane_approval(plan, authorization, approval, root, now):
    """Explicit mode sidecar; the frozen serial request envelope stays intact."""
    if (approval.get('approved') is not True or approval.get('mode')!='THREE_LOCAL_LANES_ONE_REQUEST_DISPATCHER'
        or type(approval.get('lanes')) is not int or approval.get('lanes')!=3 or approval.get('run_id')!=plan['run_id']
        or approval.get('plan_sha256')!=digest(plan)
        or approval.get('authorization_sha256')!=digest(authorization)
        or Path(approval.get('store_root','')).resolve()!=Path(root).resolve()
        or approval.get('preserve_cumulative_budgets') is not True
        or approval.get('global_request_pacing_seconds')!=plan['request_pacing_seconds']
        or plan['request_pacing_seconds']<3
        or not approval.get('approval_id') or not approval.get('reviewed_by')
        or not aware_time(approval['reviewed_at'])<=now<aware_time(approval['expires_at'])):
        raise ValueError('EXACT_REVIEWED_THREE_LANE_EXECUTION_APPROVAL_REQUIRED')
    lane_sources(plan)


def run_three_lanes(store, plan, authorization, transports, approval, *, preheld_lock=False, clock=None, sleep=None):
    """Execute three source chains and drain before a single unchanged audit.

    Caller owns the one persistent browser context and both lifetime locks.
    No new profile, retry, probe, plan mutation or per-lane run is created.
    """
    from .three_contract import validate_three_plan
    from .v4_authorization import validate_v4_authorization, _PREREQUISITES
    from .store import RunStore
    parent=V4ThreeSourceRunner(store,clock=clock,sleep=sleep)
    if not preheld_lock:
        raise ValueError('PARENT_RUN_AND_GLOBAL_LIVE_LOCKS_REQUIRED')
    validate_three_plan(plan)
    validate_lane_approval(plan,authorization,approval,store.root,parent.engine.clock())
    validate_v4_authorization(plan,authorization,parent.engine._gate(plan),now=parent.engine.clock(),store_root=store.root)
    run=store.get_run(plan['run_id'])
    if run['plan']!=plan or any(r['status']=='RESERVED' for r in store.requests(plan['run_id'])):
        raise ValueError('SAME_IMMUTABLE_RUN_WITHOUT_AMBIGUOUS_REQUEST_REQUIRED')
    parent._record_approval(plan,authorization)
    store.update_run(plan['run_id'],status='RUNNING')
    store.append_event(plan['run_id'],{'kind':'THREE_LANE_EXECUTION_START',
        'approval':approval,'request_dispatchers':1,'local_processing_lanes':3,
        'final_review_deferred_not_passed':True})
    coordinator=RequestCoordinator()
    barrier=Barrier(3)
    broker=OwnerThreadBroker(transports)
    assignments=lane_sources(plan)
    warm=_PREREQUISITES.get()

    def worker(lane):
        actual=None
        token=_PREREQUISITES.set(copy.copy(warm) if warm is not None else {})
        try:
            actual=RunStore(store.root)
            runner=_LaneRunner(LaneStore(actual,coordinator),coordinator,assignments[lane],barrier,clock=clock,sleep=sleep)
            runner.engine._admission_guard=lambda:validate_lane_approval(
                plan,authorization,approval,actual.root,runner.engine.clock())
            return V4T1Runner.run(runner,plan,authorization=authorization,
                transport=broker.proxy(lane),resume=True,preheld_lock=True)
        except BaseException as exc:
            coordinator.stop(str(exc));barrier.abort()
            return {'stop_reason':str(exc),'exception_type':type(exc).__name__}
        finally:
            if actual is not None:actual.close()
            _PREREQUISITES.reset(token)

    try:
        with ThreadPoolExecutor(max_workers=3,thread_name_prefix='v4-lane') as pool:
            futures=[pool.submit(copy_context().run,worker,i) for i in range(3)]
            while not all(f.done() for f in futures):broker.pump()
            results=[f.result() for f in futures]
    finally:
        broker.close()
        for transport in transports:
            transport.cancel_dynamic();transport.abort_held_native_routes()
    reason=coordinator.stop_reason or 'V4_T3_LOADING_SCOPE_EXECUTED_INCOMPLETE_PROOFS'
    quality={'central_full_dom_review_passed':False,'errors':[],
        'review_status':'PENDING_COMBINED_CAPACITY_REVIEW'}
    store.update_run(plan['run_id'],status='STOPPED',stop_reason=reason,
        quality_gate_passed=False,qa=quality,finished_at=utc_text())
    store.append_event(plan['run_id'],{'kind':'THREE_LANES_DRAINED_BEFORE_REVIEW','lanes':results})
    result={'run':store.get_run(plan['run_id']),'summary':store.summary(plan['run_id']),'quality':quality}
    return parent.finish_three_source(plan,result)
