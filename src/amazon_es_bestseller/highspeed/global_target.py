"""Explicit global target policy on the existing bounded three-source engine.

No source authorization, evidence requirement, or request limit is relaxed.
All three source minima and the global qualified union must pass together.
"""
from copy import deepcopy
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

# New tasks only. Existing sealed 20k/30k plans keep their original semantics.
MAIN_PRIORITY_10000_POLICY = {**MAIN_PRIORITY_POLICY,
    'profile': 'MAIN_PRIORITY_10000_FAVORITES_3000_V1', 'target': 10000,
    'source_minima': {'BEST_SELLERS': 3500, 'NEW_RELEASES': 3500, 'CUSTOMER_FAVORITES': 3000}}
MAIN_PRIORITY_50000_POLICY = {**MAIN_PRIORITY_POLICY,
    'profile': 'MAIN_PRIORITY_50000_FAVORITES_3000_V1', 'target': 50000,
    'source_minima': {'BEST_SELLERS': 15000, 'NEW_RELEASES': 15000, 'CUSTOMER_FAVORITES': 3000}}
MAIN_PRIORITY_100000_POLICY = {**MAIN_PRIORITY_POLICY,
    'profile':'MAIN_PRIORITY_100000_FAVORITES_3000_V1','target':100000,'total_task_hours':72,
    'source_minima':{'BEST_SELLERS':30000,'NEW_RELEASES':30000,'CUSTOMER_FAVORITES':3000}}
SNAPSHOT_PROFILES = {'main-priority-10000': MAIN_PRIORITY_10000_POLICY,
                     'main-priority': MAIN_PRIORITY_POLICY,
                     'main-priority-50000': MAIN_PRIORITY_50000_POLICY,
                     'main-priority-100000': MAIN_PRIORITY_100000_POLICY}

CUSTOM_PROFILE='CONFIGURABLE_PAGE_SNAPSHOT_V1'


def configurable_policy(target,hours,source_minima=None):
    """Bounded new-task policy; sealed preset policies remain exact contracts."""
    if type(target) is not int or not 1<=target<=100000:
        raise ValueError('CONFIGURABLE_TARGET_MUST_BE_1_TO_100000')
    if type(hours) is not int or not 2<=hours<=168:
        raise ValueError('CONFIGURABLE_DURATION_MUST_BE_2_TO_168_HOURS')
    minima=source_minima if source_minima is not None else {
        'BEST_SELLERS':max(1,target*3//10),'NEW_RELEASES':max(1,target*3//10),
        'CUSTOMER_FAVORITES':max(1,min(3000,target*6//100))}
    if (not isinstance(minima,dict) or set(minima)!=set(POLICY['source_minima'])
            or any(type(v) is not int or not 1<=v<=target for v in minima.values())):
        raise ValueError('EXPLICIT_BOUNDED_SOURCE_MINIMA_REQUIRED')
    return {**deepcopy(MAIN_PRIORITY_POLICY),'profile':CUSTOM_PROFILE,'target':target,
        'total_task_hours':hours,'source_minima':dict(minima)}


def is_page_policy(policy):
    return is_global_policy(policy) and (policy.get('profile')==CUSTOM_PROFILE or policy in SNAPSHOT_PROFILES.values())


def source_targets(policy=None):
    if policy is not None:
        is_global_policy(policy)
        return dict(policy['source_minima'])
    return {k: 5001 for k in POLICY['source_minima']}


def is_global_policy(policy):
    if policy is None:return False
    if not isinstance(policy,dict):
        raise ValueError('EXACT_GLOBAL_TARGET_POLICY_REQUIRED')
    expected = (configurable_policy(policy.get('target'),policy.get('total_task_hours'),policy.get('source_minima'))
        if policy.get('profile')==CUSTOM_PROFILE else
        next((p for p in SNAPSHOT_PROFILES.values() if policy.get('profile') == p['profile']), POLICY))
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
