"""Single event-loop writer for three-window PAGE responses.

Use the original run store, parser, delivery detector and DOM audit. Durable
responses precede page indexing; an interrupted callback never releases claims.
"""
import hashlib
from pathlib import Path
from ..multisource.storage import digest
from ..access.detector import detect_access_status
from ..access.location import inspect_delivery_location
from .v4_adapters import parse_v4_page
from .v4_quality import audit_v4


class ParallelSnapshotCommitter:
    def __init__(self,runner,plan,queue,*,stop,fixture_mode=False,access_stop_scope='all',observed_access=None):
        if runner.store.get_run(plan['run_id'])['plan']!=plan:
            raise ValueError('IMMUTABLE_RUN_PLAN_MISMATCH')
        self.runner=runner;self.store=runner.store;self.plan=plan
        self.queue=queue;self.stop=stop;self.fixture_mode=fixture_mode
        self.access_stop_scope=access_stop_scope;self.observed_access=observed_access

    def __call__(self,worker,item,response):
        plan=self.plan;source=item['source'];url=item['url'];reservation=item['reservation']
        if (reservation['run_id']!=plan['run_id'] or reservation['plan_sha256']!=digest(plan)
                or reservation['url']!=url or reservation['source_id']!=source['source_id']):
            self.stop('RESERVATION_BINDING_FAILURE');raise ValueError('RESERVATION_BINDING_FAILURE')
        state=detect_access_status(response.get('status_code'),response.get('text','')+'\n'+
            response.get('rendered_dom_html','')+'\n'+response.get('http_document_html','')).value
        if state!='NORMAL':
            if self.observed_access:self.observed_access(worker,state,item)
            else:self.stop(state)
        origin='OFFLINE_FIXTURE' if self.fixture_mode else 'LIVE_CAPTURE'
        try:
            receipt=self.store.save_response(plan['run_id'],reservation,{**response,'origin':origin,
                'request_url':url,'source_id':source['source_id'],'source_type':source['source_type'],
                'page_key':item['page_key'],'observed_at':reservation['observed_at'],'access_state':state})
        except BaseException:
            self.stop('RESPONSE_JOURNAL_FAILURE')
            raise
        if state!='NORMAL':
            if self.access_stop_scope=='all':
                self.store.set_gate(state,run_id=plan['run_id'],evidence_path=receipt['evidence_path'])
                if not self.fixture_mode:self.runner.engine._persist_shared_stop(plan,state,receipt)
            self.queue.complete(worker,url,state='ACCESS_STOP',receipt=receipt['sha256'],stop_scope=self.access_stop_scope)
            return receipt
        self.queue.complete(worker,url,state='SAVED',receipt=receipt['sha256'])
        try:
            if not self.fixture_mode:
                from .response_alias import authorize_v4_response
                authorize_v4_response(receipt,source,self.runner._active_authorization)
            if plan.get('snapshot_metadata_contract') and inspect_delivery_location(response['text']).is_spain is not True:
                raise ValueError('SPAIN_DELIVERY_NOT_VERIFIED')
            parsed=parse_v4_page(response['text'],source=source,page_url=url,run_id=plan['run_id'],
                snapshot_id=plan['snapshot_id'],observed_at=receipt['observed_at'],
                evidence_path=receipt['html_path'],http_status=receipt['status_code'],origin=origin,
                page_number=source['page_urls'].index(url)+1,
                snapshot_metadata_contract=plan.get('snapshot_metadata_contract'),
                snapshot_field_contract=plan.get('snapshot_field_contract'))
            reference={'path':receipt['evidence_path'],'sha256':hashlib.sha256(Path(receipt['evidence_path']).read_bytes()).hexdigest()}
            page={**parsed['page'],'run_id':plan['run_id'],'snapshot_id':plan['snapshot_id'],
                'page_key':item['page_key'],'source':source,'capture_receipt_reference':reference,
                'source_type_verified':bool(parsed['observations']),'source_context_valid':True,
                'execution_status':'APPROVED_SOURCE_PAGE_SCOPE_EXECUTED',
                'source_representation':receipt.get('source_representation','HTTP_DOCUMENT_BODY'),
                'document_html_reference':{'path':receipt.get('document_html_path'),'sha256':receipt.get('document_sha256')},
                'rendered_html_reference':{'path':receipt.get('rendered_html_path'),'sha256':receipt.get('rendered_sha256')}}
            self.store.commit_page(plan['run_id'],item['page_key'],page,parsed['observations'])
            parsed['committed_page']=page
            if not parsed['observations'] and page['status']!='EXPLICIT_EMPTY':
                if not self.runner.accept_navigation_frame(plan,source,parsed,receipt):
                    raise ValueError('V4_SOURCE_FRAME_UNVERIFIED_SAVED_FOR_OFFLINE_DIAGNOSIS')
            # Discovered children remain bound to the original approved roots.
            # Supplying only the child loses snapshot_admission_root authority
            # and prevents the central audit from replaying genuine pages.
            quality=audit_v4([page],parsed['observations'],plan['sources'],require_live=not self.fixture_mode)
            if not quality['central_full_dom_review_passed']:
                raise ValueError('PARALLEL_PAGE_DOM_REVIEW_FAILED:'+str(quality['errors'][:2]))
            self.runner.after_page(plan,source,item['page_key'],parsed)
            self.queue.replay_committed(url,receipt['sha256'],verify=lambda u,h:
                u==url and h==receipt['sha256'] and hashlib.sha256(Path(receipt['html_path']).read_bytes()).hexdigest()==h)
            return receipt
        except BaseException:
            self.stop('PAGE_COMMIT_OR_REVIEW_FAILURE')
            raise
