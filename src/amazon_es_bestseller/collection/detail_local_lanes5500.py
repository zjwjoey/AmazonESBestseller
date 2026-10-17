"""Bounded local parsers. The parent alone owns browser, receipts and writes.

Workers receive immutable captured text, never a browser/profile/authorization.
Captured work is drained on every exit; an identity failure stops new claims.
"""
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context


def parse_captured_detail(task, html, meta, require_postal, family_policy=None):
    from .detail_bridge5500 import parse_candidate_detail
    from ..access.location import inspect_delivery_location
    if family_policy is not None:
        from .detail_family_identity5500 import parse_family_detail
        result = parse_family_detail(task, html, meta, family_policy)
    else:
        result = parse_candidate_detail(task, html, meta)
    location = inspect_delivery_location(html)
    if location.is_spain is not True or (require_postal and location.postal_code != '28001'):
        result = {**result, 'status': 'IDENTITY_REVIEW', 'record': None,
                  'reason': 'SPAIN_DELIVERY_LOCATION_NOT_VERIFIED', 'delivery_text': location.text}
    import os
    result['local_parser_pid'] = os.getpid()
    return result


class LocalDetailLanes:
    """Exactly three local slots, FIFO commits, no network methods or retries."""
    def __init__(self, commit, *, executor_factory=None):
        self.commit = commit
        self.executors = ([executor_factory()] if executor_factory else
                          [ProcessPoolExecutor(max_workers=1, mp_context=get_context('spawn')) for _ in range(3)])
        self.pending = []
        self.claimed = set()
        self.failure = None
        self.closed = False

    def __enter__(self):
        return self

    def _finish_first(self):
        task, future, evidence = self.pending.pop(0)
        try:
            result = future.result()
        except Exception as exc:
            result = {'status': 'PARSE_FAILED', 'reason': 'LOCAL_PARSER_EXCEPTION',
                      'local_error': type(exc).__name__ + ': ' + str(exc)}
        result.update(evidence)
        self.commit(task, result)
        if result['status'] != 'SUCCESS' and self.failure is None:
            self.failure = ValueError('DETAIL_SYSTEMATIC_SAFETY_REVIEW_REQUIRED: ' + result['status'])

    def drain(self):
        # Save every already captured task even if another task failed.
        while self.pending:
            self._finish_first()

    def check_before_request(self):
        if self.closed:
            raise RuntimeError('LOCAL_LANES_CLOSED')
        while self.pending and self.pending[0][1].done():
            self._finish_first()
        if len(self.pending) >= 3:
            self._finish_first()
        # A later completed failure must not wait behind a slow earlier parse.
        for _, future, _ in self.pending:
            if future.done():
                try:
                    failed = future.result()['status'] != 'SUCCESS'
                except Exception:
                    failed = True
                if failed:
                    self.drain()
                    break
        if self.failure:
            raise self.failure

    def submit(self, task, html, meta, evidence, require_postal):
        # This method follows a real capture: never reject it merely because
        # another parse just failed. Save/drain it before propagating the stop.
        if self.closed:
            raise RuntimeError('LOCAL_LANES_CLOSED')
        if task['task_id'] in self.claimed:
            raise ValueError('DUPLICATE_LOCAL_TASK_CLAIM')
        if len(self.pending) >= 3:
            self._finish_first()
        self.claimed.add(task['task_id'])
        executor = self.executors[(len(self.claimed)-1) % len(self.executors)]
        future = executor.submit(parse_captured_detail, task, html, meta, require_postal)
        self.pending.append((task, future, evidence))

    def __exit__(self, exc_type, exc, tb):
        try:
            self.drain()
        finally:
            self.closed = True
            for executor in self.executors:
                executor.shutdown(wait=True)
        if exc_type is None and self.failure:
            raise self.failure
        return False
