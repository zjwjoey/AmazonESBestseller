"""Fresh original-plan admission at reservation and actual document dispatch."""
from copy import deepcopy
from .budgets import budget_reason,aware_time,madrid_day
from .v4_authorization import validate_v4_authorization
from .authorization import authorize_request
from .snapshot_control import pause_pending
from .task_clock import request_stop_reason
from .three_contract import family_budget_for_store
from .favorite_budget import effective_budgets


class ParallelAdmission:
    def __init__(self,runner,plan,authorization,*,clock):
        self.runner=runner;self.store=runner.store;self.plan=plan
        self.authorization=authorization;self.clock=clock

    def _common(self,source,url,*,check_source_minimum=True):
        now=aware_time(self.clock());p=self.plan;a=self.authorization
        validate_v4_authorization(p,a,self.runner.engine._gate(p),now=now,store_root=self.store.root)
        if pause_pending(self.store.root,p):raise ValueError('USER_PAUSED')
        reason=request_stop_reason(p,a,self.store,now=now)
        if reason:raise ValueError(reason)
        authorize_request(url,source,a,kind='PAGE')
        if check_source_minimum and not self.runner.should_collect_source(p,source):raise ValueError('SOURCE_MINIMUM_ALREADY_MET')
        if self.runner.target_stop_reason(p):raise ValueError('SNAPSHOT_TARGET_REACHED')
        return now

    def before_reserve(self,source,url):
        now=self._common(source,url)
        reason=family_budget_for_store(self.store,self.plan,source,'PAGE',authorization=self.authorization,now=now)
        if not reason:
            reason=budget_reason(self.plan,self.store.budget_counters(self.plan['run_id']),now=now,
                started_at=self.store.get_run(self.plan['run_id'])['started_at'],authorization=self.authorization)
        if reason:raise ValueError(reason)

    def __call__(self,worker,item):
        # Already charged work can finish within its original cap after another
        # worker reaches this family's minimum. New claims are skipped instead.
        now=self._common(item['source'],item['url'],check_source_minimum=False);p=self.plan
        rows=self.store.requests(p['run_id']);own=[r for r in rows if r['request_id']==item['reservation']['request_id']]
        if (len(own)!=1 or own[0]['status']!='RESERVED' or own[0]['url']!=item['url']
                or own[0]['source_id']!=item['source']['source_id'] or own[0]['kind']!='PAGE'
                or own[0]['plan_sha256']!=item['reservation']['plan_sha256']):
            raise ValueError('EXACT_UNSENT_DURABLE_RESERVATION_REQUIRED')
        if madrid_day(now)!=own[0]['local_day']:raise ValueError('RESERVED_DAY_CHANGED_REVIEW_REQUIRED')
        # This request is already charged. Exclude only itself from admission,
        # retaining every other window's reservations in all budget counts.
        counters=deepcopy(self.store.budget_counters(p['run_id']))
        counters['requests']-=1;counters['page_requests']-=1
        day=madrid_day(now);counters['requests_by_day'][day]-=1
        reason=budget_reason(p,counters,now=now,started_at=self.store.get_run(p['run_id'])['started_at'],authorization=self.authorization)
        limits=effective_budgets(p,self.authorization,now=now)[item['source']['source_type']]
        family=[r for r in rows if r['request_id']!=own[0]['request_id'] and r['source_type']==item['source']['source_type']]
        if len(family)>=limits['requests'] or sum(r['kind']=='PAGE' for r in family)>=limits['PAGE']:
            raise ValueError('SOURCE_FAMILY_BUDGET_EXHAUSTED')
        if reason:raise ValueError(reason)
        return True
