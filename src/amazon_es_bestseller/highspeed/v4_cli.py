"""V4 explicit entry point. Help/replay/prepare/check never launch a browser."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import uuid

from ..multisource.storage import immutable_json, file_lock
from .budgets import budget_reason, utc_text
from .cli import load_json, save_native_contract
from .v4_authorization import validate_v4_authorization, prerequisite_review_session
from .v4_runner import V4T1Runner
from .v4_t2_runner import V4T2Runner
from .v4_capacity import execution_for_store
from .v4_workflows import replay_v4, prepare_v4, export_v4


def run_live_v4(args):
    with prerequisite_review_session():
        return _run_live_v4(args)


def _run_live_v4(args):
    from .store import RunStore
    plan = load_json(args.plan); approval = load_json(args.authorization)
    if Path(args.root).resolve().parent.name != plan['run_id']:
        raise ValueError('V4_FRESH_RUN_DIRECTORY_REQUIRED')
    store = RunStore(args.root)
    try:
        if plan['stage'] not in {'T1','T2'}:
            raise ValueError('V4_T3_EXECUTION_DISABLED')
        runner = (V4T2Runner if plan['stage']=='T2' else V4T1Runner)(store)
        validate_v4_authorization(plan, approval, runner.engine._gate(plan), store_root=store.root)
        if args.operation == 'check':
            return {'authorized_now': True, 'stage': plan['stage'], 'browser_started': False}
        if not args.live:
            raise ValueError('V4_EXPLICIT_T1_T2_LIVE_SWITCH_REQUIRED')
        with ExitStack() as locks:
            locks.enter_context(store.run_lock(plan['run_id']))
            locks.enter_context(file_lock(Path(plan['shared_access_gate_path']).parent / 'highspeed_live.lock'))
            validate_v4_authorization(plan, approval, runner.engine._gate(plan), store_root=store.root)
            run = store.begin_run(plan, started_at=utc_text())
            reason = budget_reason(plan, store.summary(plan['run_id']), started_at=run['started_at'])
            if reason:
                raise ValueError(reason)
            if any(r['status'] == 'RESERVED' for r in store.requests(plan['run_id'])):
                raise ValueError('AMBIGUOUS_UNFINISHED_REQUESTS_NO_AUTO_RETRY')
            from ..access.browser import BrowserSession
            from .v4_loading import V4LoadingTransport
            class BoundedSession(BrowserSession):
                def goto(self, url, timeout_ms=45000):
                    self.last_document_response = None
                    response = self.page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
                    self.last_document_response = response
                    return response.status if response is not None else None
                def load_lazy_ranking_content(self, *a, **kw):
                    return None
            with BoundedSession(headless=not args.headful, profile_dir=args.profile_dir) as session:
                allowed = {u for s in plan['sources'] for u in s['page_urls']}
                def bounded(route):
                    req = route.request
                    if req.resource_type == 'document' and req.url not in allowed:
                        route.abort(); return
                    if req.resource_type in {'xhr', 'fetch'}:
                        # Unknown new-source protocols are evidence to review,
                        # never unmetered implicit collection. ACP has its own
                        # more-specific existing nonce/receipt controller.
                        route.abort()
                        immutable_json(store.root / 'native_discovery_contracts' / (uuid.uuid4().hex + '.json'), {
                            'url': req.url, 'method': req.method, 'payload': req.post_data or '',
                            'parent_page_url': session.page.url, 'resource_type': req.resource_type,
                            'observed_at': utc_text(), 'run_id': plan['run_id'],
                            'request_contract_source': 'NATIVE_BROWSER_REQUEST_ABORTED',
                            'request_sent': False, 'not_approved_for_replay': True})
                        return
                    route.continue_()
                session.page.route('**/*', bounded)
                transport = V4LoadingTransport(session, store=store, plan=plan, measure_timings=True, preserve_document_body=True,
                    hold_native_carousel=all(approval['sources'][s['source_id']].get('allow_held_native_carousel')
                        for s in plan['sources'] if s['source_type'] in {'BEST_SELLERS','NEW_RELEASES'}))
                transport.install_bounded_acp_capture(
                    on_capture=lambda value: save_native_contract(store, plan, value),
                    on_local_replay=lambda event: store.append_event(plan['run_id'], {'kind': 'NATIVE_CAROUSEL_LOCAL_UI_REPLAY', **event}))
                transport.install_dynamic_capture()
                try:
                    return runner.run(plan, authorization=approval, transport=transport, resume=True, preheld_lock=True,
                                      reverify_source_ids=args.reverify_source_id)
                finally:
                    transport.cancel_dynamic()
                    transport.abort_held_native_routes()
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for name in ('replay', 'prepare'):
        p = sub.add_parser(name); p.add_argument('--fixtures', required=True); p.add_argument('--out', required=True)
        if name == 'prepare':
            p.add_argument('--runtime-root', required=True); p.add_argument('--shared-gate', required=True)
    p=sub.add_parser('prepare-t2')
    for opt in ('t0','t1','out','runtime-root','shared-gate'):p.add_argument('--'+opt,required=True)
    p = sub.add_parser('prepare-t2-supplement', help='Offline saved-DOM supplementary T2 proposal; never LIVE.')
    for opt in ('t2', 'navigation', 'selection', 'out', 'runtime-root', 'shared-gate'):
        p.add_argument('--' + opt, required=True)
    p = sub.add_parser('review-navigation', help='Offline T2 saved-navigation inventory; no approval or network.')
    p.add_argument('--t2', required=True); p.add_argument('--out', required=True)
    for name in ('check', 'run', 'resume'):
        p = sub.add_parser(name)
        for opt in ('root', 'plan', 'authorization'):
            p.add_argument('--' + opt, required=True)
        p.add_argument('--live', action='store_true'); p.add_argument('--headful', action='store_true')
        p.add_argument('--profile-dir')
        p.add_argument('--reverify-source-id', action='append', default=[])
    p = sub.add_parser('report')
    p.add_argument('--retain-lightning-empty', action='store_true',
                   help='Explicit user acceptance of the real empty lightning source; strict eight-product result remains separate.')
    for opt in ('root', 'run-id', 'out'):
        p.add_argument('--' + opt, required=True)
    args = parser.parse_args(argv)
    if args.operation == 'prepare-t2-supplement':
        import hashlib
        from .v4_supplement import prepare_t2_supplement
        refs = [{'path': str(Path(p).resolve()), 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                for p in (args.t2, args.navigation, args.selection)]
        result = prepare_t2_supplement(*refs, args.out, args.runtime_root, args.shared_gate)
    elif args.operation == 'review-navigation':
        import hashlib
        from .v4_frontier import review_navigation
        path = Path(args.t2).resolve()
        result = review_navigation({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, args.out)
    elif args.operation=='prepare-t2':
        import hashlib
        from .v4_t2_prepare import prepare_t2
        refs=[{'path':str(Path(p).resolve()),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in (args.t0,args.t1)]
        result=prepare_t2(*refs,args.out,args.runtime_root,args.shared_gate)
    elif args.operation == 'replay':
        result = replay_v4(args.fixtures, args.out)
    elif args.operation == 'prepare':
        result = prepare_v4(args.fixtures, args.out, args.runtime_root, args.shared_gate)
    elif args.operation == 'report':
        from .store import RunStore
        store = RunStore(args.root)
        try:
            run = store.get_run(args.run_id)
            accepted=([s['actual_source_url'] for s in run['plan']['sources']
                if s['source_subtype']=='LIGHTNING_DEAL_FILTER'] if args.retain_lightning_empty else [])
            result = export_v4(store.pages(args.run_id), list(store.iter_observations(args.run_id)),
                                run['plan']['sources'], args.out, run_id=args.run_id, stage=run['stage'],
                                accepted_empty_urls=accepted,
                                capacity_execution=execution_for_store(store,args.run_id) if run['stage']=='T2' else None,
                                request_receipts=list(store.iter_responses(args.run_id)))
        finally:
            store.close()
    else:
        result = run_live_v4(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()
