"""Explicit global target policy on the existing bounded three-source engine.

No source authorization, evidence requirement, or request limit is relaxed.
All three source minima and the global qualified union must pass together.
"""
POLICY = {'mode':'GLOBAL_CONSERVATIVE_REVIEW_GROUPS','target':30000,
          'fresh_live_only':True,'stop_at_page_boundary':True,
          'source_minima':{'BEST_SELLERS':5001,'NEW_RELEASES':5001,'CUSTOMER_FAVORITES':5001},
          'total_task_hours':10}

# A new explicit task profile. Never reinterpret an existing sealed plan.
MAIN_PRIORITY_POLICY = {**POLICY,
    'profile': 'MAIN_PRIORITY_20000_FAVORITES_3000_V1',
    'target': 20000,
    'total_task_hours': 24,
    'source_minima': {'BEST_SELLERS': 5000, 'NEW_RELEASES': 5000,
                      'CUSTOMER_FAVORITES': 3000},
    'primary_sources': ['BEST_SELLERS', 'NEW_RELEASES'],
    'supplemental_sources': ['CUSTOMER_FAVORITES']}


def source_targets(policy=None):
    if policy is not None:
        is_global_policy(policy)
        return dict(policy['source_minima'])
    return {k: 5001 for k in POLICY['source_minima']}


def is_global_policy(policy):
    if policy is None:return False
    if not isinstance(policy,dict):
        raise ValueError('EXACT_GLOBAL_TARGET_POLICY_REQUIRED')
    expected = MAIN_PRIORITY_POLICY if policy.get('profile') == MAIN_PRIORITY_POLICY['profile'] else POLICY
    if (policy != expected or set(policy) != set(expected)
            or any(type(policy[k]) is not type(v) for k,v in expected.items())):
        raise ValueError('EXACT_GLOBAL_30000_TARGET_POLICY_REQUIRED')
    if any(type(v) is not int for v in policy['source_minima'].values()):
        raise ValueError('EXACT_GLOBAL_30000_TARGET_POLICY_REQUIRED')
    return True


def acceptance(metrics, audit_passed):
    if metrics.get('target_mode')=='GLOBAL_CONSERVATIVE_REVIEW_GROUPS':
        target=metrics.get('global_target',30000)
        return (f'ACCEPTED — GLOBAL_{target}_CANDIDATES_QA_PASSED' if metrics['target_met'] and audit_passed else
                f'NOT_ACCEPTED — GLOBAL_{target}_TARGET_NOT_MET' if not metrics['target_met'] else
                f'NOT_ACCEPTED — GLOBAL_{target}_QUALITY_AUDIT_FAILED')
    return ('ACCEPTED — THREE_SOURCE_5001_CANDIDATES_QA_PASSED' if metrics['target_met'] and audit_passed else
            'NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET' if not metrics['target_met'] else
            'NOT_ACCEPTED — THREE_SOURCE_QUALITY_AUDIT_FAILED')
