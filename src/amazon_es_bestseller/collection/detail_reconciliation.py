"""Read-only accounting over frozen task identities, never an identity authority."""
from collections import Counter
import re

_SUCCESS = {'success', 'reused'}
_NETWORK = {'FETCH_NEW', 'REFETCH_INVALID_CACHE', 'RETRY_TRANSIENT_FAILURE'}


def checkpoint_outcome(checkpoint, usable):
    """A checkpoint label alone cannot establish a reusable successful record."""
    status = str(checkpoint.get('status') or 'pending')
    if status.lower() in _SUCCESS:
        return (status.lower() if usable(checkpoint.get('record'), checkpoint.get('asin'))
                else 'CHECKPOINT_REVIEW_REQUIRED')
    return status


def reconcile_outcomes(tasks, outcomes, held):
    tasks = list(tasks)
    asins = [row['asin'] for row in tasks]
    if len(asins) != len(set(asins)) or any(not re.fullmatch(r'[A-Z0-9]{10}', a) for a in asins):
        raise ValueError('DUPLICATE_OR_INVALID_FROZEN_ASIN')
    if (set(outcomes) | set(held)) - set(asins):
        raise ValueError('UNKNOWN_OUTCOME_ASIN')
    rows = []
    for task in tasks:
        asin = task['asin']
        status = str(outcomes.get(asin) or '')
        # A plan action or held label never establishes reuse success.
        if not status:
            if task['action'] == 'REUSE_VALID_CACHE':
                status = 'CACHE_REVIEW_REQUIRED'
            elif asin in held or task['action'] not in _NETWORK:
                status = 'HELD_REVIEW_REQUIRED'
            else:
                status = 'PENDING'
        rows.append({'asin': asin, 'action': task['action'], 'status': status,
                     'held_reason': held.get(asin)})
    counts = Counter(row['status'] for row in rows)
    success = sum(counts.get(status, 0) for status in _SUCCESS)
    pending = sum(counts.get(status, 0) for status in ('PENDING', 'pending'))
    return {'target': len(tasks), 'accounted': len(rows), 'success': success,
            'pending': pending, 'non_success': len(rows) - success,
            'counts': dict(counts), 'records': rows,
            'status': 'COMPLETE' if rows and success == len(rows) else 'PARTIAL',
            'production_acceptance': 'NOT_EVALUATED'}
