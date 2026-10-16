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
