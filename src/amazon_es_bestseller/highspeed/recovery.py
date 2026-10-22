"""Bounded, explicitly delegated recheck of an ordinary NETWORK_ERROR only."""
from pathlib import Path
import time

from ..access.detector import detect_access_status
from ..multisource.storage import atomic_json, digest, file_lock, immutable_json, read_sealed, sealed
from .adapters import parse_page
from .authorization import authorize_request, validate_authorization
from .budgets import aware_time, utc_text, validate_plan, budget_reason


def recheck_network_error(store, plan, authorization, recovery, transport, *, sleep=time.sleep):
    """Charge one load and at most one refresh; never retry access restrictions.

    This is separate from normal resume authorization: an explicit new user
    instruction delegates verification of this exact pre-existing network gate.
    Successful source-bound evidence is required before the gate is restored.
    """
    validate_plan(plan)
    shared = Path(plan["shared_access_gate_path"])
    with store.run_lock(plan["run_id"]), file_lock(shared.parent / "highspeed_live.lock"):
        prior = read_sealed(shared)
        def check_local_gate():
            local=store.get_gate()
            if local.get('state') not in {'NORMAL','NETWORK_ERROR'}:
                raise ValueError('RECOVERY_LOCAL_GATE_RESTRICTED')
            if recovery.get('local_gate_sha256') and digest(local)!=recovery['local_gate_sha256']:
                raise ValueError('RECOVERY_LOCAL_GATE_CHANGED')
        check_local_gate()
        if (prior.get("state") != "NETWORK_ERROR" or recovery.get("approved") is not True
                or recovery.get("agent_verification_authorized") is not True
                or recovery.get("gate_sha256") != digest(prior)
                or recovery.get("store_root") != str(store.root)
                or recovery.get("run_id") != plan["run_id"]
                or not recovery.get("approved_by") or not recovery.get("approval_id")
                or not recovery.get("reviewed_at") or not recovery.get("expires_at")
                or aware_time(recovery["reviewed_at"]) > aware_time()
                or aware_time(recovery["expires_at"]) <= aware_time()
                or type(recovery.get("max_attempts")) is not int or not 1 <= recovery["max_attempts"] <= 2):
            raise ValueError("EXACT_USER_DELEGATED_NETWORK_RECOVERY_REQUIRED")
        source = next(s for s in plan["sources"] if s["source_id"] == recovery["source_id"])
        validate_authorization(plan,authorization,{"state":"NORMAL"},now=aware_time(),store_root=store.root)
        url = recovery["page_url"]
        if url not in source["page_urls"]:
            raise ValueError("RECOVERY_SOURCE_OUTSIDE_APPROVED_PLAN")
        authorize_request(url, source, authorization, kind="PAGE")
        scope = authorization["sources"][source["source_id"]]
        proof = scope["evidence"]
        if not proof.get("reviewed") or digest(Path(proof["path"]).read_bytes()) != proof["sha256"]:
            raise ValueError("RECOVERY_SOURCE_PROOF_INVALID")
        store.begin_run(plan, started_at=recovery.get("task_started_at"))
        immutable_json(store.root / "recovery_authorizations" / (digest(recovery)[:24]+".json"), sealed(recovery))
        result = None
        for attempt in range(recovery["max_attempts"]):
            if aware_time(recovery["expires_at"]) <= aware_time():
                raise ValueError("RECOVERY_AUTHORIZATION_EXPIRED")
            latest=store.latest_request_time()
            if latest:
                remaining=plan["request_pacing_seconds"]-(aware_time()-aware_time(latest)).total_seconds()
                if remaining>0:
                    sleep(remaining)
            # Pacing can cross an approval deadline or an external gate change.
            # Recheck immediately before charging a request, including refreshes.
            now = aware_time()
            if (aware_time(recovery["expires_at"]) <= now
                    or aware_time(recovery["reviewed_at"]) > now):
                raise ValueError("RECOVERY_AUTHORIZATION_EXPIRED")
            current_gate = read_sealed(shared)
            if (current_gate.get("state") != "NETWORK_ERROR"
                    or digest(current_gate) != recovery["gate_sha256"]):
                raise ValueError("RECOVERY_GATE_CHANGED")
            check_local_gate()
            validate_authorization(plan,authorization,{"state":"NORMAL"},now=now,store_root=store.root)
            # Recovery spends the original run's time and family allocation;
            # it is not an independent pool or a reset of the stopped run.
            run=store.get_run(plan['run_id'])
            reason=budget_reason(plan,store.budget_counters(plan['run_id']),now=now,
                started_at=run['started_at'],kind='PAGE')
            if not reason and plan.get('collection_contract')=='V4_THREE_SOURCE_5001':
                from .three_contract import family_budget_for_store
                reason=family_budget_for_store(store,plan,source,'PAGE')
            if reason:raise ValueError(reason)
            request = store.reserve_request(plan["run_id"], {"kind":"PAGE","url":url,
                "observed_at":utc_text(now),"origin":"LIVE_CAPTURE","source_id":source["source_id"],
                "source_type":source["source_type"],"recovery_attempt":attempt+1})
            started=time.perf_counter()
            network_exception=False
            try:
                fetched=transport.fetch_page(url)
                response=dict(fetched) if isinstance(fetched,dict) else dict(vars(fetched))
            except Exception as exc:
                network_exception=True
                response={"status_code":None,"url":None,"text":"","error":type(exc).__name__+": "+str(exc)}
            state="NETWORK_ERROR" if network_exception else detect_access_status(response.get("status_code"),response.get("text","")).value
            response={"status_code":response.get("status_code"),"final_url":response.get("url"),
                "text":response.get("text", ""),"error":response.get("error"),"access_state":state,
                "origin":"LIVE_CAPTURE","observed_at":request['observed_at'],"request_url":url,"request_method":"GET",
                "source_id":source["source_id"],"source_type":source["source_type"],
                "research_category":source.get("research_category"),"duration_seconds":time.perf_counter()-started}
            result=store.save_response(plan["run_id"],request,response)
            store.append_event(plan["run_id"],{"kind":"RECOVERY_REQUEST","stage":"navigation",
                "request_kind":"PAGE","duration_seconds":response["duration_seconds"],"access_state":state,
                "source_id":source["source_id"],"source_type":source["source_type"],"recovery_attempt":attempt+1})
            if state == "NORMAL":
                authorize_request(result["final_url"],source,authorization,kind="PAGE")
                parser=parse_page
                if plan.get('snapshot_contract_version')==4:
                    from .v4_adapters import parse_v4_page
                    parser=parse_v4_page
                parsed=parser(result["text"],source=source,run_id=plan["run_id"],page_url=url,
                    page_number=source["page_urls"].index(url)+1,observed_at=result["observed_at"],
                    evidence_path=result["html_path"],http_status=result["status_code"],origin="LIVE_CAPTURE")
                proof=parsed['page'].get('legacy_page',{}) if plan.get('snapshot_contract_version')==4 else parsed['page']
                if (result["status_code"] != 200 or not proof.get("source_type_verified")
                        or not proof.get("source_context_valid") or not parsed["observations"]
                        or plan.get('snapshot_contract_version')==4 and parsed['page']['status']!='VERIFIED_WITH_PRODUCTS'):
                    state="UNKNOWN"
                    response["error"]="RECOVERY_PAGE_NOT_SOURCE_BOUND_PRODUCT_LIST"
                else:
                    evidence={"prior_gate":prior,"user_recovery_authorization":recovery,
                        "normal_verification_receipt":result["evidence_path"],"verified_at":utc_text(),
                        "verified_parser_contract":plan.get('snapshot_contract_version',2),
                        "agent_handled":True,"human_handled":False}
                    path=store.root/"gate_recoveries"/(digest(evidence)[:24]+".json")
                    immutable_json(path,sealed(evidence))
                    handled=[r["request_id"] for r in store.requests(plan["run_id"])
                        if r["status"] not in {"NORMAL","RESERVED"}]
                    local={"state":"NORMAL","agent_handled_request_ids":handled,"agent_recovery_evidence":str(path),
                        "approved_by":recovery["approved_by"],"approval_id":recovery["approval_id"],
                        "observed_at":utc_text()}
                    atomic_json(store.root/"access_gate.json",sealed(local))
                    atomic_json(shared,sealed({"state":"NORMAL","approval_id":recovery["approval_id"],
                        "previous_gate":prior,"agent_recovery_evidence":str(path),"observed_at":utc_text()}))
                    store.append_event(plan["run_id"],{"kind":"AUTHORIZED_NETWORK_RECOVERY_COMPLETE",
                        "stage":"recovery","evidence_path":str(path),"initial_card_count":len(parsed["observations"])})
                    return result
            if state != "NETWORK_ERROR" or attempt+1 == recovery["max_attempts"]:
                gate={"state":state,"evidence_path":result["evidence_path"],"previous_gate":prior,
                    "approval_id":recovery["approval_id"],"observed_at":utc_text()}
                atomic_json(shared,sealed(gate))
                store.set_gate(state,evidence_path=result["evidence_path"])
                raise ValueError("RECOVERY_STOPPED_"+state)
        raise ValueError("RECOVERY_DID_NOT_COMPLETE")
