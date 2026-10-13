"""Serial snapshot journal with immutable evidence and recoverable checkpoints."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from ..access.detector import detect_access_status
from ..transport.base import TransportResponse, raw_response_evidence
from . import SCHEMA_VERSION
from .authorization import validate_authorization, authorize_request
from .budgets import BudgetLedger
from .contracts import ORIGINS, PHASE_TARGETS, timestamp, canonical_page_url
from .storage import (atomic_json, canonical_bytes, digest, file_lock,
                      immutable_bytes, immutable_json, read_sealed, sealed)


def safe_id(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(value)) or value in {".", ".."}:
        raise ValueError("INVALID_STORAGE_ID")
    return str(value)


class CheckpointManager:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "checkpoint.json"

    def save(self, checkpoint):
        atomic_json(self.path, sealed(checkpoint), backup=True)

    def reconstruct(self, spec):
        pages, errors = [], []
        for path in sorted((self.directory / "pages").glob("*.json")):
            try:
                page = read_sealed(path)
                for evidence in page.get("evidence_files", []):
                    raw = Path(evidence["path"]).read_bytes()
                    if digest(raw) != evidence["sha256"]:
                        raise ValueError("EVIDENCE_CHECKSUM_MISMATCH")
                pages.append(page)
            except (OSError, ValueError) as exc:
                errors.append({"path": str(path), "reason": str(exc)})
        checkpoint = {"run_id": spec["run_id"], "stage": spec["plan"]["phase"],
            "current_source": None, "current_page": None, "completed_sources": [],
            "completed_pages": [p["page_key"] for p in pages],
            "checkpoint_time": timestamp()["observed_at"], "access_state": "NORMAL",
            "retryable_errors": [], "permanent_errors": errors,
            "evidence_paths": [e["path"] for p in pages for e in p.get("evidence_files", [])],
            "counters": {"page_snapshots": len(pages), "observations": sum(len(p["observations"]) for p in pages)}}
        try:
            stored = read_sealed(self.path)
            checkpoint["access_state"] = stored.get("access_state", "NORMAL")
            checkpoint["retryable_errors"] = stored.get("retryable_errors", [])
            for field in ("last_research_category", "last_completed_at", "next_request_allowed_at"):
                checkpoint[field] = stored.get(field)
        except (OSError, ValueError):
            checkpoint["recovery"] = "RECONSTRUCTED_FROM_VERIFIED_PAGE_JOURNAL"
        if pages:
            latest = max(pages, key=lambda page: page.get("completed_at", page["observed_at"]))
            completed_at = latest.get("completed_at", latest["observed_at"])
            if checkpoint.get("last_completed_at") != completed_at:
                checkpoint["next_request_allowed_at"] = None
            checkpoint["last_research_category"] = latest["source"].get("research_category")
            checkpoint["last_completed_at"] = completed_at
        return checkpoint, pages


class SnapshotManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        marker = self.root / ".multisource_store.json"
        if not marker.exists():
            if self.root.exists() and any(self.root.iterdir()):
                raise ValueError("DEDICATED_EMPTY_MULTISOURCE_DIRECTORY_REQUIRED")
            self.root.mkdir(parents=True, exist_ok=True)
            immutable_json(marker, {"schema_version": SCHEMA_VERSION, "kind": "ISOLATED_MULTISOURCE_STORE"})
        elif json.loads(marker.read_text(encoding="utf-8")) != {"schema_version": SCHEMA_VERSION, "kind": "ISOLATED_MULTISOURCE_STORE"}:
            raise ValueError("INVALID_MULTISOURCE_STORE_MARKER")
        self.budget = BudgetLedger(self.root)

    def _run_dir(self, run_id):
        return self.root / "runs" / safe_id(run_id)

    def _gate(self):
        path = self.root / "access_gate.json"
        gate = read_sealed(path) if path.exists() else {"state": "NORMAL"}
        # A crash after evidence publication but before gate persistence cannot
        # turn a restricted response into permission for another request.
        for path in (self.root / "runs").glob("*/requests/*.json"):
            envelope = read_sealed(path)
            if envelope["origin"] == "LIVE_CAPTURE" and envelope["access_status"] != "NORMAL":
                if envelope.get("approval_id") not in gate.get("cleared_approval_ids", []):
                    gate = {**gate, "state": envelope["access_status"],
                            "approval_id": envelope.get("approval_id"), "evidence_path": str(path)}
        return gate

    def _set_gate(self, state, authorization=None, **values):
        previous = read_sealed(self.root / "access_gate.json") if (self.root / "access_gate.json").exists() else {}
        cleared = previous.get("cleared_approval_ids", [])
        if state == "NORMAL" and authorization and authorization.get("clear_access_gate"):
            prior = self._gate().get("approval_id")
            if prior and prior not in cleared:
                cleared = [*cleared, prior]
        atomic_json(self.root / "access_gate.json", sealed({"state": state,
            "approval_id": (authorization or {}).get("approval_id"),
            "cleared_approval_ids": cleared, **timestamp(), **values}), backup=True)

    def pause(self, run_id):
        directory = self._run_dir(run_id)
        if not (directory / "run_spec.json").exists():
            raise ValueError("UNKNOWN_RUN")
        atomic_json(directory / "control.json", sealed({"paused": True, **timestamp()}))
        return self.status(run_id)

    def status(self, run_id):
        directory = self._run_dir(run_id)
        spec = read_sealed(directory / "run_spec.json")
        checkpoint, pages = CheckpointManager(directory).reconstruct(spec)
        frozen = directory / "snapshots" / "v1" / "manifest.json"
        return {"run_id": run_id, "origin": spec["origin"], "phase": spec["plan"]["phase"],
            "paused": read_sealed(directory / "control.json").get("paused", False) if (directory / "control.json").exists() else False,
            "checkpoint": checkpoint, "access_gate": self._gate(),
            "frozen": frozen.exists(), "budget": self.budget.counters(spec["origin"])}

    def _paused(self, directory):
        return (directory / "control.json").exists() and read_sealed(directory / "control.json").get("paused", False)

    def _wait_category(self, source, plan, directory, checkpoint, checkpoint_manager):
        previous = checkpoint.get("last_research_category")
        current = source.get("research_category")
        if not previous or previous == current or not current:
            return True
        delay = float(plan.get("cooldown_after_category_seconds", 1800))
        deadline = checkpoint.get("next_request_allowed_at")
        if not deadline:
            last = datetime.fromisoformat(checkpoint["last_completed_at"].replace("Z", "+00:00"))
            deadline = timestamp(last + timedelta(seconds=delay))["observed_at"]
            checkpoint["next_request_allowed_at"] = deadline
            checkpoint_manager.save(checkpoint)
        target = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        while True:
            if self._paused(directory):
                return False
            if self._gate()["state"] != "NORMAL":
                raise ValueError("ACCESS_GATE_STOPPED_DURING_CATEGORY_COOLDOWN")
            remaining = (target - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                return True
            print("Category cooldown: %ds remaining; deadline %s" % (remaining, deadline), flush=True)
            time.sleep(min(30, remaining))

    def _request(self, *, transport, source, url, kind, authorization, plan, directory, descriptor=None):
        validate_authorization(plan, authorization, [source], self._gate())
        authorize_request(url, source=source, authorization=authorization, kind=kind)
        if self._gate()["state"] != "NORMAL":
            raise ValueError("ACCESS_GATE_STOPPED")
        if self._paused(directory):
            raise ValueError("RUN_PAUSED")
        request_id = "req_" + uuid.uuid4().hex
        observed_at = timestamp()["observed_at"]
        self.budget.reserve_request(request_id, origin="LIVE_CAPTURE", source_type=source["source_type"],
            url=url, kind=kind, observed_at=observed_at, caps=plan)
        # Reservation precedes network side effects. An interrupted attempt
        # remains charged rather than silently disappearing after restart.
        time.sleep(float(plan.get("request_delay_seconds", 2)))
        validate_authorization(plan, authorization, [source], self._gate())
        if self._paused(directory):
            self.budget.finish_request(request_id, "PAUSED_BEFORE_REQUEST")
            raise ValueError("RUN_PAUSED")
        response = None
        error = None
        try:
            if kind == "PAGE":
                response = transport.fetch_page(url)
            else:
                response = transport.fetch_ajax(url, method=descriptor["method"],
                    referer=descriptor.get("referer"), payload=descriptor.get("payload"), headers=descriptor.get("headers"))
            state = detect_access_status(response.status_code, response.text).value
            raw = raw_response_evidence(response)
        except Exception as exc:
            state = "NETWORK_ERROR"
            error = type(exc).__name__ + ": " + str(exc)
            attempted = descriptor or {}
            raw = raw_response_evidence(TransportResponse(None, "", access_state=state,
                request_method=attempted.get("method", "GET"), request_url=url,
                request_headers=attempted.get("headers", {}), request_payload=attempted.get("payload")))
        envelope = {"request_id": request_id, "origin": "LIVE_CAPTURE", "source_id": source["source_id"],
            "page_url": url, "kind": kind, "observed_at": observed_at,
            "parent_page_url": (descriptor or {}).get("referer"),
            "access_status": state, "approval_id": authorization["approval_id"], "error": error,
            "transport_evidence": raw}
        if kind == "ACP" and descriptor:
            envelope["request_contract_source"] = descriptor.get("request_contract_source")
            envelope["request_contract_parent_page_url"] = descriptor.get("parent_page_url")
            if descriptor.get("native_payload_sha256"):
                envelope["native_payload_sha256"] = descriptor["native_payload_sha256"]
        path = directory / "requests" / (request_id + ".json")
        immutable_json(path, sealed(envelope))
        self.budget.finish_request(request_id, state)
        if state != "NORMAL":
            self._set_gate(state, authorization, evidence_path=str(path))
            raise ValueError("ACCESS_STOP: " + state)
        final_url = response.url
        if kind == "PAGE" and final_url != url:
            try:
                authorize_request(final_url, source=source, authorization=authorization)
            except ValueError:
                self._set_gate("UNREVIEWED_REDIRECT", authorization, evidence_path=str(path))
                raise
        return response, envelope, str(path)

    def run(self, plan, sources, *, run_id, snapshot_id=None, origin="OFFLINE_FIXTURE",
            pages=None, authorization=None, transport=None, resume=False):
        from .adapters import get_adapter
        if origin not in ORIGINS or plan.get("phase") not in PHASE_TARGETS:
            raise ValueError("INVALID_RUN_SCOPE")
        if not sources or len({s["source_id"] for s in sources}) != len(sources):
            raise ValueError("EMPTY_OR_DUPLICATE_SOURCE_PLAN")
        if any(source.get("enabled") is False for source in sources):
            raise ValueError("DISABLED_SOURCE_CANNOT_RUN")
        source_urls = [canonical_page_url(source.get("actual_source_url") or source.get("root_url") or "") for source in sources]
        if len(set(source_urls)) != len(source_urls):
            raise ValueError("DUPLICATE_PHYSICAL_SOURCE_MAPPING")
        directory = self._run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        snapshot_id = safe_id(snapshot_id or run_id)
        page_inputs = pages or [{"source_id": source["source_id"], "page_url": url,
            "page_number": n + 1} for source in sources
            for n, url in enumerate((authorization or {}).get("sources", {}).get(source["source_id"], {}).get("page_urls", []))]
        if not page_inputs:
            raise ValueError("NO_PAGE_INPUTS")
        spec = {"run_id": safe_id(run_id), "snapshot_id": snapshot_id, "origin": origin,
                "plan": plan, "sources": sources, "page_inputs": page_inputs}
        with file_lock(self.root / "scheduler.lock"):
            for other in (self.root / "runs").glob("*/run_spec.json"):
                prior_spec = read_sealed(other)
                if prior_spec["snapshot_id"] == snapshot_id and prior_spec["run_id"] != run_id:
                    raise ValueError("SNAPSHOT_ID_ALREADY_ASSIGNED_TO_RUN")
            if (directory / "run_spec.json").exists():
                existing = read_sealed(directory / "run_spec.json")
                if existing != spec:
                    old_plan, new_plan = dict(existing["plan"]), dict(spec["plan"])
                    old_daily = old_plan.pop("max_requests_per_day", None)
                    new_daily = new_plan.pop("max_requests_per_day", None)
                    unchanged_scope = {k: v for k, v in existing.items() if k != "plan"} == {
                        k: v for k, v in spec.items() if k != "plan"}
                    if not (origin == "LIVE_CAPTURE" and resume and unchanged_scope
                            and old_plan == new_plan and old_daily != new_daily):
                        raise ValueError("IMMUTABLE_RUN_SPEC_CONFLICT")
                    validate_authorization(plan, authorization, sources, self._gate())
                    # Keep the original specification immutable. A newly
                    # approved daily-cap change gets its own immutable audit.
                    amendment = {"kind": "DAILY_REQUEST_CAP_AMENDMENT",
                        "run_id": run_id, "original_spec_sha256": digest((directory / "run_spec.json").read_bytes()),
                        "previous_daily_cap": old_daily, "approved_daily_cap": new_daily,
                        "approval_id": authorization["approval_id"],
                        "approved_by": authorization["approved_by"],
                        "reviewed_at": authorization["reviewed_at"],
                        "cumulative_request_cap": plan["max_requests"]}
                    immutable_json(directory / "plan_amendments" / (digest(amendment) + ".json"), sealed(amendment))
            else:
                immutable_json(directory / "run_spec.json", sealed(spec))
            checkpoint_manager = CheckpointManager(directory)
            checkpoint, committed = checkpoint_manager.reconstruct(spec)
            if checkpoint["permanent_errors"]:
                raise ValueError("CORRUPT_PAGE_JOURNAL_OR_EVIDENCE")
            if self._paused(directory) and not resume:
                return self.status(run_id)
            if origin == "LIVE_CAPTURE":
                validate_authorization(plan, authorization, sources, self._gate())
                self._set_gate("NORMAL", authorization)
                checkpoint["access_state"] = "NORMAL"
                if transport is None:
                    raise ValueError("LIVE_TRANSPORT_REQUIRED_AFTER_AUTHORIZATION")
            if resume:
                atomic_json(directory / "control.json", sealed({"paused": False, **timestamp()}))
            frozen = directory / "snapshots" / "v1" / "manifest.json"
            if frozen.exists():
                self._ensure_phase_gate(frozen.parent)
                return self.load(run_id)
            completed = {digest([p["source"]["source_id"], canonical_page_url(p["page_url"])])[:32] for p in committed}
            source_index = {s["source_id"]: s for s in sources}
            for page_input in page_inputs:
                source = source_index[page_input["source_id"]]
                url = page_input["page_url"]
                page_key = digest([source["source_id"], canonical_page_url(url)])[:32]
                if page_key in completed:
                    continue
                if self._paused(directory):
                    return self.status(run_id)
                checkpoint.update(current_source=source["source_id"], current_page=url)
                checkpoint_manager.save(checkpoint)
                try:
                    if origin == "LIVE_CAPTURE" and not self._wait_category(source, plan, directory, checkpoint, checkpoint_manager):
                        return self.status(run_id)
                    acp_responses = []
                    request_paths = []
                    capture_metadata = None
                    if origin == "LIVE_CAPTURE":
                        # Reuse a response saved before an interrupted page commit.
                        cached = []
                        for path in (directory / "requests").glob("*.json"):
                            value = read_sealed(path)
                            if value["source_id"] == source["source_id"] and value["page_url"] == url and value["kind"] == "PAGE" and value["access_status"] == "NORMAL":
                                cached.append((value, path))
                        if cached:
                            envelope, request_path = sorted(cached, key=lambda p: p[0]["observed_at"])[0]
                            if envelope["access_status"] != "NORMAL":
                                raise ValueError("ACCESS_STOP_FROM_SAVED_RESPONSE")
                            raw = envelope["transport_evidence"]["response"]
                            authorize_request(raw["url"], source=source, authorization=authorization)
                            html = raw["body"]
                            http_status = raw["status_code"]
                            captured = envelope["observed_at"]
                            request_paths.append(str(request_path))
                        else:
                            response, envelope, request_path = self._request(transport=transport, source=source,
                                url=url, kind="PAGE", authorization=authorization, plan=plan, directory=directory)
                            html, http_status, captured = response.text, response.status_code, envelope["observed_at"]
                            request_paths.append(request_path)
                        cached_acp = []
                        for path in (directory / "requests").glob("*.json"):
                            prior = read_sealed(path)
                            if (prior["kind"] == "ACP" and prior["source_id"] == source["source_id"]
                                    and prior.get("parent_page_url") == url and prior["access_status"] == "NORMAL"):
                                cached_acp.append((prior, path))
                        for prior, path in sorted(cached_acp, key=lambda item: (item[0]["observed_at"], item[0]["request_id"])):
                            acp_responses.append(prior["transport_evidence"])
                            request_paths.append(str(path))
                    else:
                        if not page_input.get("observed_at") or not page_input.get("html_path"):
                            raise ValueError("OFFLINE_ORIGINAL_TIMESTAMP_AND_HTML_REQUIRED")
                        captured = timestamp(page_input["observed_at"])["observed_at"]
                        html = Path(page_input["html_path"]).read_bytes().decode("utf-8")
                        if origin == "HISTORICAL_REPLAY":
                            if not page_input.get("capture_metadata_path"):
                                raise ValueError("HISTORICAL_CAPTURE_METADATA_REQUIRED")
                            metadata_bytes = Path(page_input["capture_metadata_path"]).read_bytes()
                            capture_metadata = json.loads(metadata_bytes)
                            if (timestamp(capture_metadata["observed_at"])["observed_at"] != captured
                                    or capture_metadata["page_url"] != url
                                    or capture_metadata["html_sha256"] != digest(html.encode("utf-8"))):
                                raise ValueError("HISTORICAL_CAPTURE_METADATA_MISMATCH")
                            metadata_path = self.root / "capture_metadata" / (digest(metadata_bytes) + ".json")
                            immutable_bytes(metadata_path, metadata_bytes)
                        http_status = page_input.get("http_status", 200)
                        for acp_path in page_input.get("acp_paths", []):
                            acp_responses.append(json.loads(Path(acp_path).read_text(encoding="utf-8")))
                    raw_bytes = html.encode("utf-8")
                    evidence_path = self.root / "html" / (digest(raw_bytes) + ".html")
                    immutable_bytes(evidence_path, raw_bytes)
                    for envelope in acp_responses:
                        self._bind_acp_evidence(envelope)
                    parse_args = dict(source=source, run_id=run_id, snapshot_id=snapshot_id,
                        page_url=url, page_number=page_input.get("page_number"), observed_at=captured,
                        evidence_path=str(evidence_path), http_status=http_status, origin=origin)
                    result = get_adapter(source["source_type"]).parse(html, acp_responses=acp_responses, **parse_args)
                    needs_context = getattr(transport, "requires_ajax_page_context", None)
                    if (origin == "LIVE_CAPTURE" and result["page"].get("acp_pending")
                            and authorization["sources"][source["source_id"]].get("allow_acp")
                            and callable(needs_context) and needs_context(url)):
                        # A crash-resumed browser starts at about:blank. Old
                        # HTML neither establishes its origin nor renews the
                        # page-specific ACP token. Fetch the exact approved
                        # page through the ordinary budget/access gate and
                        # preserve both responses instead of replaying a token.
                        response, envelope, request_path = self._request(
                            transport=transport, source=source, url=url, kind="PAGE",
                            authorization=authorization, plan=plan, directory=directory)
                        html, http_status, captured = response.text, response.status_code, envelope["observed_at"]
                        raw_bytes = html.encode("utf-8")
                        evidence_path = self.root / "html" / (digest(raw_bytes) + ".html")
                        immutable_bytes(evidence_path, raw_bytes)
                        request_paths = [request_path]
                        acp_responses = []
                        capture_metadata = None
                        parse_args.update(observed_at=captured, evidence_path=str(evidence_path), http_status=http_status)
                        result = get_adapter(source["source_type"]).parse(html, acp_responses=[], **parse_args)
                    acp_attempts = set()
                    while origin == "LIVE_CAPTURE" and result["page"].get("acp_pending"):
                        descriptor = result["page"]["acp_pending"]
                        if isinstance(descriptor, list):
                            descriptor = descriptor[0] if descriptor else None
                        if descriptor and authorization["sources"][source["source_id"]].get("allow_acp"):
                            prepare = getattr(transport, "prepare_ajax_descriptor", None)
                            if callable(prepare):
                                validate_authorization(plan, authorization, [source], self._gate())
                                if self._paused(directory):
                                    raise ValueError("RUN_PAUSED")
                                descriptor = prepare(descriptor, referer=url)
                            signature = digest([descriptor["url"], descriptor.get("payload")])
                            if signature in acp_attempts:
                                result["page"].setdefault("flags", []).append("ACP_NO_PROGRESS")
                                break
                            acp_attempts.add(signature)
                            previous_count = len(result["observations"])
                            descriptor = {**descriptor, "referer": url}
                            response, _, request_path = self._request(transport=transport, source=source,
                                url=descriptor["url"], kind="ACP", authorization=authorization, plan=plan,
                                directory=directory, descriptor=descriptor)
                            request_paths.append(request_path)
                            acp_envelope = raw_response_evidence(response)
                            self._bind_acp_evidence(acp_envelope)
                            acp_responses.append(acp_envelope)
                            result = get_adapter(source["source_type"]).parse(html, acp_responses=acp_responses, **parse_args)
                            if len(result["observations"]) <= previous_count and result["page"].get("acp_pending"):
                                result["page"].setdefault("flags", []).append("ACP_NO_PROGRESS")
                                break
                        else:
                            break
                    evidence_files = [{"path": str(evidence_path), "sha256": digest(raw_bytes), "kind": "PAGE_HTML"}]
                    if capture_metadata is not None:
                        evidence_files.append({"path": str(metadata_path), "sha256": digest(metadata_bytes), "kind": "CAPTURE_METADATA"})
                    for index, envelope in enumerate(acp_responses):
                        acp_path = directory / "acp" / (page_key + "_" + str(index) + ".json")
                        immutable_json(acp_path, envelope)
                        evidence_files.append({"path": str(acp_path), "sha256": digest(acp_path.read_bytes()), "kind": "ACP_ENVELOPE"})
                        evidence_files.append({"path": envelope["evidence_path"], "sha256": envelope["body_sha256"], "kind": "ACP_HTML"})
                    batch_id = digest([run_id, page_key])
                    self.budget.accept(batch_id, result["observations"], origin=origin,
                        phase=plan["phase"], max_observations=plan.get("max_observations"))
                    page_record = {"page_key": page_key, "source": source,
                        "observed_at": captured, "completed_at": timestamp()["observed_at"], "origin": origin, "http_status": http_status,
                        "page": {**result["page"], "evidence_files": evidence_files,
                            "request_paths": request_paths,
                            "capture_receipt_path": request_paths[0] if origin == "LIVE_CAPTURE" and request_paths else None},
                        "observations": result["observations"], "pagination": result.get("pagination", []),
                        "discovered_sources": result.get("discovered_sources", []),
                        "evidence_files": evidence_files, "request_paths": request_paths,
                        "page_url": url, "page_number": page_input.get("page_number")}
                    immutable_json(directory / "pages" / (page_key + ".json"), sealed(page_record))
                    committed.append(page_record)
                    completed.add(page_key)
                    checkpoint, _ = checkpoint_manager.reconstruct(spec)
                    checkpoint.update(last_research_category=source.get("research_category"),
                        last_completed_at=page_record["completed_at"], next_request_allowed_at=None)
                    checkpoint_manager.save(checkpoint)
                except (OSError, ValueError) as exc:
                    checkpoint.update(access_state=self._gate().get("state", "NORMAL"),
                        permanent_errors=[*checkpoint.get("permanent_errors", []), {"source": source["source_id"], "page": url, "error": str(exc)}])
                    checkpoint_manager.save(checkpoint)
                    raise
            checkpoint["completed_sources"] = sorted({p["source"]["source_id"] for p in committed})
            checkpoint_manager.save(checkpoint)
            return self._freeze(spec, committed, version="v1")

    def _bind_acp_evidence(self, envelope):
        body = envelope.get("response", {}).get("body", envelope.get("html", ""))
        if not isinstance(body, str) or not body:
            raise ValueError("ACP_BODY_EVIDENCE_REQUIRED")
        data = body.encode("utf-8")
        path = self.root / "html" / (digest(data) + ".acp.html")
        immutable_bytes(path, data)
        envelope.update(evidence_path=str(path), body_sha256=digest(data))

    def _freeze(self, spec, committed, *, version):
        from .quality import audit_snapshot
        from .identity import ProductIdentityRegistry
        directory = self._run_dir(spec["run_id"]) / "snapshots" / safe_id(version)
        observations = [o for p in committed for o in p["observations"]]
        pages = [p["page"] for p in committed]
        registry = ProductIdentityRegistry(self.root / "identity")
        # The raw observations remain untouched; identity mappings are derived.
        if version == "v1":
            registry.ingest(observations)
            enriched = registry.materialize_views(observations, version)
        else:
            enriched = registry.materialize_views(observations, version)
        dates = sorted({timestamp(p["observed_at"])["local_date"] for p in committed})
        requests = [read_sealed(path) for path in (self._run_dir(spec["run_id"]) / "requests").glob("*.json")]
        receipts = [{"path": str(path), "sha256": digest(path.read_bytes())} for path in
            (self._run_dir(spec["run_id"]) / "requests").glob("*.json")]
        manifest = {"schema_version": SCHEMA_VERSION, "run_id": spec["run_id"],
            "snapshot_id": spec["snapshot_id"], "origin": spec["origin"], "phase": spec["plan"]["phase"], "stage": spec["plan"]["phase"],
            "version": version, "sources": spec["sources"], "local_dates": dates,
            "local_date": dates[0] if len(dates) == 1 else None, "timezone": "Europe/Madrid",
            "planned_pages": list({(p["source_id"], canonical_page_url(p["page_url"])):
                {"source_id": p["source_id"], "page_url": canonical_page_url(p["page_url"])} for p in spec["page_inputs"]}.values()),
            "planned_page_urls": {s["source_id"]: [p["page_url"] for p in spec["page_inputs"] if p["source_id"] == s["source_id"]] for s in spec["sources"]},
            "source_ids": [s["source_id"] for s in spec["sources"]],
            "observation_count": len(observations), "page_count": len(pages),
            "request_count": len(requests),
            "known_saved_live_request_attempts": len(requests) if spec["origin"] == "LIVE_CAPTURE" else 0,
            "live_request_receipts": receipts if spec["origin"] == "LIVE_CAPTURE" else [],
            "counters": {"request_count": len(requests)},
            "budget": self.budget.counters(spec["origin"]), "created_from": "IMMUTABLE_PAGE_JOURNAL"}
        manifest["execution_plan"] = spec["plan"]
        manifest["plan_amendments"] = [{"path": str(path), "sha256": digest(path.read_bytes())}
            for path in (self._run_dir(spec["run_id"]) / "plan_amendments").glob("*.json")]
        qa = audit_snapshot(manifest, pages, enriched)
        manifest["status"] = qa.get("status", "UNVERIFIED")
        immutable_bytes(directory / "observations.jsonl", b"".join(canonical_bytes(o)+b"\n" for o in observations))
        immutable_json(directory / "pages.json", pages)
        immutable_json(directory / "identity_links.json", enriched)
        immutable_json(directory / "quality_report.json", qa)
        immutable_json(directory / "execution_log.json", {"requests": [{key: request.get(key) for key in
            ("request_id", "page_url", "parent_page_url", "source_id", "kind", "access_status", "observed_at", "error", "approval_id")} for request in requests],
            "mode": spec["origin"], "known_request_attempts": len(requests), "accepted_page_snapshots": len(pages),
            "note": "Budget reservations also retain ambiguous interrupted attempts; they are reported separately from saved transport receipts."})
        manifest["artifacts"] = {name: digest((directory / name).read_bytes()) for name in
            ("observations.jsonl", "pages.json", "identity_links.json", "quality_report.json", "execution_log.json")}
        immutable_json(directory / "manifest.json", sealed(manifest))
        self._ensure_phase_gate(directory)
        return self.load(spec["run_id"], version=version)

    def _ensure_phase_gate(self, directory):
        from .quality import audit_snapshot
        manifest = read_sealed(directory / "manifest.json")
        for name, checksum in manifest["artifacts"].items():
            if Path(name).name != name or digest((directory / name).read_bytes()) != checksum:
                raise ValueError("FROZEN_SNAPSHOT_ARTIFACT_CORRUPTED")
        qa = json.loads((directory / "quality_report.json").read_text(encoding="utf-8"))
        pages = json.loads((directory / "pages.json").read_text(encoding="utf-8"))
        links = json.loads((directory / "identity_links.json").read_text(encoding="utf-8"))
        # Publishing a gate after a crash requires the same evidence to remain valid.
        current = audit_snapshot(manifest, pages, links)
        if current["gate_passed"] != qa["gate_passed"]:
            raise ValueError("FROZEN_GATE_EVIDENCE_CHANGED")
        immutable_json(directory / "phase_gate.json", sealed({"phase": manifest["phase"],
            "origin": manifest["origin"], "passed": qa["gate_passed"],
            "quality_report_sha256": digest((directory / "quality_report.json").read_bytes()),
            "manifest_sha256": digest((directory / "manifest.json").read_bytes())}))

    def load(self, run_id, *, version="v1", refresh_identity=True):
        directory = self._run_dir(run_id) / "snapshots" / safe_id(version)
        if not (directory / "manifest.json").exists():
            if version != "v1":
                raise ValueError("PARSE_VERSION_NOT_FROZEN")
            return self.load_open(run_id)
        manifest = read_sealed(directory / "manifest.json")
        for name, checksum in manifest["artifacts"].items():
            if Path(name).name != name or digest((directory / name).read_bytes()) != checksum:
                raise ValueError("FROZEN_SNAPSHOT_ARTIFACT_CORRUPTED")
        observations = [json.loads(line) for line in (directory / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line]
        pages = json.loads((directory / "pages.json").read_text(encoding="utf-8"))
        if len(observations) != manifest["observation_count"] or len(pages) != manifest["page_count"]:
            raise ValueError("SNAPSHOT_CONTENT_COUNT_MISMATCH")
        if refresh_identity:
            from .identity import ProductIdentityRegistry
            observations = ProductIdentityRegistry(self.root / "identity").materialize_views(observations, version)
        else:
            observations = json.loads((directory / "identity_links.json").read_text(encoding="utf-8"))
        from .quality import audit_snapshot
        return {"path": str(directory), "manifest": manifest, "pages": pages,
                "observations": observations, "quality": audit_snapshot(manifest, pages, observations)}

    def load_open(self, run_id):
        """Diagnostic report for interrupted/blocked work, never a frozen success."""
        from .quality import audit_snapshot
        from .identity import ProductIdentityRegistry
        directory = self._run_dir(run_id)
        spec = read_sealed(directory / "run_spec.json")
        checkpoint, committed = CheckpointManager(directory).reconstruct(spec)
        pages = [record["page"] for record in committed]
        raw = [row for record in committed for row in record["observations"]]
        registry = ProductIdentityRegistry(self.root / "identity")
        registry.ingest(raw)
        observations = registry.materialize_views(raw, "v1")
        source_index = {s["source_id"]: s for s in spec["sources"]}
        requests = [read_sealed(path) for path in (directory / "requests").glob("*.json")]
        receipts = [{"path": str(path), "sha256": digest(path.read_bytes())} for path in (directory / "requests").glob("*.json")]
        for request in requests:
            if request["access_status"] == "NORMAL":
                continue
            body = request["transport_evidence"]["response"]["body"]
            data = body.encode("utf-8")
            evidence = self.root / "html" / (digest(data) + ".failed.html")
            immutable_bytes(evidence, data)
            source = source_index[request["source_id"]]
            url = request.get("parent_page_url") or request["page_url"]
            pages.append({"source_id": source["source_id"], "source_type": source["source_type"],
                "page_url": url, "page_number": None, "research_category": source.get("research_category"),
                "observed_at": request["observed_at"], **timestamp(request["observed_at"]),
                "origin": spec["origin"], "expected_count": None, "actual_count": 0,
                "access_status": request["access_status"], "http_status": request["transport_evidence"]["response"]["status_code"],
                "status": "BLOCKED", "source_type_verified": False,
                "evidence_path": str(evidence), "evidence_sha256": digest(data), "flags": ["INCOMPLETE_STOPPED_PAGE"]})
        dates = sorted({timestamp(p["observed_at"])["local_date"] for p in pages})
        manifest = {"schema_version": 1, "run_id": run_id, "snapshot_id": spec["snapshot_id"],
            "origin": spec["origin"], "phase": spec["plan"]["phase"], "stage": spec["plan"]["phase"],
            "version": "v1", "frozen": False, "in_progress": True, "sources": spec["sources"],
            "planned_pages": spec["page_inputs"], "local_dates": dates,
            "local_date": dates[0] if len(dates)==1 else None,
            "counters": {"request_count": len(requests)}, "access_stop_enforced": self._gate()["state"] != "NORMAL",
            "known_saved_live_request_attempts": len(requests) if spec["origin"] == "LIVE_CAPTURE" else 0,
            "live_request_receipts": receipts if spec["origin"] == "LIVE_CAPTURE" else [],
            "checkpoint": checkpoint, "budget": self.budget.counters(spec["origin"])}
        qa = audit_snapshot(manifest, pages, observations)
        qa["gate_passed"] = False
        qa["gate"] = "FAIL"
        qa["gate_block_reasons"] = sorted(set(qa["gate_block_reasons"] + ["RUN_NOT_FROZEN"]))
        return {"path": str(directory), "manifest": manifest, "pages": pages,
                "observations": observations, "quality": qa}

    def reparse(self, run_id, *, version):
        from .adapters import get_adapter
        if version == "v1":
            raise ValueError("NEW_PARSE_VERSION_REQUIRED")
        directory = self._run_dir(run_id)
        with file_lock(self.root / "scheduler.lock"):
            spec = read_sealed(directory / "run_spec.json")
            checkpoint, committed = CheckpointManager(directory).reconstruct(spec)
            if checkpoint["permanent_errors"]:
                raise ValueError("CORRUPT_EVIDENCE_CANNOT_REPARSE")
            reparsed = []
            for record in committed:
                html = Path(record["evidence_files"][0]["path"]).read_bytes().decode("utf-8")
                acp = [json.loads(Path(e["path"]).read_text(encoding="utf-8")) for e in record["evidence_files"][1:] if e.get("kind") == "ACP_ENVELOPE"]
                parsed = get_adapter(record["source"]["source_type"]).parse(html,
                    source=record["source"], run_id=run_id, snapshot_id=spec["snapshot_id"],
                    page_url=record["page_url"], page_number=record["page_number"],
                    observed_at=record["observed_at"], evidence_path=record["evidence_files"][0]["path"],
                    origin=record["origin"], http_status=record.get("http_status", 200), acp_responses=acp)
                previous_ids = {row["slot_position"]: row["observation_id"] for row in record["observations"]}
                for row in parsed["observations"]:
                    if row["slot_position"] in previous_ids:
                        row["observation_id"] = previous_ids[row["slot_position"]]
                reparsed.append({**record, "page": {**parsed["page"], "evidence_files": record["evidence_files"],
                    "request_paths": record["page"].get("request_paths", []),
                    "capture_receipt_path": record["page"].get("capture_receipt_path")}, "observations": parsed["observations"]})
            return self._freeze(spec, reparsed, version=version)
