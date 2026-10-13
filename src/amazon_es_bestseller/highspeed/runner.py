"""Serial link-first runner with immutable receipts before any parsing."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone
from contextlib import nullcontext, ExitStack
from ..access.detector import detect_access_status
from ..multisource.storage import file_lock, read_sealed, atomic_json, immutable_json, sealed
from .authorization import authorize_request, validate_authorization
from .budgets import aware_time, budget_reason, utc_text, validate_plan
from .performance import RollingProgress
from ..multisource.contracts import SOURCE_ROOTS, canonical_page_url

def source_navigation_only(source, url, parsed):
    """A verified empty root may expose navigation, never valid product output."""
    page = parsed["page"]
    actual = canonical_page_url(url).rstrip("/")
    root = SOURCE_ROOTS.get(source.get("source_type"), "").rstrip("/")
    return bool(actual == root and page.get("status") == "FAILED"
        and page.get("access_status") == "NORMAL" and page.get("source_type_verified") is True
        and page.get("source_context_valid") is True and not parsed.get("observations")
        and page.get("raw_card_count", 0) == 0
        and set(page.get("flags", [])) == {"PAGE_CONTENT_MISSING"}
        and not (parsed.get("acp_pending") or page.get("acp_pending")))

class HighSpeedRunner:
    def __init__(self, store, *, clock=None, sleep=None, parser=None, gate_reader=None, progress=None, qa_auditor=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep or time.sleep
        self.parser = parser
        self.gate_reader = gate_reader
        self.progress = progress
        self.qa_auditor = qa_auditor

    def _gate(self, plan):
        if self.gate_reader:
            shared = self.gate_reader()
        elif plan.get("shared_access_gate_path"):
            path = Path(plan["shared_access_gate_path"])
            if not path.exists():
                raise ValueError("SHARED_GLOBAL_ACCESS_GATE_MISSING")
            shared = read_sealed(path)
        elif plan["stage"] == "T0":
            shared = {"state": "NORMAL"}
        else:
            raise ValueError("SHARED_GLOBAL_ACCESS_GATE_REQUIRED")
        local = self.store.get_gate() if hasattr(self.store, "get_gate") else {}
        return local if local.get("state", "NORMAL") != "NORMAL" else shared

    def run(self, plan, *, authorization=None, transport=None, resume=False, preheld_lock=False):
        entry_time = utc_text(self.clock())
        validate_plan(plan)
        origin = plan.get("origin", "OFFLINE_FIXTURE" if plan["stage"] == "T0" else "LIVE_CAPTURE")
        if (plan["stage"] == "T0" and origin not in {"OFFLINE_FIXTURE", "HISTORICAL_REPLAY"}) or (plan["stage"] != "T0" and origin != "LIVE_CAPTURE"):
            raise ValueError("STAGE_ORIGIN_MISMATCH")
        validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
        if origin == "LIVE_CAPTURE" and transport is None:
            raise ValueError("VALIDATED_TRANSPORT_REQUIRED")
        run_id = plan["run_id"]
        # Only the CLI holding this store's lock for the whole browser lifetime
        # uses preheld_lock; ordinary API calls always acquire their own lock.
        lock = nullcontext() if preheld_lock else self.store.run_lock(run_id) if hasattr(self.store, "run_lock") else file_lock(Path(self.store.root) / "highspeed_runner.lock")
        with ExitStack() as locks:
            locks.enter_context(lock)
            if origin == "LIVE_CAPTURE" and not preheld_lock and plan.get("shared_access_gate_path"):
                locks.enter_context(file_lock(Path(plan["shared_access_gate_path"]).parent / "highspeed_live.lock"))
            validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
            run = self.store.get_run(run_id) if resume else self.store.begin_run(plan, started_at=entry_time)
            if not run:
                raise ValueError("RUN_NOT_FOUND")
            # Resume must use precisely the frozen plan, never larger limits.
            if run.get("plan") and run["plan"] != plan:
                raise ValueError("IMMUTABLE_RUN_PLAN_MISMATCH")
            self.store.update_run(run_id, origin=origin, status="RUNNING")
            started = run.get("started_at") or run.get("created_at")
            if not started:
                started = utc_text(self.clock())
                self.store.update_run(run_id, started_at=started)
            bundle = self.store.load_run(run_id)
            committed = {p["page_key"] for p in bundle.get("pages", []) if p.get("status", p.get("completeness")) != "PARTIAL"}
            cached = {}
            for receipt in bundle.get("responses", []):
                if receipt.get("access_state") == "NORMAL" and receipt.get("kind", receipt.get("request_kind", "PAGE")) == "PAGE":
                    cached[receipt.get("request_url", receipt.get("url"))] = receipt
            last_request = max((aware_time(r["observed_at"]) for r in bundle.get("requests", []) if r.get("observed_at")), default=None)
            if hasattr(self.store, "latest_request_time"):
                global_last = self.store.latest_request_time()
                if global_last:
                    last_request = max(filter(None, (last_request, aware_time(global_last))))
            reason = None
            rolling = RollingProgress(plan["target_unique_links"])
            for event in bundle.get("events", []):
                if event.get("kind") == "PROGRESS":
                    rolling.record(event["elapsed_seconds"], event["unique_links"])
            for source in plan["sources"]:
                for page_number, url in enumerate(source.get("page_urls", []), 1):
                    page_key = hashlib.sha256((source["source_id"] + "\0" + url).encode()).hexdigest()
                    if page_key in committed:
                        continue
                    if self.store.candidate_count(run_id) >= plan["target_unique_links"]:
                        reason = "TARGET_UNIQUE_LINKS_REACHED"
                        break
                    try:
                        if url in cached:
                            receipt = cached[url]
                            response = {**receipt, "text": Path(receipt["html_path"]).read_text(encoding="utf-8")}
                        else:
                            response, receipt, last_request = self._request(plan, authorization, transport,
                                source, url, "PAGE", started, last_request, page_key=page_key)
                        html = response.get("text", "")
                        acp = [{**item, "body": Path(item["html_path"]).read_text(encoding="utf-8"), "evidence_path": item["html_path"]}
                            for item in bundle.get("responses", []) if item.get("kind") == "ACP" and item.get("page_key") == page_key and item.get("access_state") == "NORMAL"]
                        parsed = self._parse(plan, source, url, page_number, receipt, html, acp, origin)
                        attempted = set()
                        pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                        needs_context = getattr(transport, "requires_ajax_page_context", None)
                        if pending and origin == "LIVE_CAPTURE" and url in cached and callable(needs_context) and needs_context(url):
                            response, receipt, last_request = self._request(plan, authorization, transport,
                                source, url, "PAGE", started, last_request, page_key=page_key)
                            html = response.get("text", "")
                            parsed = self._parse(plan, source, url, page_number, receipt, html, [], origin)
                            pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                        while pending and origin == "LIVE_CAPTURE":
                            descriptor = pending[0] if isinstance(pending, list) else pending
                            if not authorization["sources"][source["source_id"]].get("allow_acp"):
                                break
                            prepare = getattr(transport, "prepare_ajax_descriptor", None)
                            if callable(prepare):
                                validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
                                prepare_start = time.perf_counter()
                                descriptor = prepare(descriptor, referer=url)
                                self.store.append_event(run_id, {"kind": "ACP_PREPARE", "stage": "acp_prepare",
                                    "duration_seconds": time.perf_counter()-prepare_start,
                                    "source_type": source["source_type"], "research_category": source.get("research_category")})
                            key = json.dumps(descriptor, sort_keys=True, default=str)
                            if key in attempted:
                                parsed["page"].setdefault("flags", []).append("ACP_NO_PROGRESS")
                                break
                            attempted.add(key)
                            try:
                                item, saved, last_request = self._request(plan, authorization, transport,
                                    source, descriptor["url"], "ACP", started, last_request,
                                    descriptor=descriptor, page_key=page_key)
                            except (ValueError, OSError) as exc:
                                # Preserve all successfully observed cards even when a
                                # continuation fails; never call this a complete page.
                                parsed["page"]["completeness"] = "PARTIAL"
                                parsed["page"]["status"] = "PARTIAL"
                                parsed["page"]["page_complete"] = False
                                parsed["page"].setdefault("flags", []).append(str(exc))
                                self.store.commit_page(run_id, page_key,
                                    {**parsed["page"], "page_key": page_key, "source": source,
                                     "evidence_path": receipt["html_path"], "origin": origin}, parsed["observations"])
                                raise
                            acp.append({**item, "body": item.get("text", ""), "evidence_path": saved["html_path"]})
                            previous = parsed["page"].get("actual_count", len(parsed["observations"]))
                            parsed = self._parse(plan, source, url, page_number, receipt, html, acp, origin)
                            pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                            if parsed["page"].get("actual_count", len(parsed["observations"])) <= previous and pending:
                                parsed["page"].setdefault("flags", []).append("ACP_NO_PROGRESS")
                                break
                        persist_start = aware_time(self.clock())
                        self.store.commit_page(run_id, page_key,
                            {**parsed["page"], "page_key": page_key, "source": source,
                             "evidence_path": receipt["html_path"], "origin": origin}, parsed["observations"])
                        self.store.append_event(run_id, {"kind": "PERSIST", "stage": "persistence",
                            "duration_seconds": (aware_time(self.clock())-persist_start).total_seconds(),
                            "source_type": source["source_type"], "research_category": source.get("research_category")})
                        if parsed["page"].get("status") in {"FAILED", "BLOCKED"}:
                            if source_navigation_only(source, url, parsed):
                                self.store.append_event(run_id, {"kind": "SOURCE_NAVIGATION_ONLY", "stage": "source_navigation",
                                    "source_id": source["source_id"], "source_type": source["source_type"],
                                    "page_url": url, "evidence_path": receipt["html_path"],
                                    "note": "Known root has no cards; quality remains FAILED. Continue only the frozen approved URL queue."})
                            else:
                                raise ValueError("PAGE_PARSE_OR_ACCESS_FAILED")
                        committed.add(page_key)
                        elapsed = (aware_time(self.clock()) - aware_time(started)).total_seconds()
                        count = self.store.candidate_count(run_id)
                        progress = {"kind": "PROGRESS", **rolling.record(elapsed, count)}
                        self.store.append_event(run_id, progress)
                        if self.progress:
                            self.progress(progress)
                    except (ValueError, OSError) as exc:
                        reason = str(exc)
                        self.store.append_event(run_id, {"kind": "STOP", "reason": reason, "observed_at": utc_text(self.clock())})
                        break
                if reason:
                    break
            qa_start = aware_time(self.clock())
            bundle = self.store.load_run(run_id)
            # Re-audit evidence, observations and link pool; finishing a plan is
            # separate from proving completeness or passing a stage gate.
            if self.qa_auditor is None:
                from .quality import audit_run
                auditor = audit_run
            else:
                auditor = self.qa_auditor
            quality = auditor(self.store, run_id)
            errors = quality.get("errors", [finding for finding in quality.get("findings", []) if finding.get("severity") == "ERROR"])
            elapsed = (aware_time(self.clock()) - aware_time(started)).total_seconds()
            reason = reason or ("TARGET_UNIQUE_LINKS_REACHED" if self.store.candidate_count(run_id) >= plan["target_unique_links"] else "SOURCE_PLAN_EXHAUSTED")
            self.store.append_event(run_id, {"kind": "QA", "stage": "qa", "duration_seconds": (aware_time(self.clock())-qa_start).total_seconds(), "errors": errors})
            self.store.update_run(run_id, status="STOPPED" if errors or reason not in {"TARGET_UNIQUE_LINKS_REACHED", "SOURCE_PLAN_EXHAUSTED"} else "FINISHED",
                stop_reason=reason, elapsed_seconds=elapsed, within_duration_target=elapsed <= plan["max_duration_hours"]*3600,
                completed_at=utc_text(self.clock()), finished_at=utc_text(self.clock()), origin=origin,
                qa=quality, quality_gate_passed=quality.get("gate_passed") is True)
            return self.store.load_run(run_id)

    def _parse(self, plan, source, url, number, receipt, html, acp, origin):
        if self.parser is None:
            from .adapters import parse_page
            parser = parse_page
        else:
            parser = self.parser
        before = aware_time(self.clock())
        parsed = parser(html, source=source, run_id=plan["run_id"], snapshot_id=plan["run_id"],
            page_url=url, page_number=number, observed_at=receipt.get("observed_at") or utc_text(before),
            evidence_path=receipt["html_path"], http_status=receipt.get("status_code"), origin=origin, acp_responses=acp)
        for observation in parsed["observations"]:
            if observation.get("origin", origin) != origin:
                raise ValueError("OBSERVATION_ORIGIN_MISMATCH")
            observation.setdefault("origin", origin)
        self.store.append_event(plan["run_id"], {"kind": "PARSE", "stage": "parse", "duration_seconds": (aware_time(self.clock())-before).total_seconds(),
            "raw_card_count": len(parsed["observations"]), "page_url": url,
            "source_type": source["source_type"], "research_category": source.get("research_category")})
        return parsed

    def _request(self, plan, authorization, transport, source, url, kind, started, last_request, *, descriptor=None, page_key=None):
        live = plan["stage"] != "T0"
        if live:
            validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
            authorize_request(url, source, authorization, kind=kind, descriptor=descriptor)
            if last_request is not None:
                delay = plan["request_pacing_seconds"] - (aware_time(self.clock())-last_request).total_seconds()
                if delay > 0:
                    self.sleep(delay)
            # Approval, gate and elapsed budget may change while pacing.
            validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
        counters = self.store.summary(plan["run_id"])
        reason = budget_reason(plan, counters, now=self.clock(), started_at=started, kind=kind)
        if reason:
            raise ValueError(reason)
        observed = aware_time(self.clock())
        origin = plan.get("origin", "LIVE_CAPTURE" if live else "OFFLINE_FIXTURE")
        request = {"kind": kind, "url": url, "source_id": source["source_id"], "source_type": source["source_type"],
            "observed_at": utc_text(observed), "limits": plan, "page_key": page_key,
            "origin": origin}
        reservation = self.store.reserve_request(plan["run_id"], request)
        before = aware_time(self.clock())
        try:
            if not live:
                entries = source.get("offline_pages", {})
                path = entries.get(url)
                if not path:
                    raise ValueError("OFFLINE_SOURCE_HTML_REQUIRED")
                body = Path(path).read_bytes()
                if origin == "HISTORICAL_REPLAY":
                    metadata = source.get("offline_metadata", {}).get(url, {})
                    if metadata.get("page_url") != url or metadata.get("html_sha256") != hashlib.sha256(body).hexdigest():
                        raise ValueError("HISTORICAL_CAPTURE_METADATA_REQUIRED")
                    observed = aware_time(metadata["observed_at"])
                response = {"status_code": 200, "url": url, "text": body.decode("utf-8"), "access_state": "NORMAL"}
            else:
                obj = transport.fetch_page(url) if kind == "PAGE" else transport.fetch_ajax(url, method=descriptor["method"],
                    referer=descriptor.get("referer"), payload=descriptor.get("payload"), headers=descriptor.get("headers"))
                response = dict(obj) if isinstance(obj, dict) else dict(vars(obj))
            state = detect_access_status(response.get("status_code"), response.get("text", "")).value
        except Exception as exc:
            response = {"status_code": None, "url": None, "text": "", "error": type(exc).__name__ + ": " + str(exc)}
            state = "NETWORK_ERROR"
        response.update(access_state=state, observed_at=utc_text(observed), request_url=url,
            duration_seconds=(aware_time(self.clock())-before).total_seconds(), origin=request["origin"],
            kind=kind, page_key=page_key, source_id=source["source_id"], source_type=source["source_type"],
            research_category=source.get("research_category"), final_url=response.get("url"),
            request_method="GET" if kind == "PAGE" else descriptor["method"],
            request_payload=None if descriptor is None else descriptor.get("payload"))
        sensitive = {"authorization", "cookie", "proxy-authorization", "set-cookie"}
        for field in ("headers", "request_headers"):
            response[field] = {str(k): str(v) for k,v in (response.get(field) or {}).items() if str(k).casefold() not in sensitive}
        save_start = aware_time(self.clock())
        receipt = self.store.save_response(plan["run_id"], reservation, response)
        self.store.append_event(plan["run_id"], {"kind": "HTMLSAVE", "stage": "html_save",
            "duration_seconds": (aware_time(self.clock())-save_start).total_seconds(),
            "source_type": source["source_type"], "research_category": source.get("research_category")})
        self.store.append_event(plan["run_id"], {"kind": "REQUEST", "stage": "navigation" if kind == "PAGE" else "acp", "request_kind": kind,
            "duration_seconds": response["duration_seconds"], "access_state": state, "url": url,
            "source_type": source["source_type"], "research_category": source.get("research_category")})
        if state != "NORMAL":
            if hasattr(self.store, "set_gate"):
                self.store.set_gate(state, evidence_path=receipt["evidence_path"], run_id=plan["run_id"])
            else:
                self.store.update_run(plan["run_id"], access_state=state)
            if live:
                self._persist_shared_stop(plan, state, receipt)
            raise ValueError(state)
        if live:
            try:
                authorize_request(response.get("url", url), source, authorization, kind=kind, descriptor=descriptor)
            except ValueError:
                if hasattr(self.store, "set_gate"):
                    self.store.set_gate("UNKNOWN", evidence_path=receipt["evidence_path"], run_id=plan["run_id"], reason="UNREVIEWED_REDIRECT")
                self._persist_shared_stop(plan, "UNKNOWN", receipt)
                raise
        return response, receipt, aware_time(self.clock())

    def _persist_shared_stop(self, plan, state, receipt):
        path = plan.get("shared_access_gate_path")
        if not path:
            return  # Injected fake gates in offline tests have no filesystem.
        path = Path(path)
        with file_lock(path.with_suffix(path.suffix + ".v2.lock")):
            previous = read_sealed(path)
            immutable_json(path.parent / "highspeed_gate_history" / (uuid.uuid4().hex + ".json"), sealed(previous))
            atomic_json(path, sealed({**previous, "state": state, "run_id": plan["run_id"],
                "evidence_path": receipt["evidence_path"], "stopped_at": utc_text(self.clock()),
                "reason": "HIGH_SPEED_V2_ACCESS_STOP"}), backup=True)

