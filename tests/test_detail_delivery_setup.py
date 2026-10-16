import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amazon_es_bestseller.multisource.storage import digest, immutable_json, sealed
from amazon_es_bestseller.collection.quick_selection import file_sha


def fixture(tmp_path):
    root = tmp_path / "run"; root.mkdir()
    task = dict(task_id="first", candidate_id="candidate", request_url="https://www.amazon.es/dp/B000000001")
    plan = dict(run_id="run", tasks=[task], shared_gate_path=str(tmp_path / "gate.json"))
    immutable_json(tmp_path / "gate.json", sealed({"state": "NORMAL"}))
    immutable_json(root / "detail_plan_5500.json", plan)
    now = datetime.now(timezone.utc)
    original = dict(run_id="run", plan_sha256=digest(plan), max_requests=6050, max_page_requests=6050,
                    max_other_requests=0, request_start_interval_seconds=3, starts_at=(now-timedelta(hours=1)).isoformat(),
                    request_deadline=(now+timedelta(hours=1)).isoformat(), deadline=(now+timedelta(hours=2)).isoformat())
    immutable_json(root / "authorization.approved.json", original)
    receipt = dict(run_id="run", task_id="first", request_url=task["request_url"], state="NORMAL", status_code=200,
                   started_at=(now-timedelta(minutes=1)).isoformat())
    immutable_json(root / "receipts/first_1.json", sealed(receipt))
    amendment = dict(approved=True, approved_by="user", approval_id="bounded", run_id="run", plan_sha256=digest(plan),
        scope="ONE_NORMAL_DELIVERY_LOCATION_INITIALIZATION_AND_FIRST_FAILED_TASK_RECHECK",
        original_authorization_reference=dict(path=str(root / "authorization.approved.json"), sha256=file_sha(root / "authorization.approved.json")),
        original_failed_receipt_reference=dict(path=str(root / "receipts/first_1.json"), sha256=file_sha(root / "receipts/first_1.json")),
        task_id="first", candidate_id="candidate", initial_product_URL=task["request_url"], shipping_postal_code="28001",
        max_setup_attempts=1, max_setup_requests=8, new_other_request_subcap=8, first_unsuccessful_task_max_attempts=2,
        total_request_cap_unchanged=6050, PAGE_cap_unchanged=6050, request_deadline_unchanged=original["request_deadline"],
        final_deadline_unchanged=original["deadline"], normal_UI_only=True, no_gate_clear_or_access_bypass=True,
        no_other_starting_product_or_ranking_URL=True, no_replay_successful_tasks=True)
    immutable_json(root / "delivery_location_amendment.approved.json", amendment)
    auth = {**original, "max_other_requests": 8, "max_attempts_per_task": 2,
        "delivery_setup_reference": dict(path=str(root / "delivery_location_amendment.approved.json"),
                                         sha256=file_sha(root / "delivery_location_amendment.approved.json"))}
    return root, plan, auth, amendment


def test_exact_amendment_binding_and_original_preserved(tmp_path):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_amendment
    root, plan, auth, amendment = fixture(tmp_path)
    before = file_sha(root / "authorization.approved.json")
    assert validate_amendment(root, plan, auth) == amendment
    assert file_sha(root / "authorization.approved.json") == before


@pytest.mark.parametrize("field,value", [("max_requests", 6051), ("max_other_requests", 9),
    ("deadline", "2099-01-01T00:00:00+00:00"), ("plan_sha256", "wrong")])
def test_amendment_cannot_expand_scope(tmp_path, field, value):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_amendment
    root, plan, auth, _ = fixture(tmp_path)
    auth[field] = value
    with pytest.raises(ValueError):
        validate_amendment(root, plan, auth)


@pytest.mark.parametrize("url,method,body,expected", [
    ("https://www.amazon.es/portal-migration/hz/glow/get-rendered-address-selections?deviceType=desktop", "GET", "", True),
    ("https://www.amazon.es/portal-migration/hz/glow/address-change", "POST", "zipCode=28001&locationType=LOCATION_INPUT", True),
    ("https://www.amazon.es/portal-migration/hz/glow/address-change", "POST", "zipCode=90210", False),
    ("https://www.amazon.es/gp/cart/add.html", "POST", "zipCode=28001", False),
    ("https://www.amazon.es/portal-migration/hz/glow/delete-address", "POST", "zipCode=28001", False),
    ("https://evil.test/portal-migration/hz/glow/address-change", "POST", "zipCode=28001", False),
])
def test_only_observed_normal_delivery_UI_request_scope(url, method, body, expected):
    from amazon_es_bestseller.collection.delivery_setup5500 import allowed_shipping_request
    assert allowed_shipping_request(url, method, body, "28001") is expected


def test_setup_requests_share_original_total_budget(tmp_path):
    from amazon_es_bestseller.collection.delivery_setup5500 import SetupLedger
    root, plan, auth, amendment = fixture(tmp_path)
    ledger = SetupLedger(root, plan, auth, amendment, lambda: None)
    before = len(list((root / "receipts").glob("*.json")))
    p = ledger.reserve("https://www.amazon.es/portal-migration/hz/glow/get-rendered-address-selections", "GET", "OTHER", "")
    assert len(list((root / "receipts").glob("*.json"))) == before + 1
    assert json.loads(p.read_text(encoding="utf8"))["payload"]["request_role"] == "DELIVERY_SETUP"
    with pytest.raises(ValueError, match="IN_FLIGHT"):
        ledger.reserve("https://www.amazon.es/portal-migration/hz/glow/get-rendered-address-selections", "GET", "OTHER", "")


def test_setup_cap_and_failure_are_not_auto_retried(tmp_path):
    from amazon_es_bestseller.collection.delivery_setup5500 import SetupLedger
    root, plan, auth, amendment = fixture(tmp_path)
    for i in range(8):
        immutable_json(root / f"receipts/setup_{i}.json", sealed(dict(state="NORMAL", kind="OTHER", request_role="DELIVERY_SETUP")))
    ledger = SetupLedger(root, plan, auth, amendment, lambda: None)
    with pytest.raises(ValueError, match="SETUP_BUDGET"):
        ledger.reserve("https://www.amazon.es/portal-migration/hz/glow/address-change", "POST", "OTHER", "zipCode=28001")


@pytest.mark.parametrize("status,body,state", [(403, "denied", "BLOCKED"), (429, "limited", "RATE_LIMITED"),
    (200, "<title>Robot Check</title>", "CHALLENGE"), (None, "", "UNKNOWN")])
def test_setup_real_status_or_challenge_stops_both_gates(tmp_path, status, body, state):
    from amazon_es_bestseller.collection.delivery_setup5500 import SetupLedger
    from amazon_es_bestseller.multisource.storage import read_sealed
    root, plan, auth, amendment = fixture(tmp_path)
    ledger = SetupLedger(root, plan, auth, amendment, lambda: None)
    p = ledger.reserve(amendment["initial_product_URL"], "GET", "PAGE")
    ledger.finish(p, status, body, amendment["initial_product_URL"])
    assert read_sealed(p)["state"] == state
    assert read_sealed(root / "access_gate.json")["state"] == state
    assert read_sealed(plan["shared_gate_path"])["state"] == state
    assert read_sealed(plan["shared_gate_path"])["previous_gate"]["state"] == "NORMAL"


def test_gate_rechecked_after_pacing_before_reservation(tmp_path):
    from amazon_es_bestseller.collection.delivery_setup5500 import SetupLedger
    root, plan, auth, amendment = fixture(tmp_path)
    calls = []
    def check():
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("NEW_RESTRICTION")
    ledger = SetupLedger(root, plan, auth, amendment, check)
    with pytest.raises(ValueError, match="NEW_RESTRICTION"):
        ledger.reserve(amendment["initial_product_URL"], "GET", "PAGE")
    assert len(list((root / "receipts").glob("*.json"))) == 1


def test_only_first_delivery_failure_retried_and_success_not_replayed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
    from amazon_es_bestseller.collection.delivery_setup5500 import verify_setup
    from amazon_es_bestseller.multisource.storage import atomic_json, read_sealed
    root, plan, auth, a = fixture(tmp_path)
    t = plan["tasks"][0]; t["selection_id"] = "selection"
    # Re-freeze test-only bindings after enriching the minimal task.
    atomic_json(root / "detail_plan_5500.json", plan)
    original = json.loads((root / "authorization.approved.json").read_text())
    original["plan_sha256"] = digest(plan); atomic_json(root / "authorization.approved.json", original)
    a["original_authorization_reference"]["sha256"] = file_sha(root / "authorization.approved.json")
    a["plan_sha256"] = auth["plan_sha256"] = digest(plan)
    atomic_json(root / "delivery_location_amendment.approved.json", a)
    auth["delivery_setup_reference"]["sha256"] = file_sha(root / "delivery_location_amendment.approved.json")
    slot = root / "tasks/first"; slot.mkdir(parents=True)
    old = dict(status="IDENTITY_REVIEW", reason="SPAIN_DELIVERY_LOCATION_NOT_VERIFIED",
               receipt_path=a["original_failed_receipt_reference"]["path"], candidate_id="candidate", run_id="run")
    immutable_json(slot / "checkpoint.json", sealed(old))
    immutable_json(slot / "response_metadata.json", {"delivery": "original Taiwan evidence"})
    oldsha = file_sha(slot / "response_metadata.json")
    html = '<a id="nav-global-location-popover-link">Enviar a Madrid 28001</a>'
    hp = root / "destination.html"; hp.write_text(html, encoding="utf8")
    sp = root / "receipts/delivery_setup_01.json"
    immutable_json(sp, sealed(dict(state="NORMAL", request_role="DELIVERY_SETUP", kind="PAGE",
        started_at=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat())))
    immutable_json(root / "delivery_setup.complete.json", sealed(dict(passed=True, plan_sha256=digest(plan),
        amendment_sha256=auth["delivery_setup_reference"]["sha256"], postal_code="28001", html_path=str(hp),
        html_sha256=file_sha(hp), receipts={str(sp):file_sha(sp)})))
    assert verify_setup(root, plan, auth) == a
    bridge = DetailBridge5500(root, plan)
    monkeypatch.setattr(bridge, "validate_authorization", lambda auth: None)
    monkeypatch.setattr("amazon_es_bestseller.collection.detail_bridge5500.parse_candidate_detail",
                        lambda *args: dict(status="SUCCESS", record=None))
    calls = []
    session = SimpleNamespace(page=SimpleNamespace(url=t["request_url"], content=lambda:html),
        goto=lambda url:calls.append(url) or 200, wait_for_product_page=lambda:None, wait_for_price_text=lambda:None)
    bridge.run(session, auth, until_completed=1)
    bridge.run(session, auth, until_completed=1)
    assert len(calls) == 1
    assert read_sealed(slot / "checkpoint.attempt_1.preserved.json") == old
    assert file_sha(slot / "response_metadata.json") == oldsha
    assert (slot / "response_metadata.attempt_2.json").exists()
    assert read_sealed(root / "receipts/first_2.json")["attempt"] == 2
    assert len(list((root / "receipts").glob("*.json"))) == 3


def test_consumed_initialization_needs_new_explicit_approval(tmp_path):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_continuation
    root, plan, auth, a = fixture(tmp_path)
    with pytest.raises(ValueError, match="NEW_EXPLICIT"):
        validate_continuation(root, plan, auth, a)


@pytest.mark.parametrize("change,expected", [(None,True), ("unapproved",False), ("three_attempts",False),
                                            ("already_submitted",False), ("changed_evidence",False)])
def test_additional_initialization_requires_bound_pre_submission_evidence(tmp_path, change, expected):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_continuation
    from amazon_es_bestseller.multisource.storage import atomic_json
    root, plan, auth, amendment = fixture(tmp_path)
    attempt = root / "delivery_setup.attempt.json"
    immutable_json(attempt, sealed(dict(started_at="fixed", max_attempts=1)))
    hp = root / "original_UI.html"; hp.write_text("real test UI fixture placeholder", encoding="utf8")
    failure = root / "delivery_setup.failure.json"
    immutable_json(failure, sealed(dict(error_type="DeliveryLocationError", pending_receipts=[],
        evidence=dict(html_sha256=file_sha(hp)))))
    sp = root / "receipts/delivery_setup_01.json"
    immutable_json(sp, sealed(dict(request_role="DELIVERY_SETUP",kind="PAGE",method="GET",state="NORMAL",status_code=200,
        request_url=amendment["initial_product_URL"])))
    def ref(p):return dict(path=str(p),sha256=file_sha(p))
    proposal = dict(approved=change!="unapproved",approved_by="user",approval_id="new_human_approval",
        scope="ONE_ADDITIONAL_NORMAL_UI_INITIALIZATION_AFTER_UNCONFIRMED_UI_FAILURE",run_id=plan["run_id"],
        plan_sha256=digest(plan),task_id=amendment["task_id"],initial_product_URL=amendment["initial_product_URL"],
        shipping_postal_code="28001",max_total_setup_attempts=3 if change=="three_attempts" else 2,
        max_total_setup_requests=8,total_request_cap_unchanged=auth["max_requests"],
        request_deadline_unchanged=auth["request_deadline"],final_deadline_unchanged=auth["deadline"],
        original_amendment_reference=auth["delivery_setup_reference"], initial_attempt_reference=ref(attempt),
        initial_failure_reference=ref(failure),initial_setup_receipt_reference=ref(sp),initial_failed_UI_snapshot_reference=ref(hp))
    pp=root/"continuation.json";immutable_json(pp,proposal)
    auth["delivery_setup_continuation_reference"]=ref(pp)
    if change=="already_submitted":
        immutable_json(root/"receipts/delivery_setup_02.json",sealed(dict(request_role="DELIVERY_SETUP",kind="OTHER",method="POST",state="NORMAL")))
    if change=="changed_evidence":hp.write_text("changed",encoding="utf8")
    if expected:
        assert validate_continuation(root,plan,auth,amendment)==proposal
    else:
        with pytest.raises(ValueError):validate_continuation(root,plan,auth,amendment)


@pytest.mark.parametrize("url,method,kind,expected", [
    ("https://www.amazon.es/portal-migration/hz/glow/address-change", "POST", "other", True),
    ("https://www.amazon.es/portal-migration/hz/glow/address-change", "GET", "script", True),
    ("https://www.amazon.es/gp/delivery/ajax/address-change.html", "POST", "script", True),
    ("https://www.amazon.es/ordinary.js", "GET", "script", False),
])
def test_shipping_requests_cannot_escape_accounting_by_resource_type(url, method, kind, expected):
    from amazon_es_bestseller.collection.delivery_setup5500 import requires_setup_accounting
    assert requires_setup_accounting(url,method,kind) is expected
