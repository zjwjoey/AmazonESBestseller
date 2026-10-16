"""Keep an independently reviewed changed child as a failure, never a success.

This permits the next original frozen task, not a replacement or a request retry.
Access failures and unresolved ambiguous identity cannot enter this path.
"""
import json
import re
from pathlib import Path

from ..identity import asin_from_url
from ..access.location import inspect_delivery_location
from ..multisource.storage import digest, read_sealed
from .quick_selection import file_sha


def owned_changed_child_parent_evidence(task, html, meta):
    """Prove a DIFFERENT owned selected child, solely to keep it a failure.

    A legacy parser may list the child plus its explicit canonical parent as
    two identities. This never permits accepting either as the requested ASIN.
    """
    from bs4 import BeautifulSoup
    from ..access.detector import detect_access_status
    from .detail_bridge5500 import safe_url
    from .detail_v2 import _json_after
    requested = asin_from_url(task['request_url'])
    final = meta.get('final_url') or ''
    if (not requested or not safe_url(final) or asin_from_url(final) != requested
            or meta.get('requested_url') != task['request_url']
            or detect_access_status(meta.get('status_code'), html).value != 'NORMAL'):
        return None
    soup = BeautifulSoup(html, 'lxml')
    active = {str(e.get('value') or '').strip().upper() for e in
              soup.select('input#ASIN,input#productAsin,input[name=ASIN]') if e.get('value')}
    if len(active) != 1:
        return None
    current = next(iter(active))
    if not re.fullmatch(r'[A-Z0-9]{10}', current) or current == requested or len(soup.select('#ppd')) != 1:
        return None
    owned = soup.select('#ppd #twister_feature_div')
    canons = soup.select('link[rel=canonical]')
    buybox = {str(e.get('value') or '').strip().upper() for e in
              soup.select('#ppd #buybox input#ASIN[name=ASIN]')}
    if len(owned) != 1 or len(canons) != 1 or buybox != {current}:
        return None
    canonical = str(canons[0].get('href') or '')
    if not safe_url(canonical):
        return None
    scripts = [e.get_text() for e in owned[0].select('script')
               if re.search(r"P\.register\(['\"]twister-js-init-dpx-data['\"]\s*,", e.get_text())]
    if len(scripts) != 1:
        return None
    script = scripts[0]
    def unique(key):
        values = re.findall(r'"'+key+r'"\s*:\s*"([A-Z0-9]{10})"', script)
        return values[0] if values and len(set(values)) == 1 else None
    parent = unique('parentAsin')
    display = _json_after(script, '"dimensionValuesDisplayData"')
    explicit = {str(e.get('value') or '').strip().upper() for e in
                soup.select('input#parentASIN,input[name=parentASIN]')}
    if (unique('currentAsin') != current or unique('landingAsin') != requested
            or not parent or parent in {current, requested} or asin_from_url(canonical) != parent
            or not isinstance(display, dict) or not isinstance(display.get(current), list)
            or not display[current] or (explicit and explicit != {parent})):
        return None
    return {'rule':'OWNED_CURRENT_CHILD_DIFFERS_REQUESTED_CANONICAL_EXPLICIT_PARENT_V1',
            'requested_asin':requested,'current_asin':current,'parent_asin':parent,'landing_asin':requested,
            'source':'#ppd #twister_feature_div / twister-js-init-dpx-data',
            'buybox_input':'#ppd #buybox input#ASIN[name=ASIN]'}


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
        simple_change = (old.get('reason') == 'ACTIVE_CHILD_ASIN_CHANGED'
                         and replay.get('reason') == 'ACTIVE_CHILD_ASIN_CHANGED'
                         and len(observed) == 1 and observed[0] != asin_from_url(task['request_url']))
        owned_change = False
        if (old.get('reason') == 'ACTIVE_PRODUCT_ASIN_NOT_UNAMBIGUOUS'
                and replay.get('reason') == 'ACTIVE_PRODUCT_ASIN_NOT_UNAMBIGUOUS'):
            proof = owned_changed_child_parent_evidence(task, html, meta)
            owned_change = bool(proof and item.get('owned_child_parent_change_evidence') == proof
                                and len(observed) == 2
                                and set(observed) == {proof['current_asin'], proof['parent_asin']})
        if (old.get("status") != "IDENTITY_REVIEW" or not (simple_change or owned_change)
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
                or replay.get("status") != "IDENTITY_REVIEW"
                or replay.get("observed_active_asins") != observed
                or not asin_from_url(task["request_url"])
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
