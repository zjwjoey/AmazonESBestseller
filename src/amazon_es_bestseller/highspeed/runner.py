"""Serial link-first runner with immutable receipts before any parsing."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone
from contextlib import nullcontext, ExitStack
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from ..access.detector import detect_access_status
from ..multisource.storage import file_lock, read_sealed, atomic_json, immutable_json, sealed
from .authorization import authorize_request, validate_authorization
from .budgets import aware_time, budget_reason, utc_text, validate_plan
from .performance import RollingProgress
from .links import NORMALIZER_VERSION, normalize_product_url
from ..multisource.contracts import SOURCE_ROOTS, canonical_page_url

def source_navigation_only(source, url, parsed):
    """A verified empty root may expose navigation, never valid product output."""
    page = parsed["page"]
    actual = canonical_page_url(url).rstrip("/")
    root = SOURCE_ROOTS.get(source.get("source_type"), "").rstrip("/")
    eligible = bool(page.get("status") == "FAILED"
        and page.get("access_status") == "NORMAL" and page.get("source_type_verified") is True
        and page.get("source_context_valid") is True and not parsed.get("observations")
        and page.get("raw_card_count", 0) == 0
        and set(page.get("flags", [])) == {"PAGE_CONTENT_MISSING"}
        and page.get("expected_count") is None
        and not (parsed.get("acp_pending") or page.get("acp_pending")))
    if not eligible:
        return False
    if actual == root:
        return True
    # A category landing page needs affirmative, saved navigation evidence.
    # Missing cards alone must never suppress an ordinary leaf parser failure.
    try:
        html = Path(page["evidence_path"]).read_text(encoding="utf-8")
    except (OSError, KeyError, UnicodeError):
        return False
    if hashlib.sha256(html.encode("utf-8")).hexdigest() != page.get("evidence_sha256"):
        return False
    soup = BeautifulSoup(html, "lxml")
    left, right = soup.select_one("#zg-left-col"), soup.select_one("#zg-right-col")
    if (left is None or right is None or right.find(True) is not None
            or right.get_text(strip=True)
            or soup.select_one("[data-expected-count], [data-client-recs-list], [data-acp-path]")):
        return False
    return any(anchor.get_text(strip=True)
        and canonical_page_url(urljoin(url, anchor["href"])).rstrip("/").startswith(actual+"/")
        for anchor in left.select("a[href]"))

def continuation_unavailable(parsed, receipt, reason, gate):
    """A bounded native discovery miss is source incompleteness, not success."""
    page = parsed["page"]
    return bool(reason == "ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED"
        and receipt.get("status_code") == 200 and receipt.get("access_state") == "NORMAL"
        and gate.get("state") == "NORMAL" and page.get("access_status") == "NORMAL"
        and page.get("source_type_verified") is True and page.get("source_context_valid") is True
        and parsed.get("observations") and page.get("actual_count", 0) > 0)

def empty_continuation_unavailable(parsed, receipt, reason, gate):
    """Zero capacity stays FAILED after a proven bounded native discovery miss."""
    page=parsed["page"]
    return bool(reason=="ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED"
        and receipt.get("status_code")==200 and receipt.get("access_state")=="NORMAL"
        and gate.get("state")=="NORMAL" and page.get("access_status")=="NORMAL"
        and page.get("source_type_verified") is True and page.get("source_context_valid") is True
        and not parsed.get("observations") and page.get("actual_count")==0
        and page.get("raw_card_count")==0 and (parsed.get("acp_pending") or page.get("acp_pending")))

def explicit_source_empty(parsed):
    """Accept only the documented official wished-list empty component."""
    page=parsed["page"]
    if (page.get("source_type")!="MOST_WISHED_FOR" or page.get("status")!="FAILED"
            or page.get("access_status")!="NORMAL" or page.get("source_type_verified") is not True
            or page.get("source_context_valid") is not True or parsed.get("observations")
            or page.get("actual_count")!=0 or page.get("raw_card_count")!=0
            or page.get("expected_count") is not None or parsed.get("acp_pending") or page.get("acp_pending")
            or set(page.get("flags",[]))!={"PAGE_CONTENT_MISSING"}):
        return False
    try:
        html=Path(page["evidence_path"]).read_text(encoding="utf-8")
        if hashlib.sha256(html.encode("utf-8")).hexdigest()!=page.get("evidence_sha256"):return False
        soup=BeautifulSoup(html,"lxml")
        grids=soup.select("#zg-right-col .p13n-desktop-grid")
        if len(grids)!=1:return False
        grid=grids[0]
        if json.loads(grid.get("data-client-recs-list","null"))!=[]:return False
    except (OSError,UnicodeError,ValueError,KeyError):
        return False
    message="Lo sentimos, no hay productos de los más deseados disponibles en esta categoría. Vuelve más tarde."
    if (not any(h.get_text(" ",strip=True)==message for h in grid.find_all("h4",recursive=False))
            or grid.select_one("#endOfList") is None
            or grid.select_one("[data-expected-count], [data-acp-path], .zg-bdg-text, [data-ranking-card], .ranking-card, #gridItemRoot")):
        return False
    return not any(normalize_product_url(a["href"],base_url=page["page_url"])["is_valid"] for a in grid.select("a[href]"))

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
            seed_cache = {}
            for page in bundle.get("pages", []):
                if page.get("seed_provenance_path"):
                    from .continuation import validate_seed_provenance
                    seed = validate_seed_provenance(page,plan,store=self.store)
                    seed["imported_page"] = page
                    seed_cache[page["page_url"]] = seed
            committed = {p["page_key"] for p in bundle.get("pages", [])
                if p.get("status") == "COMPLETE"
                or (p.get("status") == "PARTIAL" and p.get("terminal_for_snapshot") is True)
                or (p.get("status") == "FAILED" and p.get("source_navigation_only") is True
                    and source_navigation_only(p.get("source", {}), p.get("page_url", ""), {"page":p}))}
            cached = {}
            for url,seed in seed_cache.items():
                original = seed["latest_page"]
                receipt = next(r for r in seed["receipts"] if r["kind"] == "PAGE"
                    and r["html_path"] == original["evidence_path"] and r["observed_at"] == original["observed_at"])
                cached[url] = receipt
            for receipt in bundle.get("responses", []):
                if receipt.get("access_state") == "NORMAL" and receipt.get("kind", receipt.get("request_kind", "PAGE")) == "PAGE":
                    cached[receipt.get("request_url", receipt.get("url"))] = receipt
            # Re-audit a previously recorded zero-card native miss offline. A
            # cached failure must not repeatedly refresh the same source forever.
            for page in bundle.get("pages", []):
                if (page.get("seed_provenance_path") or page.get("status") not in {"FAILED","PARTIAL"}
                        or not ("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED" in page.get("flags",[])
                            or set(page.get("flags",[]))=={"PAGE_CONTENT_MISSING"})
                        or page.get("actual_count")!=0 or page.get("raw_card_count")!=0
                        or page.get("access_status")!="NORMAL" or page.get("source_type_verified") is not True
                        or page.get("source_context_valid") is not True):
                    continue
                source=next((s for s in plan["sources"] if s["source_id"]==page.get("source_id")),None)
                receipt=cached.get(page.get("page_url"))
                if not source or not receipt or receipt.get("html_path")!=page.get("evidence_path"):
                    continue
                try:
                    from .adapters import parse_page
                    html=Path(receipt["html_path"]).read_text(encoding="utf-8")
                    hashed=hashlib.sha256(html.encode("utf-8")).hexdigest()
                    if hashed!=page.get("evidence_sha256") or (receipt.get("sha256") and hashed!=receipt["sha256"]):
                        continue
                    checked=parse_page(html,source=source,run_id=run_id,snapshot_id=plan.get("snapshot_id",run_id),
                        page_url=page["page_url"],page_number=page.get("page_number"),observed_at=page["observed_at"],
                        evidence_path=page["evidence_path"],http_status=receipt.get("status_code"),origin=page["origin"])
                except (OSError,ValueError,KeyError):
                    continue
                official_empty=(receipt.get("status_code")==200 and receipt.get("access_state")=="NORMAL"
                    and explicit_source_empty(checked))
                native_empty=("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED" in page.get("flags",[])
                    and empty_continuation_unavailable(checked,receipt,"ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED",self._gate(plan)))
                if self._gate(plan).get("state")=="NORMAL" and (official_empty or native_empty):
                    if not (page.get("terminal_for_snapshot") is True and page.get("status")=="FAILED"):
                        page={**page,"status":"FAILED","completeness":"UNKNOWN","page_complete":False,
                            "continuation_unavailable":native_empty,"source_empty":official_empty,"terminal_for_snapshot":True}
                        self.store.commit_page(run_id,page["page_key"],{k:v for k,v in page.items() if k!="journal_path"},[])
                        self.store.append_event(run_id,{"kind":"SOURCE_EMPTY" if official_empty else "SOURCE_CONTINUATION_UNAVAILABLE","stage":"source_incompleteness",
                            "page_url":page["page_url"],"source_id":source["source_id"],"source_type":source["source_type"],
                            "actual_cards":0,"reason":"OFFICIAL_EMPTY_STATE" if official_empty else "ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED","cached_failure_reaudited":True,
                            "note":"Zero capacity retained FAILED; frozen approved queue continues without re-requesting this source."})
                    committed.add(page["page_key"])
            last_request = max((aware_time(r["observed_at"]) for r in bundle.get("requests", []) if r.get("observed_at")), default=None)
            if hasattr(self.store, "latest_request_time"):
                global_last = self.store.latest_request_time()
                if global_last:
                    last_request = max(filter(None, (last_request, aware_time(global_last))))
            reason = None
            rolling = RollingProgress(plan["target_unique_links"])
            progress_history = [e for e in bundle.get("events", []) if e.get("kind") == "PROGRESS"]
            comparable = [e for e in progress_history if e.get("normalizer_version") == NORMALIZER_VERSION]
            current_elapsed = (aware_time(self.clock()) - aware_time(started)).total_seconds()
            current_count = self.store.candidate_count(run_id)
            rebase_reason = "NO_CURRENT_NORMALIZER_PROGRESS" if not comparable else None
            if comparable:
                try:
                    for event in comparable:
                        rolling.record(event["elapsed_seconds"], event["unique_links"])
                    if rolling.samples[-1][0] > current_elapsed or rolling.samples[-1][1] > current_count:
                        rebase_reason = "CURRENT_PROGRESS_NO_LONGER_COMPARABLE"
                except ValueError:
                    rebase_reason = "CURRENT_PROGRESS_NO_LONGER_COMPARABLE"
            if rebase_reason:
                # The run wall clock retains its original start. Only the rate
                # baseline changes; historical counts and events remain intact.
                rolling.samples.clear()
                rolling.samples.append((current_elapsed, current_count))
                if resume or progress_history:
                    self.store.append_event(run_id, {"kind":"NORMALIZATION_PROGRESS_REBASE",
                        "normalizer_version":NORMALIZER_VERSION, "reason":rebase_reason,
                        "elapsed_seconds":current_elapsed, "unique_links":current_count,
                        "preserved_history_count":len(progress_history),
                        "previous_progress":progress_history[-1] if progress_history else None})
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
                        historical = url in seed_cache and receipt.get("run_id") == plan.get("parent_run_id")
                        if historical:
                            acp = [{**r,"body":Path(r["html_path"]).read_text(encoding="utf-8"),"evidence_path":r["html_path"]}
                                for r in seed_cache[url]["receipts"] if r["kind"] == "ACP"]
                        parsed = self._parse(plan, source, url, page_number, receipt, html, acp,
                            "HISTORICAL_REPLAY" if historical else origin)
                        attempted = set()
                        pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                        needs_context = getattr(transport, "requires_ajax_page_context", None)
                        if pending and origin == "LIVE_CAPTURE" and url in cached and (historical or (callable(needs_context) and needs_context(url))):
                            response, receipt, last_request = self._request(plan, authorization, transport,
                                source, url, "PAGE", started, last_request, page_key=page_key)
                            historical = False
                            html = response.get("text", "")
                            parsed = self._parse(plan, source, url, page_number, receipt, html, [], origin)
                            pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                        while pending and origin == "LIVE_CAPTURE":
                            descriptor = pending[0] if isinstance(pending, list) else pending
                            scope = authorization["sources"][source["source_id"]]
                            if not (scope.get("allow_acp") or scope.get("allow_native_acp")):
                                break
                            prepare = getattr(transport, "prepare_ajax_descriptor", None)
                            if callable(prepare):
                                prepare_start = time.perf_counter()
                                prepare_success = False
                                try:
                                    validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
                                    descriptor = prepare(descriptor, referer=url)
                                    if isinstance(descriptor.get("payload"), str):
                                        descriptor["request_payload_sha256"] = hashlib.sha256(descriptor["payload"].encode("utf-8")).hexdigest()
                                    prepare_success = True
                                except Exception as exc:
                                    # The initial normal page is real evidence even
                                    # when its lazy request contract is unavailable.
                                    prepare_error = str(exc) if isinstance(exc, ValueError) else "ACP_PREPARE_FAILED:"+type(exc).__name__
                                    empty_unavailable = empty_continuation_unavailable(parsed,receipt,prepare_error,self._gate(plan))
                                    parsed["page"].update(status="FAILED" if empty_unavailable else "PARTIAL", completeness="PARTIAL", page_complete=False)
                                    parsed["page"].setdefault("flags", []).append(prepare_error)
                                    unavailable = empty_unavailable or continuation_unavailable(parsed, receipt, prepare_error, self._gate(plan))
                                    if unavailable:
                                        parsed["page"].update(completeness="UNKNOWN", continuation_unavailable=True,
                                            terminal_for_snapshot=True)
                                    self.store.commit_page(run_id, page_key,
                                        {**parsed["page"], "page_key": page_key, "source": source,
                                         "evidence_path": receipt["html_path"], "origin": origin}, parsed["observations"])
                                    if unavailable:
                                        self.store.append_event(run_id, {"kind":"SOURCE_CONTINUATION_UNAVAILABLE",
                                            "stage":"source_incompleteness", "source_id":source["source_id"],
                                            "source_type":source["source_type"], "page_url":url,
                                            "actual_cards":parsed["page"]["actual_count"], "reason":prepare_error,
                                            "note":"Source retained FAILED at zero capacity or PARTIAL with saved cards; no endpoint/body invented. Continue the frozen approved queue."})
                                        pending = None
                                        break
                                    raise ValueError(prepare_error) from exc
                                finally:
                                    self.store.append_event(run_id, {"kind": "ACP_PREPARE", "stage": "acp_prepare",
                                        "duration_seconds": time.perf_counter()-prepare_start, "success": prepare_success,
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
                        navigation_only = source_navigation_only(source, url, parsed)
                        official_empty = (receipt.get("status_code")==200 and receipt.get("access_state")=="NORMAL"
                            and self._gate(plan).get("state")=="NORMAL" and explicit_source_empty(parsed))
                        if historical:
                            imported = seed_cache[url]["imported_page"]
                            parsed["page"] = {k:v for k,v in imported.items() if k != "journal_path"}
                            parsed["observations"] = read_sealed(imported["journal_path"])["observations"]
                        if navigation_only:
                            parsed["page"]["source_navigation_only"] = True
                        if official_empty:
                            parsed["page"].update(source_empty=True,terminal_for_snapshot=True,completeness="UNKNOWN",page_complete=False)
                            self.store.append_event(run_id,{"kind":"SOURCE_EMPTY","stage":"source_incompleteness",
                                "source_id":source["source_id"],"source_type":source["source_type"],"page_url":url,
                                "actual_cards":0,"reason":"OFFICIAL_EMPTY_STATE","evidence_path":receipt["html_path"]})
                        self.store.commit_page(run_id, page_key,
                            {**parsed["page"], "page_key": page_key, "source": source,
                             "evidence_path": receipt["html_path"], "origin": "HISTORICAL_REPLAY" if historical else origin}, parsed["observations"])
                        self.store.append_event(run_id, {"kind": "PERSIST", "stage": "persistence",
                            "duration_seconds": (aware_time(self.clock())-persist_start).total_seconds(),
                            "source_type": source["source_type"], "research_category": source.get("research_category")})
                        if parsed["page"].get("status") in {"FAILED", "BLOCKED"}:
                            if parsed["page"].get("status")=="FAILED" and parsed["page"].get("terminal_for_snapshot") is True and (parsed["page"].get("continuation_unavailable") is True or official_empty):
                                pass
                            elif navigation_only:
                                self.store.append_event(run_id, {"kind": "SOURCE_NAVIGATION_ONLY", "stage": "source_navigation",
                                    "source_id": source["source_id"], "source_type": source["source_type"],
                                    "page_url": url, "evidence_path": receipt["html_path"],
                                    "note": "Verified navigation page has no cards; quality remains FAILED. Continue only the frozen approved URL queue."})
                            else:
                                raise ValueError("PAGE_PARSE_OR_ACCESS_FAILED")
                        committed.add(page_key)
                        elapsed = (aware_time(self.clock()) - aware_time(started)).total_seconds()
                        count = self.store.candidate_count(run_id)
                        progress = {"kind": "PROGRESS", "normalizer_version":NORMALIZER_VERSION,
                            **rolling.record(elapsed, count)}
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
        if descriptor is not None:
            response["native_acp_descriptor"] = dict(descriptor)
        sensitive = {"authorization", "cookie", "proxy-authorization", "set-cookie"}
        for field in ("headers", "request_headers"):
            response[field] = {str(k): str(v) for k,v in (response.get(field) or {}).items() if str(k).casefold() not in sensitive}
        if response.get("native_acp_descriptor"):
            response["native_acp_descriptor"]["headers"] = {str(k):str(v) for k,v in (descriptor.get("headers") or {}).items() if str(k).casefold() not in sensitive}
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

