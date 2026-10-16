"""Owner-thread browser plumbing for the explicit three-local-lane executor."""
from contextlib import contextmanager
from copy import copy
from uuid import uuid4
from .v4_loading import V4LoadingTransport
from .cli import save_native_contract
from .budgets import utc_text
from ..multisource.storage import immutable_json


@contextmanager
def lane_browser_transports(session, store, plan, authorization):
    """Three pages in the SAME persistent profile; never three profile owners."""
    transports=[];pages=[]
    try:
        for lane in range(3):
            view=copy(session)
            view.page=session.context.new_page()
            view.last_document_response=None
            pages.append(view.page)
            allowed={u for s in plan['sources'][lane::3] for u in s['page_urls']}
            def install(view=view,allowed=allowed,lane=lane):
                def bounded(route):
                    req=route.request
                    if req.resource_type=='document' and req.url not in allowed:
                        immutable_json(store.root/'navigation_discovery_contracts'/(uuid4().hex+'.json'),{
                            'kind':'UNAPPROVED_DOCUMENT_ABORTED','url':req.url,'method':req.method,
                            'run_id':plan['run_id'],'observed_at':utc_text(),
                            'request_sent':False,'approved':False,'lane':lane})
                        route.abort();return
                    if req.resource_type in {'xhr','fetch'}:
                        route.abort()
                        immutable_json(store.root/'native_discovery_contracts'/(uuid4().hex+'.json'),{
                            'url':req.url,'method':req.method,'payload':req.post_data or '',
                            'parent_page_url':view.page.url,'resource_type':req.resource_type,
                            'observed_at':utc_text(),'run_id':plan['run_id'],
                            'request_contract_source':'NATIVE_BROWSER_REQUEST_ABORTED',
                            'request_sent':False,'not_approved_for_replay':True,'lane':lane})
                        return
                    route.continue_()
                view.page.route('**/*',bounded)
            install()
            transport=V4LoadingTransport(view,store=store,plan=plan,measure_timings=True,
                preserve_document_body=True,hold_native_carousel=all(
                    authorization['sources'][s['source_id']].get('allow_held_native_carousel')
                    for s in plan['sources'] if s['source_type'] in {'BEST_SELLERS','NEW_RELEASES'}))
            transport.install_bounded_acp_capture(
                on_capture=lambda value:save_native_contract(store,plan,value),
                on_local_replay=lambda event:store.append_event(plan['run_id'],
                    {'kind':'NATIVE_CAROUSEL_LOCAL_UI_REPLAY',**event}))
            transports.append(transport)
        yield transports
    finally:
        for transport in transports:
            transport.cancel_dynamic();transport.abort_held_native_routes()
        for page in pages:page.close()
