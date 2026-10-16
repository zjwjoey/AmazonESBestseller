"""Keep an independently reviewed changed child as a failure, never a success.

This permits the next original frozen task, not a replacement or a request retry.
Access failures and ambiguous identity cannot enter this isolation path.
"""
import json
from pathlib import Path

from ..identity import asin_from_url
from ..access.location import inspect_delivery_location
from ..multisource.storage import digest, read_sealed
from .quick_selection import file_sha


def reviewed_identity_quarantines(root, plan, auth):
    root = Path(root)
    ref = (auth or {}).get("reviewed_identity_quarantines_reference")
    if not ref:
        return {}
    if not Path(ref["path"]).resolve().is_relative_to(root.resolve()) or file_sha(ref["path"]) != ref["sha256"]:
        raise ValueError("IDENTITY_QUARANTINE_REVIEW_CHANGED")
    v = json.loads(Path(ref["path"]).read_text(encoding="utf8"))
    if (v.get("approved") is not True or v.get("approved_by") != "agent_under_explicit_user_delegation"
            or v.get("scope") != "ISOLATED_ACTUAL_CHILD_CHANGE_NOT_A_SUCCESS"
            or v.get("run_id") != plan["run_id"] or v.get("plan_sha256") != digest(plan)
            or v.get("no_replacement") is not True or not v.get("tasks")):
        raise ValueError("IDENTITY_QUARANTINE_SCOPE_INVALID")
    tasks = {t["task_id"]: t for t in plan["tasks"]}
    out = {}
    from .detail_bridge5500 import parse_candidate_detail
    for item in v["tasks"]:
        task = tasks.get(item["task_id"])
        if not task or task["task_id"] in out:
            raise ValueError("IDENTITY_QUARANTINE_TASK_INVALID")
        for name in ("checkpoint_reference", "receipt_reference", "HTML_reference", "metadata_reference"):
            r = item[name]
            if not Path(r["path"]).resolve().is_relative_to(root.resolve()) or file_sha(r["path"]) != r["sha256"]:
                raise ValueError("IDENTITY_QUARANTINE_EVIDENCE_CHANGED")
        old = read_sealed(item["checkpoint_reference"]["path"])
        receipt = read_sealed(item["receipt_reference"]["path"])
        meta = json.loads(Path(item["metadata_reference"]["path"]).read_text(encoding="utf8"))
        html = Path(item["HTML_reference"]["path"]).read_text(encoding="utf8")
        replay = parse_candidate_detail(task, html, meta)
        observed = old.get("observed_active_asins") or []
        location = inspect_delivery_location(html)
        if (old.get("status") != "IDENTITY_REVIEW" or old.get("reason") != "ACTIVE_CHILD_ASIN_CHANGED"
                or old.get("task_id") != task["task_id"] or old.get("run_id") != plan["run_id"]
                or receipt.get("state") != "NORMAL" or receipt.get("status_code") != 200
                or receipt.get("request_role") != "DETAIL" or receipt.get("request_url") != task["request_url"]
                or receipt.get("run_id") != plan["run_id"] or receipt.get("task_id") != task["task_id"]
                or old.get("receipt_path") != item["receipt_reference"]["path"]
                or old.get("receipt_sha256") != item["receipt_reference"]["sha256"]
                or old.get("html_path") != item["HTML_reference"]["path"]
                or old.get("html_sha256") != item["HTML_reference"]["sha256"]
                or receipt.get("html_sha256") != item["HTML_reference"]["sha256"]
                or old.get("response_metadata_path") != item["metadata_reference"]["path"]
                or old.get("response_metadata_sha256") != item["metadata_reference"]["sha256"]
                or replay.get("status") != "IDENTITY_REVIEW" or replay.get("reason") != "ACTIVE_CHILD_ASIN_CHANGED"
                or replay.get("observed_active_asins") != observed or len(observed) != 1
                or not asin_from_url(task["request_url"]) or observed[0] == asin_from_url(task["request_url"])
                or location.is_spain is not True or location.postal_code != "28001"):
            raise ValueError("IDENTITY_QUARANTINE_NOT_PROVEN_ISOLATED_CHILD_CHANGE")
        out[task["task_id"]] = {"not_success": True, "item": item, "reason": old["reason"]}
    return out


def stage_tasks(bridge, stage, count, auth=None):
    """T2 still requires 100 true successes; FINAL retains all frozen tasks."""
    q = reviewed_identity_quarantines(bridge.root, bridge.plan, auth)
    if stage != "T2" or not q:
        return bridge.plan["tasks"][:count], q
    if any(t["task_id"] in q for t in bridge.plan["tasks"][:30]):
        raise ValueError("T1_FIRST_THIRTY_CANNOT_BE_SUBSTITUTED")
    selected = [t for t in bridge.plan["tasks"] if t["task_id"] not in q][:count]
    if len(selected) != count:
        raise ValueError("T2_TRUE_SUCCESS_SCOPE_SHORTFALL")
    return selected, q
