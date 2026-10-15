"""Explicit global target policy on the existing bounded three-source engine.

No source authorization, evidence requirement, or request limit is relaxed.
Source 5001 counters remain diagnostics; global acceptance uses qualified union.
"""
POLICY = {'mode':'GLOBAL_CONSERVATIVE_REVIEW_GROUPS','target':30000,
          'fresh_live_only':True,'stop_at_page_boundary':True}


def is_global_policy(policy):
    if policy is None:return False
    if (policy != POLICY or set(policy) != set(POLICY)
            or any(type(policy[k]) is not type(v) for k,v in POLICY.items())):
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
