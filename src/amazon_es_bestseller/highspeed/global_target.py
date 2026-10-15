"""Explicit global target policy on the existing bounded three-source engine.

No source authorization, evidence requirement, or request limit is relaxed.
All three source minima and the global qualified union must pass together.
"""
POLICY = {'mode':'GLOBAL_CONSERVATIVE_REVIEW_GROUPS','target':30000,
          'fresh_live_only':True,'stop_at_page_boundary':True,
          'source_minima':{'BEST_SELLERS':5001,'NEW_RELEASES':5001,'CUSTOMER_FAVORITES':5001},
          'total_task_hours':10}


def is_global_policy(policy):
    if policy is None:return False
    if (policy != POLICY or set(policy) != set(POLICY)
            or any(type(policy[k]) is not type(v) for k,v in POLICY.items())):
        raise ValueError('EXACT_GLOBAL_30000_TARGET_POLICY_REQUIRED')
    if any(type(v) is not int for v in policy['source_minima'].values()):
        raise ValueError('EXACT_GLOBAL_30000_TARGET_POLICY_REQUIRED')
    return True


def acceptance(metrics, audit_passed):
    if metrics.get('target_mode')=='GLOBAL_CONSERVATIVE_REVIEW_GROUPS':
        return ('ACCEPTED — GLOBAL_30000_CANDIDATES_QA_PASSED' if metrics['target_met'] and audit_passed else
                'NOT_ACCEPTED — GLOBAL_30000_TARGET_NOT_MET' if not metrics['target_met'] else
                'NOT_ACCEPTED — GLOBAL_30000_QUALITY_AUDIT_FAILED')
    return ('ACCEPTED — THREE_SOURCE_5001_CANDIDATES_QA_PASSED' if metrics['target_met'] and audit_passed else
            'NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET' if not metrics['target_met'] else
            'NOT_ACCEPTED — THREE_SOURCE_QUALITY_AUDIT_FAILED')
