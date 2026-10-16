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


def test_read_only_verification_cannot_initialize_again(tmp_path):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_read_only_verification
    root, plan, auth, a = fixture(tmp_path)
    with pytest.raises(ValueError, match="READ_ONLY_VERIFICATION"):
        validate_read_only_verification(root, plan, auth, a)


def verification_fixture(tmp_path):
    root,plan,auth,a=fixture(tmp_path)
    attempt=root/"delivery_setup.attempt.json";immutable_json(attempt,sealed(dict(max_attempts=1)))
    failure=root/"delivery_setup.failure.json";immutable_json(failure,sealed(dict(error_type="DeliveryLocationError",pending_receipts=[])))
    sp=root/"receipts/delivery_setup_01.json"
    immutable_json(sp,sealed(dict(request_role="DELIVERY_SETUP",kind="PAGE",method="GET",state="NORMAL",status_code=200,
        request_url=a["initial_product_URL"],started_at=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat())))
    book=root/"user_book.txt";book.write_text("Existing setup budget includes document verification; no second initialization.",encoding="utf8")
    def ref(p):return dict(path=str(p),sha256=file_sha(p))
    v=dict(scope="ONE_READ_ONLY_DOCUMENT_VERIFICATION_WITHIN_ORIGINAL_SETUP",reviewed=True,
        reviewer="agent_under_existing_user_authorization",run_id=plan["run_id"],plan_sha256=digest(plan),
        task_id=a["task_id"],initial_product_URL=a["initial_product_URL"],max_verification_pages=1,
        mutation_allowed=False,repeat_initialization_allowed=False,original_max_setup_attempts_preserved=1,
        max_total_setup_requests=8,original_amendment_reference=auth["delivery_setup_reference"],
        initial_attempt_reference=ref(attempt),initial_failure_reference=ref(failure),initial_setup_receipt_reference=ref(sp),
        existing_user_delegation_reference=ref(book))
    vp=root/"verification_review.json";immutable_json(vp,v)
    auth["delivery_setup_verification_reference"]=ref(vp)
    return root,plan,auth,a,v


@pytest.mark.parametrize("destination,passed", [("Enviar a Madrid 28001",True),("Enviar a Taiwán",False)])
def test_verification_only_one_GET_never_clicks_fills_or_submits(tmp_path,monkeypatch,destination,passed):
    from types import SimpleNamespace as NS
    from amazon_es_bestseller.collection.delivery_setup5500 import verify_existing_setup_without_UI
    from amazon_es_bestseller.multisource.storage import read_sealed
    root,plan,auth,a,v=verification_fixture(tmp_path)
    before=file_sha(root/"delivery_setup.attempt.json")
    calls=[];blocked=[]
    class Page:
        url=a["initial_product_URL"];main_frame=object()
        def unroute(self,*args):pass
        def route(self,pattern,handler):self.handler=handler
        def content(self):return '<a id="nav-global-location-popover-link">'+destination+'</a>'
        def locator(self,*args):pytest.fail("verification must not use any UI control")
    page=Page()
    def goto(url):
        req=NS(url=url,method="GET",frame=page.main_frame,resource_type="document",is_navigation_request=lambda:True)
        page.handler(NS(request=req,continue_=lambda:calls.append(url),abort=lambda:pytest.fail("GET should be allowed")))
        # An unsolicited shipping POST is blocked, not sent or charged as success.
        req=NS(url="https://www.amazon.es/portal-migration/hz/glow/address-change",method="POST",resource_type="xhr",is_navigation_request=lambda:False)
        page.handler(NS(request=req,continue_=lambda:pytest.fail("verification must never POST"),abort=lambda:blocked.append("POST")))
        return 200
    session=NS(page=page,access_blocked=False,document_responses=[],goto=goto,wait_for_product_page=lambda:None,
        ensure_spain_delivery=lambda *args,**kw:pytest.fail("must not initialize again"))
    bridge=NS(root=root,plan=plan,validate_authorization=lambda auth:None,gates_normal=lambda:None)
    monkeypatch.setattr("amazon_es_bestseller.collection.detail_bridge5500.parse_candidate_detail",
        lambda *args:dict(status="SUCCESS",identity_status="EXACT_ACTIVE_PRODUCT_MATCH"))
    if passed:
        verify_existing_setup_without_UI(session,bridge,auth)
        assert read_sealed(root/"delivery_setup.complete.json")["initialization_attempt_count_preserved"]==1
    else:
        with pytest.raises(ValueError,match="DESTINATION_OR_IDENTITY_NOT_VERIFIED"):
            verify_existing_setup_without_UI(session,bridge,auth)
        assert not (root/"delivery_setup.complete.json").exists()
    assert calls==[a["initial_product_URL"]] and blocked==["POST"]
    assert file_sha(root/"delivery_setup.attempt.json")==before
    assert len(list((root/"receipts").glob("*.json")))==3
    assert not (root/"receipts/first_2.json").exists()
    if passed:
        verify_existing_setup_without_UI(session,bridge,auth)  # Proof reuse, no page replay.
    else:
        with pytest.raises(ValueError,match="NOT_REPLAYABLE"):
            verify_existing_setup_without_UI(session,bridge,auth)
    assert len(calls)==1


@pytest.mark.parametrize("key,value", [("mutation_allowed",True),("repeat_initialization_allowed",True),
    ("max_verification_pages",2),("original_max_setup_attempts_preserved",2)])
def test_verification_review_cannot_expand_into_another_setup(tmp_path,key,value):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_read_only_verification
    from amazon_es_bestseller.multisource.storage import atomic_json
    root,plan,auth,a,v=verification_fixture(tmp_path)
    v[key]=value;vp=Path(auth["delivery_setup_verification_reference"]["path"]);atomic_json(vp,v)
    auth["delivery_setup_verification_reference"]["sha256"]=file_sha(vp)
    with pytest.raises(ValueError,match="SCOPE_INVALID"):
        validate_read_only_verification(root,plan,auth,a)


@pytest.mark.parametrize("change", [None,"POST","cross_site","wrong_endpoint","wrong_status"])
def test_verification_accepts_actual_readonly_modal_receipt_not_submission(tmp_path,change):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_read_only_verification
    from amazon_es_bestseller.multisource.storage import read_sealed,atomic_json
    root,plan,auth,a,v=verification_fixture(tmp_path)
    saved=Path(__file__).parent/'fixtures/detail_bridge5500/actual_setup_readonly_modal_receipt.json'
    r=read_sealed(saved)
    # Exact observed read-only UI shape; test run identity is bound separately.
    r.update(run_id=plan['run_id'],task_id=a['task_id'],plan_sha256=digest(plan))
    if change=='POST':r['method']='POST'
    if change=='cross_site':r['request_url']=r['request_url'].replace('www.amazon.es','evil.test')
    if change=='wrong_endpoint':r['request_url']='https://www.amazon.es/portal-migration/hz/glow/address-change'
    if change=='wrong_status':r['status_code']=403
    sp=root/'receipts/delivery_setup_01.json';atomic_json(sp,sealed(r))
    v['initial_setup_receipt_reference']=dict(path=str(sp),sha256=file_sha(sp))
    vp=Path(auth['delivery_setup_verification_reference']['path']);atomic_json(vp,v)
    auth['delivery_setup_verification_reference']['sha256']=file_sha(vp)
    if change is None:
        assert validate_read_only_verification(root,plan,auth,a)==v
    else:
        with pytest.raises(ValueError,match='NOT_REPLAYABLE'):
            validate_read_only_verification(root,plan,auth,a)


@pytest.mark.parametrize('change',[None,'unapproved','missing_verification','changed_html','verification_passed','submitted'])
def test_explicit_continuation_after_failed_readonly_verification(tmp_path,change):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_continuation
    from amazon_es_bestseller.multisource.storage import read_sealed,atomic_json
    import gzip
    root,plan,auth,a,v=verification_fixture(tmp_path)
    def ref(p):return dict(path=str(p),sha256=file_sha(p))
    saved=Path(__file__).parent/'fixtures/detail_bridge5500/actual_setup_readonly_modal_receipt.json'
    r=read_sealed(saved);r.update(run_id=plan['run_id'],task_id=a['task_id'],plan_sha256=digest(plan))
    sp=root/'receipts/delivery_setup_01.json';atomic_json(sp,sealed(r))
    hp=root/'initial_UI.html';hp.write_bytes(gzip.decompress((saved.parent/'actual_failed_glux_UI.html.gz').read_bytes()))
    fp=root/'delivery_setup.failure.json';atomic_json(fp,sealed(dict(error_type='DeliveryLocationError',pending_receipts=[],evidence=dict(html_sha256=file_sha(hp)))))
    # Kept real wrong-destination document, not fabricated success data.
    vh=root/'verification.html';vh.write_bytes(gzip.decompress((saved.parent/'actual_readonly_verification_taiwan.html.gz').read_bytes()))
    vr=root/'receipts/delivery_setup_02.json';immutable_json(vr,sealed(dict(request_role='DELIVERY_SETUP',kind='PAGE',method='GET',state='NORMAL',status_code=200,
        run_id=plan['run_id'],task_id=a['task_id'],plan_sha256=digest(plan),request_url=a['initial_product_URL'],final_url=a['initial_product_URL'],body_path=str(vh),body_sha256=file_sha(vh))))
    va=root/'delivery_setup.verification_1.attempt.json';immutable_json(va,sealed(dict(mutation_allowed=False)))
    vf=root/'delivery_setup.verification_1.result.json';immutable_json(vf,sealed(dict(passed=change=='verification_passed',no_UI_mutation_performed=True,
        product_parse_status='SUCCESS',identity_status='EXACT_ACTIVE_PRODUCT_MATCH',receipt_path=str(vr),receipt_sha256=file_sha(vr),html_path=str(vh),html_sha256=file_sha(vh))))
    p=dict(approved=change!='unapproved',approved_by='user',approval_id='explicit_additional_once',
        scope='ONE_ADDITIONAL_NORMAL_UI_INITIALIZATION_AFTER_UNCONFIRMED_UI_FAILURE',run_id=plan['run_id'],plan_sha256=digest(plan),
        task_id=a['task_id'],initial_product_URL=a['initial_product_URL'],shipping_postal_code='28001',max_total_setup_attempts=2,max_total_setup_requests=8,
        total_request_cap_unchanged=auth['max_requests'],request_deadline_unchanged=auth['request_deadline'],final_deadline_unchanged=auth['deadline'],
        original_amendment_reference=auth['delivery_setup_reference'],initial_attempt_reference=ref(root/'delivery_setup.attempt.json'),
        initial_failure_reference=ref(fp),initial_setup_receipt_reference=ref(sp),initial_failed_UI_snapshot_reference=ref(hp),
        failed_read_only_verification_reference=ref(vf),verification_attempt_reference=ref(va),verification_receipt_reference=ref(vr),verification_html_reference=ref(vh))
    if change=='missing_verification':p.pop('failed_read_only_verification_reference')
    pp=root/'continuation.json';immutable_json(pp,p);auth['delivery_setup_continuation_reference']=ref(pp)
    if change=='changed_html':vh.write_text('changed',encoding='utf8')
    if change=='submitted':immutable_json(root/'receipts/delivery_setup_03.json',sealed(dict(request_role='DELIVERY_SETUP',kind='OTHER',method='POST',state='NORMAL')))
    if change is None:assert validate_continuation(root,plan,auth,a)==p
    else:
        with pytest.raises(ValueError):validate_continuation(root,plan,auth,a)
