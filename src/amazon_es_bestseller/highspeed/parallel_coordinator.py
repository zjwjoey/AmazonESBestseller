"""Single-owner PAGE scheduling loop shared by live and isolated execution.

The entry point must hold the original run and global locks, validate the
parallel amendment, initialize runner hooks and provide fresh send admission.
No browser is created or collection started by importing this module.
"""
import asyncio,time,hashlib
from datetime import datetime,timezone
from uuid import uuid4
from ..multisource.storage import immutable_json
from .budgets import utc_text
from .parallel_transport import ThreeWindowTransport
from .parallel_commit import ParallelSnapshotCommitter
from .parallel_faults import FaultReporter
from ..multisource.storage import digest


class ParallelSnapshotCoordinator:
    def __init__(self,runner,plan,queue,gate,*,admit,stop_reason,fixture_mode=False,
                 access_stop_scope='all',reporter=None):
        if access_stop_scope not in {'worker','all'}:raise ValueError('EXPLICIT_ACCESS_STOP_SCOPE_REQUIRED')
        self.runner=runner;self.plan=plan;self.queue=queue;self.gate=gate
        self.admit=admit;self.stop_reason=stop_reason;self.fixture_mode=fixture_mode
        self.stopped=None;self.by_url={};self.active={}
        self.failures=[]
        self.access_observation_saved=False
        self.access_stop_scope=access_stop_scope
        self.reporter=reporter or FaultReporter(runner.store.root,plan['run_id'],digest(plan),
            **({'notify':lambda path:None} if fixture_mode else {}))
        self.fault_workers=set();self.access_workers=set();self.active_items={}

    def fault(self,worker,item,error,stage):
        item=item or {};scope='worker' if stage=='capture' else 'all'
        # A restriction already observed for this worker keeps precedence over
        # a subsequent failure to read its body.
        if worker in self.access_workers and stage=='capture':
            self.queue.disable_worker(worker,'ACCESS_STOP_CAPTURE_UNAVAILABLE')
            item=item or {}
            self.failures.append(self.reporter.record(worker=worker,url=item.get('url'),
                request_id=(item.get('reservation') or {}).get('request_id'),stage='capture_after_access',
                error=error,scope=self.access_stop_scope))
            return
        if worker in self.fault_workers and scope=='worker':return
        self.fault_workers.add(worker)
        if scope=='worker':
            try:self.queue.disable_worker(worker,'CAPTURE_FAILURE')
            except Exception as queue_error:
                self.fault(worker,item,queue_error,'queue');scope='all'
        if scope=='all':self.stop('SHARED_'+stage.upper()+'_FAILURE')
        record=self.reporter.record(worker=worker,url=item.get('url'),
            request_id=(item.get('reservation') or {}).get('request_id'),stage=stage,error=error,scope=scope)
        self.failures.append(record)
        if not record.get('persisted'):self.stop('FAULT_EVIDENCE_WRITE_FAILURE')

    def access_stop(self,worker,state,item=None):
        if worker in self.access_workers:return
        self.access_workers.add(worker)
        if self.access_stop_scope=='all':self.stop(state)
        else:
            try:self.queue.disable_worker(worker,'ACCESS_STOP:'+state,hold_claim=False)
            except Exception as error:self.fault(worker,item,error,'queue');return
        item=item or {}
        record=self.reporter.record(worker=worker,url=item.get('url'),
            request_id=(item.get('reservation') or {}).get('request_id'),stage='access',
            error=RuntimeError(state),scope=self.access_stop_scope)
        self.failures.append(record)
        if not record.get('persisted'):self.stop('FAULT_EVIDENCE_WRITE_FAILURE')

    def stop(self,reason):
        self.stopped=self.stopped or reason
        self.gate.stop()
        restricted=reason in {'BLOCKED','CHALLENGE','RATE_LIMITED'}
        try:
            if restricted and not self.access_observation_saved:
                store=self.runner.store
                path=store.root/'parallel_access_observations'/(uuid4().hex+'.json')
                immutable_json(path,{'kind':'PARALLEL_EARLY_ACCESS_OBSERVATION','state':reason,
                    'run_id':self.plan['run_id'],'observed_at':utc_text(),
                    'response_body_capture_confirmed':False,'fixture_mode':self.fixture_mode})
                store.set_gate(reason,run_id=self.plan['run_id'],reason='PARALLEL_EARLY_ACCESS_OBSERVATION',evidence_path=str(path))
                # An isolated replay of the original plan must never write its
                # referenced production shared gate, even on fixture failure.
                if not self.fixture_mode:
                    self.runner.engine._persist_shared_stop(self.plan,reason,{'evidence_path':str(path)})
                self.access_observation_saved=True
        finally:
            kind=('USER_PAUSED' if reason=='USER_PAUSED' else 'ACCESS_STOP' if restricted else
                  'JOURNAL_FAILURE' if any(s in reason for s in ('FAILURE','FAULT')) else 'CONTROL_STOP')
            try:self.queue.stop(kind)
            except Exception as error:
                self.failures.append(self.reporter.record(worker=None,url=None,request_id=None,
                    stage='queue',error=error,scope='all'))

    def add_work(self,work):
        pages=[]
        for source,url in work:
            if url in self.by_url:continue
            self.by_url[url]=source
            pages.append({'url':url,'category':source['collection_category_key'],'family':source['source_type']})
        self.queue.seed(pages)

    def publish_schedule(self):
        category,deadline=self.queue.scheduler_state()
        if category is None:return
        old=self.runner.store.get_run(self.plan['run_id']).get('category_schedule') or {}
        waiting=deadline>time.time()
        value=({'current':old.get('current'),'next':category,
            'deadline':datetime.fromtimestamp(deadline,timezone.utc).isoformat()} if waiting else
            {'current':category,'next':None,'deadline':None})
        if value!=old:
            self.runner.store.update_run(self.plan['run_id'],category_schedule=value,
                snapshot_phase='CATEGORY_COOLDOWN' if waiting else 'CAPTURING')

    async def run(self,context,*,offline_pages=None):
        if offline_pages is not None and not self.fixture_mode:
            raise ValueError('EXPLICIT_OFFLINE_COORDINATOR_REQUIRED')
        commit=ParallelSnapshotCommitter(self.runner,self.plan,self.queue,stop=self.stop,fixture_mode=self.fixture_mode,
            access_stop_scope=self.access_stop_scope,observed_access=self.access_stop)
        adapter=await ThreeWindowTransport(context,self.gate,admit=self.admit,
            observed_stop=self.stop,captured=commit,offline_pages=offline_pages).open()
        if hasattr(adapter,'configure_faults'):
            adapter.configure_faults(on_fault=self.fault,on_access=self.access_stop,access_stop_scope=self.access_stop_scope)
        try:
            while not self.stopped:
                reason=self.stop_reason()
                if reason:self.stop(reason);break
                for worker in (1,2,3):
                    if self.stopped:break
                    if worker in self.active or worker in self.queue.disabled_workers():continue
                    url=self.queue.claim(worker,eligible=lambda u,f:self.runner.should_collect_source(self.plan,self.by_url[u]))
                    if url is None:continue
                    source=self.by_url[url]
                    key=source['source_id'] if url==source['actual_source_url'] else source['source_id']+'_'+hashlib.sha256(url.encode()).hexdigest()[:12]
                    try:
                        before_reserve=getattr(self.admit,'before_reserve',None)
                        if before_reserve:before_reserve(source,url)
                        reservation=self.runner.store.reserve_request(self.plan['run_id'],{
                            'kind':'PAGE','url':url,'source_id':source['source_id'],'source_type':source['source_type'],
                            'observed_at':utc_text(),'limits':self.plan,'page_key':key,
                            'origin':'OFFLINE_FIXTURE' if self.fixture_mode else 'LIVE_CAPTURE'})
                    except Exception as error:
                        self.fault(worker,{'url':url},error,'reservation');break
                    item={'url':url,'source':source,'page_key':key,'reservation':reservation}
                    self.active_items[worker]=item
                    self.active[worker]=asyncio.create_task(adapter.capture(worker,item))
                done=[w for w,t in self.active.items() if t.done()]
                for worker in done:
                    task=self.active.pop(worker)
                    try:
                        task.result()
                        self.add_work(getattr(self.runner,'_work',[]))
                    except Exception as error:
                        if worker not in self.fault_workers and worker not in self.access_workers:
                            self.fault(worker,self.active_items.get(worker),error,'commit')
                    finally:self.active_items.pop(worker,None)
                self.publish_schedule()
                snapshot=self.queue.snapshot();states=snapshot['pages']
                if not self.active and (self.queue.identity[5]+len(snapshot['claims'])>=self.queue.identity[3]):
                    self.stop('REQUEST_BUDGET_EXHAUSTED');break
                category,_=self.queue.scheduler_state()
                pending_here=any(r[3]=='PENDING' and r[1]==category for r in states)
                if not self.active and not pending_here and any(r[3] in {'HELD','SAVED'} for r in states):
                    self.stop('UNRESOLVED_SAVED_OR_HELD_REQUEST');break
                if not self.active and not any(r[3]=='PENDING' for r in states):break
                if not self.active and len(self.queue.disabled_workers())==3:
                    self.stop('ALL_WORKERS_DISABLED');break
                await asyncio.sleep(.1)
        except Exception as error:
            self.fault(None,None,error,'scheduler')
        finally:
            # Already-sent work must preserve its response even after another
            # window stops; never cancel the durable capture callback.
            if self.active:
                workers=list(self.active)
                results=await asyncio.gather(*self.active.values(),return_exceptions=True)
                for worker,error in zip(workers,results):
                    if isinstance(error,Exception) and worker not in self.fault_workers and worker not in self.access_workers:
                        self.fault(worker,self.active_items.get(worker),error,'commit')
                self.active.clear();self.active_items.clear()
            self.queue.hold_inflight_after_restart()
        return {'stop_reason':self.stopped,'queue':self.queue.snapshot(),'failures':self.failures}
