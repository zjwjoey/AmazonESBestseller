"""TEST_ONLY request-to-window binding; not an installed live route handler.

Each window has exactly one pre-reserved top-level document. Fail closed on
redirects or an unexpected document, even if it belongs to another approved
source. XHR/fetch cannot escape the PAGE-only budget. The real browser adapter
and redirect review remain separate prerequisites for live use.
"""
from .v4_navigation import exact_query_token_reordering


class OfflineRouteGuard:
    def __init__(self, reservations, *, gate_open):
        if set(reservations)!={1,2,3} or not callable(gate_open):
            raise ValueError('THREE_FIXED_WINDOW_RESERVATIONS_REQUIRED')
        urls=[reservations[i] for i in (1,2,3)]
        if any(not isinstance(u,str) or not u for u in urls) or len(set(urls))!=3:
            raise ValueError('DISTINCT_RESERVED_URLS_REQUIRED')
        self.reservations=dict(reservations);self.gate_open=gate_open;self.dispatched=set()

    def decide(self, worker, *, url, method, resource_type, top_level=True, redirected=False):
        if worker not in self.reservations or not self.gate_open():return 'ABORT'
        if resource_type in {'xhr','fetch'}:return 'ABORT'
        if resource_type!='document':return 'CONTINUE_RESOURCE'
        expected=self.reservations[worker]
        if (method!='GET' or not top_level or redirected or worker in self.dispatched
                or not (url==expected or exact_query_token_reordering(url,expected))):
            return 'ABORT'
        self.dispatched.add(worker)
        return 'CONTINUE_RESERVED_DOCUMENT'
