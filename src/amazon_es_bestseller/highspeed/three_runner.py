"""Three-source scheduling through the verified serial V4 receipt engine."""
import hashlib
from pathlib import Path
from ..multisource.storage import immutable_json
from .three_navigation import CONTRACT, discover_navigation
from .three_contract import validate_three_plan
from .three_metrics import measure_three
from .v4_runner import V4T1Runner
from .v4_t2_runner import V4T2Runner
from .v4_capacity import audit_capacity, execution_for_store


class V4ThreeSourceRunner(V4T2Runner):
    stage='T3'

    def run(self,plan,**kwargs):
        validate_three_plan(plan)
        # Derived indexes only in this fresh campaign store. Avoid repeatedly
        # parsing every large reservation JSON just to count a source budget.
        self.store.db.execute("CREATE INDEX IF NOT EXISTS three_request_family ON requests "
            "(run_id,json_extract(data_json,'$.source_type'),kind)")
        self.store.db.execute("CREATE INDEX IF NOT EXISTS three_request_source ON requests "
            "(run_id,json_extract(data_json,'$.source_id'),kind)")
        self._global_target_met=None
        self._global_metrics=None
        result=V4T1Runner.run(self,plan,**kwargs)
        execution=execution_for_store(self.store,plan['run_id'])
        rows=list(self.store.iter_observations(plan['run_id']));pages=self.store.pages(plan['run_id'])
        audit=audit_capacity(pages,rows,plan['sources'],execution,three_source=True)
        result['quality']=audit['central_quality']
        self.store.append_event(plan['run_id'],{'kind':'THREE_COMBINED_DOM_RECEIPT_REVIEW',
            'central_dom_passed':audit['central_quality']['central_full_dom_review_passed'],
            'receipt_audit_passed':audit['diagnostic_passed'],'errors':audit['errors']})
        metrics=measure_three(rows,plan['run_id'],target_policy=plan.get('target_policy'))
        accepted=metrics['target_met'] and audit['diagnostic_passed']
        from .global_target import acceptance as target_acceptance
        acceptance=target_acceptance(metrics,audit['diagnostic_passed'])
        self.store.update_run(plan['run_id'],quality_gate_passed=accepted,qa=audit['central_quality'],three_source_metrics=metrics,
            three_source_receipt_audit=audit,three_source_acceptance=acceptance,
            SKU_identity_status='NOT_VERIFIED',T3_execution_enabled=False)
        return {**result,'run':self.store.get_run(plan['run_id']),'metrics':metrics,'receipt_audit':audit,
                'acceptance':acceptance}

    def target_stop_reason(self,plan):
        from .global_target import is_global_policy
        if not is_global_policy(plan.get('target_policy')):return None
        if self._global_target_met is None:
            m=measure_three(list(self.store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan['target_policy'])
            self._global_target_met=m['target_met']
            self._global_metrics=m
        return 'GLOBAL_30000_QUALIFIED_GROUPS_REACHED' if self._global_target_met else None

    def should_collect_source(self,plan,source):
        if not plan.get('target_policy'):return True
        self.target_stop_reason(plan)
        metrics=self._global_metrics
        return (metrics['F_global_conservative_review_groups']<30000
            or not metrics['sources'][source['source_type']]['target_met'])

    def defer_central_review_to_capacity(self):
        # audit_capacity performs the complete current-run DOM review itself.
        return True

    def continue_after_source_stop(self,reason):
        return reason.startswith(('THREE_SOURCE_REQUEST_BUDGET_EXHAUSTED:',
            'THREE_SOURCE_PAGE_BUDGET_EXHAUSTED:','THREE_SOURCE_NATIVE_BUDGET_EXHAUSTED:'))

    def remaining_page_work(self,plan,work,committed,current_url):
        from .three_contract import family_budget_for_store
        blocked=set()
        for kind in {s['source_type'] for s in plan['sources']}:
            source=next(s for s in plan['sources'] if s['source_type']==kind)
            if family_budget_for_store(self.store,plan,source,'PAGE'):blocked.add(kind)
        return sum(u not in committed and u!=current_url and s['source_type'] not in blocked for s,u in work)

    def accept_navigation_frame(self,plan,source,parsed,receipt):
        # Missing cards plus sidebar is insufficient: reject unresolved product
        # metadata, unknown source states and unsafe HTTP/access conditions.
        page=parsed['page']
        if (receipt.get('status_code')!=200 or receipt.get('access_state')!='NORMAL'
                or page['status']!='NAVIGATION_ONLY' or parsed['acp_pending']
                or page.get('legacy_page',{}).get('expected_count')):
            return False
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(Path(page['evidence_path']).read_bytes(),'lxml')
        from .v4_frontier import _excluded
        if any(not _excluded(n) for n in soup.select('[data-asin], [data-client-recs-list], [data-expected-count]')):
            return False
        refs=discover_navigation(page,source)
        return any(r['status']=='PENDING_REVIEW' and r['relationship'] in {'THEME_LINK','CHILD_CATEGORY','RESULTS_ENTRY'} for r in refs)

    def after_page(self,plan,source,key,parsed):
        rows=discover_navigation(parsed['page'],source)
        directory=self.store.root/'runs'/plan['run_id']/'navigation'
        immutable_json(directory/(hashlib.sha256((key+parsed['page']['evidence_sha256']).encode()).hexdigest()+'.json'),
            {'contract':CONTRACT,'run_id':plan['run_id'],'page_key':key,'references':rows,
             'approved':False,'new_network_requests':0})
        metrics=measure_three(list(self.store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan.get('target_policy'))
        self._global_target_met=metrics['target_met']
        self._global_metrics=metrics
        self.store.append_event(plan['run_id'],{'kind':'THREE_SOURCE_PROGRESS','source_type':source['source_type'],
            'page_key':key,'metrics':metrics,
            'discovered_links_pending_review':len(rows)})
