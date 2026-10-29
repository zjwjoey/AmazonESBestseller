"""PAGE-only current candidate task on the existing serial V4 engine.

This contract does not assert that the legacy eight-source native loading or
capacity stages passed. Native requests remain disabled for this task mode.
"""
from datetime import timedelta
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from ..multisource.storage import atomic_json,immutable_json,sealed,read_sealed,digest
from .global_target import SNAPSHOT_PROFILES,is_global_policy,is_page_policy
from .three_contract import verify_source
from .three_metrics import measure_three
from .v4_runner import V4T1Runner
from .budgets import aware_time,utc_text,budget_reason
from .page_navigation import POLICY,validate_admitted_source,extend_source
from .three_navigation import discover_navigation

CONTRACT='CURRENT_THREE_SOURCE_PAGE_SNAPSHOT_V1'


def prioritize_saved_pages(work,receipts,committed_urls,groups,*,favorites_unmet=False):
    """Replay interrupted, saved pages first inside their existing department."""
    saved={r['request_url'] for r in receipts if r.get('kind')=='PAGE' and r.get('status_code')==200
           and r.get('access_state')=='NORMAL' and r.get('origin')=='LIVE_CAPTURE'}
    return sorted(work,key=lambda pair:(groups.index(pair[0]['collection_category_key']),
        0 if pair[1] in saved and pair[1] not in committed_urls else
        1 if favorites_unmet and pair[0].get('source_type')=='CUSTOMER_FAVORITES' else 2))


def promote_favorite_page(work,current_index,*,minimum_met):
    """One favorite turn after a main page, inside the same department only."""
    if minimum_met:return
    current=work[current_index][0];category=current['collection_category_key']
    pending=[i for i in range(current_index+1,len(work)) if work[i][0]['collection_category_key']==category]
    # Alternate while both are available; never starve the main sources either.
    if current.get('source_type')=='CUSTOMER_FAVORITES' and any(
            work[i][0].get('source_type')!='CUSTOMER_FAVORITES' for i in pending):return
    favorite=next((i for i in pending if work[i][0].get('source_type')=='CUSTOMER_FAVORITES'),None)
    if favorite is not None:work.insert(current_index+1,work.pop(favorite))


def validate_page_snapshot(plan):
    from .global_target import CUSTOM_PROFILE
    policy=plan.get('target_policy') or {}
    if policy.get('profile') in {CUSTOM_PROFILE,'MAIN_PRIORITY_100000_FAVORITES_3000_V1'} and plan.get('max_duration_hours')!=policy.get('total_task_hours'):
        raise ValueError('SNAPSHOT_PLAN_DURATION_MUST_MATCH_POLICY')
    if plan.get('snapshot_field_contract') not in {None, 'CARD_IMAGE_V1'}:
        raise ValueError('UNKNOWN_SNAPSHOT_FIELD_CONTRACT')
    if plan.get('delivery_preflight_contract') not in {None, 'SPAIN_UI_PREFLIGHT_V1'}:
        raise ValueError('UNKNOWN_SNAPSHOT_DELIVERY_PREFLIGHT_CONTRACT')
    if (plan.get('collection_contract')!=CONTRACT or plan.get('stage')!='T1'
            or not is_global_policy(plan.get('target_policy'))
            or not is_page_policy(plan['target_policy'])
            or plan.get('target_unique_links')!=plan['target_policy']['target']
            or plan.get('source_targets')!=plan['target_policy']['source_minima']
            or plan.get('snapshot_metadata_contract')!='SOURCE_CATEGORY_IDENTITY_V1'
            or not plan.get('current_protocol_reference')):
        raise ValueError('EXPLICIT_CURRENT_PAGE_SNAPSHOT_CONTRACT_REQUIRED')
    if type(plan.get('category_cooldown_seconds')) is not int or not 1800<=plan['category_cooldown_seconds']<=86400:
        raise ValueError('PAGE_SNAPSHOT_NORMAL_CATEGORY_COOLDOWN_REQUIRED')
    seen=set();last=None
    for source in plan['sources']:
        verify_source(source)
        if source['source_type'] in {'BEST_SELLERS','NEW_RELEASES'} and urlsplit(source['actual_source_url']).path.rstrip('/') in {'/gp/bestsellers','/gp/new-releases'}:
            raise ValueError('REVIEWED_PHYSICAL_DEPARTMENT_ROOT_REQUIRED')
        key=source.get('collection_category_key');proof=source.get('collection_category_evidence') or {}
        p=Path(proof.get('path','__missing__'))
        if not key or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=proof.get('sha256'):
            raise ValueError('REVIEWED_COLLECTION_CATEGORY_CONTEXT_REQUIRED')
        if source.get('owned_page_navigation')!=POLICY:
            raise ValueError('EXACT_BOUNDED_OWNED_PAGE_POLICY_REQUIRED')
        if key!=last:
            if key in seen:raise ValueError('SERIAL_CATEGORY_GROUPS_MUST_BE_CONTIGUOUS')
            seen.add(key);last=key
    if {s['source_type'] for s in plan['sources']}!={'BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES'}:
        raise ValueError('PAGE_SNAPSHOT_ALL_THREE_FAMILIES_REQUIRED')


class CurrentPageSnapshotRunner(V4T1Runner):
    def before_central_review(self, plan):
        self.store.update_run(plan['run_id'], snapshot_phase='FINAL_DOM_REVIEW')

    def pause_requested(self, plan):
        from .snapshot_control import pause_pending
        if not hasattr(self.store, 'root'):return False
        return pause_pending(self.store.root, plan)

    def run(self,plan,**kwargs):
        validate_page_snapshot(plan)
        self._snapshot_metrics=None
        result=super().run(plan,**kwargs)
        metrics=measure_three(list(self.store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan['target_policy'])
        if getattr(self, '_metrics_index', None) is not None and self._metrics_index.measure() != metrics:
            self.store.append_event(plan['run_id'], {'kind':'SNAPSHOT_INCREMENTAL_ACCOUNTING_MISMATCH',
                'acceptance_uses':'FULL_RECOMPUTATION_ONLY'})
        from .snapshot_review import snapshot_review_fields,publish_snapshot_review
        fields=snapshot_review_fields(self.store.get_run(plan['run_id']),metrics,result['quality'],
            len(self.store.pages(plan['run_id'])))
        run=publish_snapshot_review(self.store,plan['run_id'],fields)
        return {**result,**fields,'run':run}

    def page_scope_executed(self,plan):
        return True

    def mark_run_running(self,plan):
        from .snapshot_review import snapshot_review_fields,publish_snapshot_review
        self._metrics_index = None
        self._favorite_minimum_confirmed = False
        self._target_quantity_confirmed = False
        self._budget_blocked_sources = set()
        from .store import RunStore
        if isinstance(self.store, RunStore) and not self.store._review_projection(plan['run_id']):
            from .snapshot_metrics import SnapshotMetricsIndex, update_from_store
            self._metrics_index = SnapshotMetricsIndex(plan['run_id'], plan['target_policy'])
            self._metrics_cursor = update_from_store(self._metrics_index, self.store, plan['run_id'], 0)
            self._snapshot_metrics = self._metrics_index.measure()
        else:
            self._snapshot_metrics=measure_three(list(self.store.iter_observations(plan['run_id'])),
                plan['run_id'],target_policy=plan['target_policy'])
        run={**self.store.get_run(plan['run_id']),'status':'RUNNING'}
        fields=snapshot_review_fields(run,self._snapshot_metrics,{},len(self.store.pages(plan['run_id'])))
        publish_snapshot_review(self.store,plan['run_id'],{**fields,'status':'RUNNING','snapshot_phase':'CAPTURING'})

    def accept_navigation_frame(self,plan,source,parsed,receipt):
        from .three_runner import V4ThreeSourceRunner
        return V4ThreeSourceRunner.accept_navigation_frame(self,plan,source,parsed,receipt)

    def page_work(self,plan):
        self._approved_roots={s['source_id']:s for s in plan['sources']}
        work=super().page_work(plan)
        self._navigation_dir=self.store.root/'runs'/plan['run_id']/'page_navigation'
        self._navigation_dir.mkdir(parents=True,exist_ok=True)
        for path in sorted(self._navigation_dir.glob('*.json')):
            item=read_sealed(path)
            if item['plan_sha256']!=digest(plan):raise ValueError('PAGE_NAVIGATION_PLAN_BINDING_MISMATCH')
            root=self._approved_roots[item['source']['snapshot_admission_root']]
            validate_admitted_source(item['source'],root,plan['run_id'],digest(plan))
            work.append((item['source'],item['page_url']))
        groups=list(dict.fromkeys(s['collection_category_key'] for s in plan['sources']))
        prior_metrics=self.store.get_run(plan['run_id']).get('snapshot_metrics') or {}
        work=prioritize_saved_pages(work,self.store.iter_responses(plan['run_id']),
            {p['page_url'] for p in self.store.pages(plan['run_id'])},groups,
            favorites_unmet=bool(prior_metrics.get('A_raw_observations')) and not prior_metrics.get(
                'sources',{}).get('CUSTOMER_FAVORITES',{}).get('target_met',False))
        self._work=work
        self.admitted_urls={url for _,url in work}
        self._known_source_ids={s['source_id'] for s,_ in work}
        return work

    def _admit_navigation(self,plan,source,key,parsed):
        page=parsed['committed_page']
        if page.get('http_status')!=200 or page.get('access_status')!='NORMAL':return
        root=self._approved_roots[source.get('snapshot_admission_root',source['source_id'])]
        refs=discover_navigation(page,source)
        refs.sort(key=lambda r:r['relationship']!='NEXT_PAGE')
        additions=[]
        for ref in refs:
            if ref['status']!='PENDING_REVIEW' or ref['url'] in self.admitted_urls:continue
            if ref['relationship']=='NEXT_PAGE' and page['page_url']!=source['page_urls'][-1]:continue
            child=extend_source(source,ref,page['capture_receipt_reference'],root,
                favorite_budget_reference=self._active_authorization.get('favorite_budget_reference'))
            if child is None:continue
            if child['source_id'] not in self._known_source_ids:
                count=len({s['source_id'] for s,_ in self._work if s.get('snapshot_admission_root',s['source_id'])==root['source_id']})
                if count>=POLICY['max_sources_per_department']:continue
            validate_admitted_source(child,root,plan['run_id'],digest(plan))
            item={'plan_sha256':digest(plan),'source':child,'page_url':ref['url'],'admitted_at':utc_text(self.engine.clock())}
            immutable_json(self._navigation_dir/(digest(item)+'.json'),sealed(item))
            self.admitted_urls.add(ref['url']);self._known_source_ids.add(child['source_id'])
            additions.append((child,ref['url']))
        # Breadth first inside one department: visit each approved family before
        # its discovered descendants; never cross a department cooldown early.
        position=max(i for i,(s,u) in enumerate(self._work) if s['collection_category_key']==source['collection_category_key'])
        self._work[position+1:position+1]=additions

    def should_collect_source(self,plan,source):
        if source['source_type'] in getattr(self, '_budget_blocked_sources', set()):return False
        if self._snapshot_metrics is None:
            self._snapshot_metrics=measure_three(list(self.store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan['target_policy'])
        if source['source_type']=='CUSTOMER_FAVORITES' and self._snapshot_metrics['sources']['CUSTOMER_FAVORITES']['target_met']:
            if getattr(self, '_favorite_minimum_confirmed', False):return False
            # Before skipping requests on a quota, confirm the authoritative
            # projection. Incremental accounting alone never admits completion.
            full=measure_three(list(self.store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan['target_policy'])
            if full['sources']['CUSTOMER_FAVORITES']['target_met'] and getattr(self, '_metrics_index', None) is not None:
                self._favorite_minimum_confirmed = True
            return not full['sources']['CUSTOMER_FAVORITES']['target_met']
        return True

    def source_skip_event(self, plan, source):
        return ('SOURCE_BUDGET_ALREADY_EXHAUSTED' if source['source_type'] in
            getattr(self,'_budget_blocked_sources',set()) else 'SOURCE_MINIMUM_ALREADY_MET')

    def target_stop_reason(self,plan):
        if len(getattr(self, '_budget_blocked_sources', set())) == 3:
            return 'SNAPSHOT_SOURCE_BUDGETS_EXHAUSTED'
        if self._snapshot_metrics is None:
            self._snapshot_metrics=measure_three(list(self.store.iter_observations(plan['run_id'])),
                plan['run_id'],target_policy=plan['target_policy'])
            self._target_quantity_confirmed = self._snapshot_metrics['target_met']
        if self._snapshot_metrics and self._snapshot_metrics['target_met']:
            if getattr(self, '_target_quantity_confirmed', False):
                return f"SNAPSHOT_{plan['target_policy']['target']}_TARGET_REACHED"
            full=measure_three(list(self.store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan['target_policy'])
            self._target_quantity_confirmed = full['target_met']
            if full['target_met']:return f"SNAPSHOT_{plan['target_policy']['target']}_TARGET_REACHED"
        return None

    def continue_after_source_stop(self, reason):
        # A family budget is not an access restriction. Keep other approved
        # families inside their original caps, without transferring budgets.
        prefixes=('THREE_SOURCE_REQUEST_BUDGET_EXHAUSTED:', 'THREE_SOURCE_PAGE_BUDGET_EXHAUSTED:',
                  'THREE_SOURCE_NATIVE_BUDGET_EXHAUSTED:')
        if not reason.startswith(prefixes):return False
        family=reason.rsplit(':',1)[-1]
        if family not in {'BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES'}:return False
        self.__dict__.setdefault('_budget_blocked_sources', set()).add(family)
        return True

    def before_source(self,plan,source):
        run=self.store.get_run(plan['run_id']);schedule=run.get('category_schedule') or {}
        key=source['collection_category_key'];e=self.engine
        if schedule.get('current') not in {None,key}:
            if schedule.get('next')!=key or not schedule.get('deadline'):
                schedule.update(next=key,deadline=utc_text(e.clock()+timedelta(seconds=plan['category_cooldown_seconds'])))
                self.store.update_run(plan['run_id'],category_schedule=schedule,snapshot_phase='CATEGORY_COOLDOWN')
                self.store.append_event(plan['run_id'],{'kind':'CATEGORY_COOLDOWN_STARTED',**schedule})
            deadline=aware_time(schedule['deadline'])
            while e.clock()<deadline:
                if self.pause_requested(plan):raise ValueError('USER_PAUSED')
                if e._gate(plan).get('state')!='NORMAL':raise ValueError('GLOBAL_ACCESS_GATE_BLOCKED_DURING_COOLDOWN')
                reason=budget_reason(plan,self.store.budget_counters(plan['run_id']),now=e.clock(),
                    started_at=run['started_at'],authorization=self._active_authorization)
                if reason:raise ValueError(reason)
                e.sleep(min(30,(deadline-e.clock()).total_seconds()))
        schedule={'current':key,'next':None,'deadline':None}
        self.store.update_run(plan['run_id'],category_schedule=schedule,snapshot_phase='CAPTURING')

    def _record_approval(self,plan,authorization):
        self._active_authorization=authorization
        # Reuse the existing immutable approval journal, without enabling T2.
        from .v4_t2_runner import V4T2Runner
        V4T2Runner._record_approval(self, plan, authorization)

    def after_page(self,plan,source,key,parsed):
        self._admit_navigation(plan,source,key,parsed)
        self._target_quantity_confirmed = False
        if getattr(self, '_metrics_index', None) is not None:
            from .snapshot_metrics import update_from_store
            self._metrics_cursor=update_from_store(self._metrics_index,self.store,plan['run_id'],self._metrics_cursor)
            metrics=self._metrics_index.measure()
        else:
            metrics=measure_three(list(self.store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan['target_policy'])
        self._snapshot_metrics=metrics
        position=next(i for i,(s,u) in enumerate(self._work) if s['source_id']==source['source_id']
                      and u==parsed['committed_page']['page_url'])
        if metrics['sources']['CUSTOMER_FAVORITES']['A_raw_observations']:
            promote_favorite_page(self._work,position,minimum_met=metrics['sources']['CUSTOMER_FAVORITES']['target_met'])
        self.store.update_run(plan['run_id'],snapshot_metrics=metrics)
        progress={'run_id':plan['run_id'],'observed_at':utc_text(self.engine.clock()),'metrics':metrics,
                  'source_id':source['source_id'],'page_url':parsed['committed_page']['page_url'],
                  'phase':'CAPTURING','status':'RUNNING','source_complete':False}
        atomic_json(self.store.root/'snapshot_progress.json',progress)
        print('SNAPSHOT_PROGRESS qualified=%d target=%d favorites=%d/%d' %
              (metrics['F_global_conservative_review_groups'],metrics['global_target'],
               metrics['sources']['CUSTOMER_FAVORITES']['E_qualified_review_groups'],
               metrics['sources']['CUSTOMER_FAVORITES']['target']),flush=True)
