"""Persistent, read-only hourly report; no browser, retries or Gate writes."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..multisource.storage import immutable_json, immutable_bytes, read_sealed

SHANGHAI = timezone(timedelta(hours=8))


def build_report(root, sample, at=None):
    root = Path(root)
    at = at or datetime.now(timezone.utc)
    plan = json.loads((root / "detail_plan_5500.json").read_text(encoding="utf8"))
    authorizations = [root / "authorization.delivery_location_1.approved.json", root / "authorization.approved.json"]
    auth = json.loads(next(p for p in authorizations if p.exists()).read_text(encoding="utf8"))
    outcomes = [read_sealed(p) for p in (root / "tasks").glob("*/checkpoint.json")]
    counts = Counter(r.get("status", "UNKNOWN") for r in outcomes)
    success_rows = [r for r in outcomes if r.get("status") in {"SUCCESS", "REUSED_VALID_CACHE"}]
    live = [r for r in success_rows if r["status"] == "SUCCESS"]
    recent = [r for r in live if r.get("saved_at") and at-timedelta(hours=1) <= datetime.fromisoformat(r["saved_at"]) <= at]
    receipts = [read_sealed(p) for p in (root / "receipts").glob("*.json")]
    from .detail_budget5500 import uncertain_setup_page_holds
    setup_held = uncertain_setup_page_holds(root, plan)
    from .detail_budget5500 import unobserved_manual_page_holds
    manual_held=unobserved_manual_page_holds(root,plan)
    held=setup_held+manual_held
    latest = max((r.get("started_at", "") for r in receipts), default=None)
    gates = {}
    for name, p in (("local", root / "access_gate.json"), ("shared", Path(plan["shared_gate_path"]))):
        gates[name] = read_sealed(p).get("state") if p.exists() else ("NOT_CREATED" if name == "local" else "MISSING")
    status = sample.get("worker_status", {})
    collecting = status.get("phase", "").endswith("COLLECTING") and bool(sample.get("process"))
    local_three = collecting and status.get('local_processing_lanes') == 3 and status.get('network_parallelism') == 1
    parallel_three = collecting and status.get('local_processing_lanes') == 3 and status.get('network_parallelism') == 3
    parallel_two = collecting and status.get('local_processing_lanes') == 2 and status.get('network_parallelism') == 2
    parallel_five = collecting and status.get('local_processing_lanes') == 5 and status.get('network_parallelism') == 5
    parallel_local = local_three or parallel_three or parallel_two or parallel_five
    mode = ('FIVE_ASYNC_CAPTURE_LANES_GROUPED_3_PLUS_2' if parallel_five else 'TWO_ASYNC_CAPTURE_LANES_1000_TARGET' if parallel_two else 'THREE_ASYNC_CAPTURE_LANES' if parallel_three else
            'THREE_LOCAL_PARSERS_SERIAL_NETWORK' if local_three else 'SERIAL') if collecting else 'STOPPED_OR_NOT_COLLECTING'
    parser_counts = Counter(r.get('local_parser_pid') for r in live if r.get('local_parser_pid') and
                            r.get('local_execution_authorization_sha256') == status.get('authorization_sha256'))
    lanes = ([dict(lane=i+1, state='LOCAL_PARSER_COMPLETIONS_OBSERVED', parser_pid=pid, success=n)
              for i, (pid, n) in enumerate(sorted(parser_counts.items()))] if parallel_local else
             [dict(lane=1, state='SERIAL_RUNNING' if collecting else 'STOPPED', success=len(live)),
              dict(lane=2, state='NOT_ENABLED', success=0), dict(lane=3, state='NOT_ENABLED', success=0)])
    if parallel_local:
        lanes.extend(dict(lane=i+1, state='NO_COMPLETION_OBSERVED_YET', success=0) for i in range(len(lanes),5 if parallel_five else 2 if parallel_two else 3))
    # Callback arrival clocks are retained as diagnostics, never as HTTP proof.
    events=[]
    for r in receipts:
        if (status.get('authorization_sha256') and r.get('authorization_sha256')==status['authorization_sha256']
                and r.get('document_started_at') and r.get('document_response_at')):
            start=datetime.fromisoformat(r['document_started_at'])
            end=datetime.fromisoformat(r['document_response_at'])
            if end>=start:
                events.extend([(start,1),(end,-1)])
    active=peak=0
    for _,change in sorted(events):
        active+=change;peak=max(peak,active)
    from .browser_network_timing5500 import network_overlap
    current_completed = [r for r in receipts if status.get('authorization_sha256')
                         and r.get('authorization_sha256') == status['authorization_sha256']
                         and r.get('state') == 'NORMAL']
    snapshots = [r['browser_network_timing'] for r in current_completed
                 if r.get('browser_network_timing', {}).get('requests')]
    missing_browser_timing = len(current_completed)-len(snapshots)
    try:
        network = network_overlap(snapshots)
    except ValueError as exc:
        network = dict(coverage_complete=False, body_coverage_complete=False,
                       request_to_first_byte_peak=None, request_to_body_end_peak=None,
                       evidence_error=str(exc))
    network.pop('intervals', None)  # Full original intervals remain in receipts.
    network['completed_receipts_without_browser_timing'] = missing_browser_timing
    coverage = network['coverage_complete'] and missing_browser_timing == 0
    pending = len(plan["tasks"])-len(success_rows)
    speed = len(recent)  # Complete wall-clock hour, including pauses; no invented active speed.
    collect_seconds = pending/speed*3600 if speed else None
    qa_seconds = None
    qa_reference = None
    for name in ("T2", "T1"):
        p = root / (name+"_acceptance.json")
        if p.exists():
            proof = read_sealed(p)
            duration, count = proof.get("independent_audit_elapsed_seconds"), proof.get("success_count", 0)
            if proof.get("passed") is True and duration and count:
                qa_seconds = max(3600, duration/count*len(plan["tasks"])*1.2+900)
                qa_reference = str(p)
                break
    eta = (at+timedelta(seconds=collect_seconds+qa_seconds)).isoformat() if collect_seconds is not None and qa_seconds else None
    empty = not recent
    reason = status.get("reason") or ("LATEST_SUCCESS_OUTSIDE_THIS_HOUR" if live else "NO_SUCCESSFUL_DETAIL_YET")
    actions = []
    for p in (root / "maintenance_actions").glob("*.json"):
        action = json.loads(p.read_text(encoding="utf8"))
        if action.get("at") and at-timedelta(hours=1) <= datetime.fromisoformat(action["at"]) <= at:
            actions.append(action)
    return dict(report_time_beijing=at.astimezone(SHANGHAI).isoformat(), report_time_utc=at.isoformat(),
        run_id=plan["run_id"], actual_state=status.get("phase", "UNKNOWN"), mode=mode,
        total_tasks=len(plan["tasks"]), tasks_with_checkpoint=len(outcomes), success_details=len(success_rows),
        live_success_details=len(live), cache_reused=counts["REUSED_VALID_CACHE"], real_new_details_last_hour=len(recent),
        remaining_tasks=pending, failures_and_identity_review={k:v for k,v in counts.items() if k not in {"SUCCESS", "REUSED_VALID_CACHE"}},
        paid_requests=len(receipts), conservative_budget_reservation=held, effective_request_budget_used=len(receipts)+held,
        request_cap=auth["max_requests"], remaining_request_budget=auth["max_requests"]-len(receipts)-held,
        setup_budget_used_upper_bound=sum(r.get("request_role")=="DELIVERY_SETUP" for r in receipts)+setup_held,
        unobserved_manual_PAGE_budget_holds=manual_held,
        request_kinds=dict(Counter(r.get("kind", "PAGE") for r in receipts)),
        latest_success_at=max((r.get("saved_at", "") for r in live), default=None), latest_request_at=latest,
        lanes=lanes,
        configured_network_parallelism=status.get('network_parallelism',1),
        target_requests_per_hour=status.get('target_requests_per_hour'),
        effective_dispatch_interval_seconds=status.get('effective_dispatch_interval_seconds'),
        python_callback_document_event_peak=peak if events else None,
        observed_document_HTTP_peak_inflight=network['request_to_first_byte_peak'] if coverage else None,
        observed_document_HTTP_body_peak_inflight=network['request_to_body_end_peak']
            if coverage and network['body_coverage_complete'] else None,
        browser_network_timing_evidence=network,
        HTTP_overlap_scope='CURRENT_AUTHORIZATION_BROWSER_CDP_SEND_TO_FIRST_BYTE_ONLY',
        access_gates=gates, latest_error_or_STOP=reason, process_evidence=sample.get("process"),
        average_live_success_per_wall_clock_hour=speed, remaining_collection_seconds=collect_seconds,
        full_QA_seconds=qa_seconds, ETA_including_full_QA=eta, QA_measurement_reference=qa_reference,
        QA_projection_model="max(3600, measured_stage_audit_seconds/stage_count*total*1.2+900); estimate only",
        ETA_limitation="Estimate includes pauses and projected full QA; unknown until actual stage audit and live growth exist; no completion guarantee.",
        suspected_stall=bool(sample.get("suspected_stall")) or bool(pending and not sample.get("process")),
        hourly_summary=("本小时无新增，当前状态为%s，原因已检查：%s。" % (status.get("phase", "UNKNOWN"), reason)) if empty else "本小时真实新增%d条详情。" % len(recent),
        problems_this_hour=[reason] if empty else [], repair_actions_this_hour=actions,
        report_independent_of_collector=True, local_report_not_claimed_as_message_delivery=True,
        request_deadline=auth["request_deadline"], final_deadline=auth["deadline"])


def persist_hour(root, sample, at=None):
    at = at or datetime.now(timezone.utc)
    # One immutable report per local hour even if the independent monitor restarts.
    stem = at.astimezone(SHANGHAI).strftime("%Y%m%d_%H00")
    target = Path(root) / "hourly_reports" / (stem+".json")
    if target.exists():
        return target
    report = build_report(root, sample, at)
    immutable_json(target, report)
    text = ("时间：{report_time_beijing}\n运行：{run_id}\n状态：{actual_state} / {mode}\n"
        "成功详情：{success_details}/{total_tasks}；本小时真实新增：{real_new_details_last_hour}\n"
        "确认请求：{paid_requests}/{request_cap}；历史不确定PAGE预留：{conservative_budget_reservation}；有效已用额度：{effective_request_budget_used}；剩余额度：{remaining_request_budget}\n"
        "最近请求：{latest_request_at}\n最近成功：{latest_success_at}\n"
        "{hourly_summary}\n门禁：{access_gates}\n"
        "处理通道状态：{lanes}\n完整交付ETA：{ETA_including_full_QA}；{ETA_limitation}\n"
        "本文件是本地报告，主动消息送达需另行验证。\n").format(**report)
    immutable_bytes(target.with_suffix(".txt"), text.encode("utf8"))
    return target
