"""Conservative budget reservations are not fabricated network receipts."""
from pathlib import Path
from ..multisource.storage import read_sealed, digest
from .quick_selection import file_sha


def uncertain_setup_page_holds(root, plan, auth=None):
    root = Path(root)
    p = root / "uncertain_setup_PAGE_budget_reservation.json"
    if not p.exists():
        if auth and auth.get("request_budget_reservation_reference"):
            raise ValueError("REQUEST_BUDGET_RESERVATION_MISSING")
        return 0
    if auth is not None and auth.get("request_budget_reservation_reference") != dict(path=str(p), sha256=file_sha(p)):
        raise ValueError("REQUEST_BUDGET_RESERVATION_NOT_BOUND")
    v = read_sealed(p)
    if (v.get("scope") != "CONSERVATIVE_RESERVATION_FOR_TWO_UNRECEIPTED_SETUP_DOCUMENTS"
            or v.get("run_id") != plan["run_id"] or v.get("plan_sha256") != digest(plan)
            or v.get("held_PAGE_requests") != 2 or v.get("not_network_receipts") is not True):
        raise ValueError("REQUEST_BUDGET_RESERVATION_INVALID")
    for name in ("initial_attempt_reference", "continuation_2_attempt_reference",
                 "initial_failure_reference", "continuation_2_failure_reference"):
        r = v[name]
        if not Path(r["path"]).resolve().is_relative_to(root.resolve()) or file_sha(r["path"]) != r["sha256"]:
            raise ValueError("REQUEST_BUDGET_RESERVATION_EVIDENCE_CHANGED")
    return 2


def unobserved_manual_page_holds(root, plan, auth=None):
    """Charge reported human refreshes conservatively without inventing HTTP."""
    root=Path(root);paths=sorted((root/'manual_PAGE_budget_holds').glob('*.json'))
    refs=[dict(path=str(p),sha256=file_sha(p)) for p in paths]
    if auth is not None and refs!=(auth.get('manual_PAGE_budget_hold_references') or []):
        raise ValueError('MANUAL_PAGE_HOLDS_NOT_BOUND')
    total=0;seen=set()
    for p in paths:
        v=read_sealed(p);source=v.get('user_report_reference') or {};key=source.get('sha256')
        n=v.get('held_PAGE_requests')
        if (v.get('scope')!='USER_REPORTED_MANUAL_REFRESH_UNOBSERVED'
                or v.get('run_id')!=plan['run_id'] or v.get('plan_sha256')!=digest(plan)
                or v.get('not_network_receipts') is not True or type(n) is not int or not 1<=n<=5
                or not Path(source.get('path','')).resolve().is_relative_to(root.resolve())
                or not key or key in seen or file_sha(source['path'])!=key):
            raise ValueError('MANUAL_PAGE_HOLD_INVALID_OR_DUPLICATED')
        seen.add(key);total+=n
    return total


def total_conservative_page_holds(root,plan,auth=None):
    return uncertain_setup_page_holds(root,plan,auth)+unobserved_manual_page_holds(root,plan,auth)
