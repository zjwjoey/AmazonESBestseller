"""Offline ledgers bound to source, run manifests and immutable observations."""
from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..cache import TranslationCache
from ..production_contract import canonical_source_field, target_field_for
from ..schemas import TRANSLATION_SCHEMA_VERSION
from ..service import TranslationService, source_hash
from .accounting import origin, requests, semantic, state, text_facts
from .evidence import EvidenceReader, digest, scrub, utc


class ReadOnlyKeys:
    """Static cache contract only; construction never opens a production cache."""
    memory_key = staticmethod(TranslationCache.memory_key)
    key = staticmethod(TranslationCache.key)


class OfflineIdentity:
    def __init__(self, identity: dict):
        self.name = identity.get("name", "UNKNOWN")
        self.model = identity.get("model", "UNKNOWN")

    def translate(self, *_args, **_kwargs):
        raise RuntimeError("RECONCILIATION_PROVIDER_CALL_FORBIDDEN")


def _counts(rows: list[dict], field: str) -> dict:
    return dict(Counter(str(row.get(field, "UNKNOWN")) for row in rows))


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(scrub(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _csv(path: Path, rows: list[dict], fallback: list[str]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) or fallback
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(scrub(val), ensure_ascii=False) if isinstance(val, (dict, list))
                             else scrub(val) for key, val in row.items()})


def _version() -> dict:
    root = Path(__file__).resolve().parents[4]
    def git(*args):
        process = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
        return process.stdout.strip() if process.returncode == 0 else "UNKNOWN"
    return {"head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
            "tracked_dirty": bool(git("status", "--porcelain", "-uno")),
            "translation_schema": TRANSLATION_SCHEMA_VERSION,
            "reconciliation_code_hashes": {path.name: digest(path.read_bytes())
                for path in Path(__file__).parent.glob("*.py")},
            "qa_rules_hash": current_qa_rules_hash()}


def current_qa_rules_hash() -> str:
    directory = Path(__file__).resolve().parents[1]
    hashes = {name: digest((directory / name).read_bytes()) for name in
              ("qa.py", "validators.py", "protection.py", "terminology.py", "schemas.py")}
    return digest(json.dumps(hashes, sort_keys=True).encode("utf-8"))


def _envelope_conflict(left: dict, right: dict) -> bool:
    return (text_facts(left)["text_hash"], left.get("qa_status"), left.get("translation_status")) != (
        text_facts(right)["text_hash"], right.get("qa_status"), right.get("translation_status"))


class Reconciler:
    def __init__(self, config: dict, reader: EvidenceReader):
        self.config, self.reader = config, reader
        self.selected: set[str] = set()
        self.products: dict[str, tuple[dict, dict, str]] = {}
        self.saved: dict[tuple[str, str], tuple[dict, str, str]] = {}
        self.saved_history: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.http: list[dict] = []
        self.events: dict[str, list[dict]] = defaultdict(list)
        self.batches: list[dict] = []
        self.cache: dict = {}
        self.cache_path = ""
        self.references: dict[tuple[str, str], dict] = {}
        self.source_records: dict[str, dict] = {}
        self.derivatives: dict[str, list[dict]] = defaultdict(list)
        self.fields: list[dict] = []
        self.items: list[dict] = []
        self.units: dict[str, list[dict]] = defaultdict(list)
        self.sku_rows: list[dict] = []
        self.source_gate = []
        self.qa_rules_hash = current_qa_rules_hash()
        self.historical_checkpoints = []

    def load(self) -> None:
        reader = self.reader
        self.cache_path = self.config.get("cache", "")
        if self.cache_path:
            self.cache = reader.read_object(self.cache_path)
            for namespace in ("entries", "memory", "results", "memory_results"):
                if not isinstance(self.cache.get(namespace, {}), dict):
                    reader.conflict("CACHE_NAMESPACE_INVALID", self.cache_path, namespace=namespace)
                    self.cache[namespace] = {}
        mutable = {str(reader.resolve(self.cache_path))} if self.cache_path else set()
        for spec in self.config["batches"]:
            batch_id = spec["id"]
            directory = reader.resolve(spec["directory"])
            cfg = reader.read_object(directory / spec.get("config_file", "run_config.json"), optional=True)
            if cfg.get("resume_admission"):
                admission = reader.bound(cfg["resume_admission"])
                reader.verify_bindings(admission, skip_paths=mutable)
            binding = spec.get("manifest") or cfg.get("run_context")
            if binding:
                manifest = reader.bound_object(binding)
            else:
                manifest = reader.read_object(directory / "run_manifest.json")
            reader.verify_bindings(manifest, skip_paths=mutable)
            authority = reader.bound_object(manifest["extension_authorization"]) if manifest.get("extension_authorization") else {}
            reader.verify_bindings(authority or {}, skip_paths=mutable)
            scope = set(manifest.get("selected_asins") or [])
            if len(scope) != len(manifest.get("selected_asins") or []):
                reader.conflict("RUN_SCOPE_DUPLICATE_ASIN", directory)
            events_path = str(directory / "runtime_events.jsonl")
            events = reader.read(events_path, optional=spec.get("dry_run", False), jsonl=True) or []
            starts = [event for event in events if event.get("event") == "AUTHORIZED_RUN_START"]
            eligible_live = bool(starts) and not spec.get("dry_run", False)
            if any(event.get("dry_run") is True for event in starts):
                eligible_live = False
            if not starts and not spec.get("dry_run", False):
                reader.conflict("LIVE_RUN_START_EVIDENCE_MISSING", events_path, batch_id=batch_id)
            final = reader.read_object(directory / "final_manifest.json", optional=True)
            result_path = str(directory / "result.json")
            result = reader.read(result_path, optional=True)
            if result is not None:
                result = reader.object(result, result_path)
            finish = [event for event in events if event.get("event") == "CANARY_PROCESS_FINISHED"]
            closed = bool(eligible_live and finish and finish[-1].get("exit_code") == 0
                          and final.get("exit_code") == 0 and isinstance(result, dict))
            if any(row.get("path") == str(reader.resolve(events_path)) and row["code"].startswith("JSONL_")
                   for row in reader.conflicts):
                closed = False
            plan = reader.read_object(directory / "dryrun_plan.json", optional=True)
            product_data = reader.bound(manifest["products"]) if manifest.get("products") else None
            records = product_data.get("records", []) if isinstance(product_data, dict) else product_data or []
            if {row.get("asin") for row in records} != scope:
                reader.conflict("RUN_PRODUCTS_SCOPE_MISMATCH", directory)
            if eligible_live:
                self.selected.update(scope)
                for record in records:
                    asin = record.get("asin")
                    previous = self.products.get(asin)
                    if previous and previous[0] != record:
                        reader.conflict("SOURCE_PREPARATION_CONFLICT", manifest.get("products", {}).get("path", ""), asin=asin)
                    self.products[asin] = (record, manifest, batch_id)
            rows, groups = requests(batch_id, events, events_path, reader, eligible_live=eligible_live)
            for row in rows:
                path = row["response_path"]
                if path != "UNKNOWN":
                    response = reader.read_object(path)
                    receipt = reader.receipts[str(reader.resolve(path))]
                    row["response_sha256"] = receipt.get("sha256", "UNKNOWN")
                    if (response.get("canonical_dispatch_key") != row["canonical_key"]
                            or response.get("alias") != row["provider_alias"]
                            or response.get("response", {}).get("status_code") != row["http_status"]):
                        reader.conflict("HTTP_RESPONSE_FILE_CONFLICT", path, batch_id=batch_id)
                else:
                    row["response_sha256"] = "UNKNOWN"
            self.http.extend(rows)
            for key, group in groups.items():
                for kind, values in group.items():
                    self.events[key + ":" + kind].extend(values)
            if closed:
                for asin, record in result.items():
                    # Increments can contain the hash-bound parent's saved ASINs.
                    if asin not in self.selected:
                        reader.conflict("RESULT_ASIN_OUTSIDE_SELECTED_SCOPE", result_path, asin=asin)
                        continue
                    for target, envelope in record.get("fields", {}).items():
                        field = canonical_source_field(envelope.get("field") or target)
                        identity = (asin, field)
                        previous = self.saved.get(identity)
                        self.saved_history[identity].append({"batch_id": batch_id, "result_path": result_path,
                            "translation_status": envelope.get("translation_status"), "qa_status": envelope.get("qa_status"),
                            "text_hash": text_facts(envelope)["text_hash"]})
                        if previous and _envelope_conflict(previous[0], envelope):
                            # Resume authorization is explicit; order is supplied by
                            # the reviewed config, never chosen by file mtime.
                            if not cfg.get("resume_admission") and not authority:
                                reader.conflict("UNAUTHORIZED_RESULT_REPLACEMENT", result_path, asin=asin, field=field)
                        self.saved[identity] = (envelope, batch_id, result_path)
            self.batches.append({"batch_id": batch_id, "directory": str(directory), "selected_asins": sorted(scope),
                "selected_skus": len(scope), "live_start_observed": eligible_live, "saved_result_closed": closed,
                "result_path": result_path if result is not None else None,
                "http_attempts": len(rows), "http_status_counts": _counts(rows, "http_status"),
                "planned_dispatches": len(set(plan.get("dispatch_keys") or [])),
                "unique_provider_entry_keys": sum(bool(group["entries"]) for group in groups.values()),
                "stop_marker": reader.read(directory / "HTTP429_HALT.json", optional=True),
                "last_event_at": events[-1].get("time") if events else None,
                "last_response_at": next((e.get("time") for e in reversed(events) if e.get("event") == "HTTP_RESULT"), None),
                "last_persisted_at": next((e.get("time") for e in reversed(events) if e.get("event") == "QA_RESULT_SAVED"), None)})
            if manifest.get("source_manifest"):
                source_manifest = reader.bound_object(manifest["source_manifest"])
                parent_dir = reader.resolve(manifest["source_manifest"]["path"]).parent
                name = "spanish_master_5480.json"
                expected = source_manifest.get("artifacts", {}).get(name)
                source = reader.read_object(parent_dir / name, expected_hash=expected)
                self.source_records.update({row["asin"]: row for row in source.get("records", [])})
                gate = source_manifest.get("source_gate", {})
                if gate and gate not in self.source_gate:
                    self.source_gate.append({k: gate.get(k) for k in ("status", "ready", "audit_hash")})
            if manifest.get("reference_overlay"):
                overlay = reader.bound_object(manifest["reference_overlay"])
                for envelope in overlay.get("reference_envelopes", []):
                    identity = (envelope["asin"], canonical_source_field(envelope["field"]))
                    previous = self.references.get(identity)
                    if previous and _envelope_conflict(previous, envelope):
                        reader.conflict("REFERENCE_CONFLICT", manifest["reference_overlay"]["path"],
                                        asin=identity[0], field=identity[1])
                    self.references[identity] = envelope
        if self.config.get("selection"):
            selection = reader.bound_object(self.config["selection"])
            selected = selection.get("cumulative1000", selection.get("selected_asins", []))
            if len(selected) != len(set(selected)) or set(selected) != self.selected:
                reader.conflict("MERGED_SELECTION_SCOPE_MISMATCH", self.config["selection"]["path"],
                                selected_count=len(selected), observed_count=len(self.selected))
            self.selected.update(selected)
        for binding in self.config.get("derived_qa", []):
            artifact = reader.bound_object(binding)
            version = artifact.get("summary", {}).get("rule_version", "UNKNOWN")
            for row in artifact.get("units", []):
                key = semantic(row.get("canonical_key", ""))
                if not key:
                    reader.conflict("DERIVED_QA_KEY_UNKNOWN", binding["path"])
                    continue
                after = row.get("after_v2", row.get("after", {}))
                self.derivatives[key].append({"path": binding["path"], "sha256": binding["sha256"],
                    "rule_version": version, "qa_status": row.get("effective_status_v2", after.get("qa_status", "UNKNOWN")),
                    "qa_rules_hash": artifact.get("summary", {}).get("qa_rules_hash", "UNKNOWN"),
                    "reviewed_asins": sorted({item.get("asin") for item in
                        [row.get("owner", {}), *row.get("occurrences", [])] if item.get("asin")}),
                    "text_hash": source_hash(row.get("saved_render", "")), "classification": row.get("classification", "UNCLASSIFIED")})
        for binding in self.config.get("supporting_evidence", []):
            artifact = reader.bound(binding)
            reader.verify_bindings(artifact, skip_paths=mutable)
            if isinstance(artifact, dict):
                rows = artifact.get("rows", [])
                pairs = {(row.get("asin"), row.get("source_field", row.get("field"))) for row in rows if isinstance(row, dict)}
                self.historical_checkpoints.append({"path": binding["path"], "sha256": binding["sha256"],
                    "recomputed_rows": len(rows), "recomputed_unique_parent_fields": len(pairs),
                    "recomputed_unique_asins": len({asin for asin, _ in pairs}),
                    "original_declared_summary_preserved": artifact.get("summary", {}),
                    "layer": "INDEPENDENT_HISTORICAL_CHECKPOINT_NOT_CURRENT_QA"})

    def _cache_field(self, asin: str, field: str, text: str, service: TranslationService) -> tuple[dict, str, bool]:
        key = service._field_cache_key(asin, field, source_hash(text))
        value = self.cache.get("entries", {}).get(key)
        if isinstance(value, dict):
            return value, key, False
        identity = TranslationCache.result_key(asin, field, source_hash(text))
        value = self.cache.get("results", {}).get(identity)
        return (value if isinstance(value, dict) else {}), identity, True

    def _unit(self, *, asin: str, field: str, label: str | None, value: str, index: int | None,
              envelope: dict, service: TranslationService, batch_id: str, result_path: str,
              closed: bool, preclean_blocked: bool, source_missing: bool, source_blocked: bool,
              inherited: bool = False, field_cache_binding: str = "") -> dict:
        key = service._memory_key(value, field, label=label)
        stable = semantic(key)
        ev = {kind: self.events.get(key + ":" + kind, []) for kind in ("claims", "entries", "sends", "responses")}
        selected_envelope = envelope
        cache_binding = field_cache_binding
        namespace_old = False
        if not selected_envelope:
            memory = self.cache.get("memory", {}).get(key)
            if not isinstance(memory, dict):
                memory = self.cache.get("memory_results", {}).get(stable)
                namespace_old = bool(memory)
            if isinstance(memory, dict):
                selected_envelope = memory
                cache_binding = key if not namespace_old else stable
        if selected_envelope.get("attempt_state") == "claimed" or selected_envelope.get("claim_id"):
            ev["claims"] = ev["claims"] or [{"cache_claim": cache_binding or result_path}]
        facts = text_facts(selected_envelope)
        qa = selected_envelope.get("qa_status", "UNKNOWN")
        if namespace_old:
            qa = "UNKNOWN"
        derivatives = self.derivatives.get(stable, [])
        matching = [row for row in derivatives if row["text_hash"] == facts["text_hash"]]
        latest_version = self.config.get("latest_qa_rule_version")
        latest = [row for row in matching if latest_version and row["rule_version"] == latest_version
                  and row["qa_rules_hash"] == self.config.get("current_qa_rules_hash") == self.qa_rules_hash
                  and asin in row["reviewed_asins"]]
        latest_status = latest[-1]["qa_status"] if latest else "UNKNOWN"
        if len({row["qa_status"] for row in latest}) > 1:
            self.reader.conflict("LATEST_QA_CONFLICT", result_path, asin=asin, field=field, item_index=index)
            latest_status = "UNKNOWN"
        entry_owners = {row.get("asin") for row in ev["entries"] if row.get("asin")}
        direct = asin in entry_owners and bool(ev["sends"])
        provenance = origin(selected_envelope, reused=bool(cache_binding), direct_proven=direct)
        if (inherited or field_cache_binding) and provenance not in {"DICTIONARY", "DETERMINISTIC_RULE", "IDENTITY_PRESERVED", "LEGACY_REVIEWED_REFERENCE"}:
            provenance = "CACHE_REUSED"
        complete = bool(closed and facts["has_text"] and qa == "pass" and latest_status == "pass"
                        and not namespace_old and not preclean_blocked and not source_blocked)
        row = {"asin": asin, "source_field": field, "target_field": target_field_for(field),
               "item_id": f"{field}:{index}:{source_hash(value)}" if index is not None else "scalar",
               "item_index": index, "source_hash": source_hash(value), "source_label": label,
               "canonical_key": key, "semantic_key": stable, "batch_id": batch_id,
               "saved_result_path": result_path, "cache_binding": cache_binding,
               "provenance": provenance, "saved_translation_status": selected_envelope.get("translation_status", "UNKNOWN"),
               "saved_qa_status": selected_envelope.get("qa_status", "UNKNOWN"),
               "translation_schema": selected_envelope.get("schema_version", "UNKNOWN"),
               "qa_schema_inferred_from_parent": selected_envelope.get("schema_version") is None,
               "current_code_schema": TRANSLATION_SCHEMA_VERSION,
               "saved_qa_rule_version": selected_envelope.get("qa_rule_version", "UNKNOWN"),
               "derived_reviews": derivatives, "latest_qa_rule_version": latest_version or "UNKNOWN",
               "latest_reqa_performed": bool(latest), "latest_qa_status": latest_status,
               "semantic_classification": matching[-1]["classification"] if matching else "UNCLASSIFIED",
               "qa_issue_codes": [r.get("code", "UNKNOWN") if isinstance(r, dict) else str(r)
                                  for r in selected_envelope.get("qa_issues", [])],
               "claim_observed": bool(ev["claims"]), "provider_entry_observed": bool(ev["entries"]),
               "http_send_observed": bool(ev["sends"]), "http_response_observed": bool(ev["responses"]),
               "cache_reuse_status": "EXACT_NAMESPACE_TEXT_CURRENT_QA_UNKNOWN" if cache_binding and facts["has_text"]
                                      and not namespace_old and qa == "pass" else "REVIEW_REQUIRED" if cache_binding else "NOT_APPLICABLE",
               "cache_text_available": bool(cache_binding and facts["has_text"]),
               "owner_excluded": False, "required": not source_missing,
               **facts}
        row["completion_state"] = state(text=facts["has_text"], qa=qa,
            saved_status=selected_envelope.get("translation_status", ""), evidence=ev,
            source_missing=source_missing, source_blocked=source_blocked,
            preclean_blocked=preclean_blocked, complete=complete)
        self.units[stable].append(row)
        return row

    def build_fields(self) -> None:
        for asin in sorted(self.selected):
            if asin not in self.products:
                self.reader.conflict("ASIN_SOURCE_RECORD_MISSING", asin=asin)
                self.sku_rows.append({"asin": asin, "completion_state": "EVIDENCE_CONFLICT", "missing_reason": "SOURCE_RECORD_MISSING"})
                continue
            record, manifest, batch_id = self.products[asin]
            service = TranslationService(OfflineIdentity(manifest.get("provider_identity", {})), ReadOnlyKeys(),
                **manifest.get("service_contract", {}))
            for raw_field in manifest.get("fields", []):
                field = canonical_source_field(raw_field)
                prepared = record.get("fields", {}).get(field)
                if not isinstance(prepared, dict):
                    self.reader.conflict("EXPECTED_FIELD_PREPARATION_MISSING", asin=asin, field=field)
                    self.fields.append({"asin": asin, "source_field": field, "completion_state": "EVIDENCE_CONFLICT"})
                    continue
                text = str(prepared.get("clean_text") or prepared.get("source_text") or "").strip()
                source_present = record.get("raw_fields", {}).get(field, {}).get("source_present")
                missing = not text and source_present is not True
                blocked = not prepared.get("translate_allowed") and not missing
                codes = {row.get("code") if isinstance(row, dict) else str(row)
                         for row in prepared.get("issues", [])}
                source_blocked = bool(codes & {"SOURCE_REVIEW_REQUIRED", "SOURCE_REVIEW_EVIDENCE_MISSING"})
                saved = self.saved.get((asin, field))
                result_path, envelope, old_namespace, field_cache_binding = "", {}, False, ""
                if saved:
                    envelope, _, result_path = saved
                else:
                    envelope, field_cache_binding, old_namespace = self._cache_field(asin, field, text, service)
                reference = self.references.get((asin, field))
                if not envelope and reference:
                    envelope = reference
                expected_hashes = {source_hash(text), source_hash(str(prepared.get("source_text", "")))}
                source_missing_envelope = bool(missing and envelope.get("translation_status") == "source_missing"
                    and not text_facts(envelope)["has_text"] and envelope.get("source_hash") in {"", None, source_hash("")})
                conflict = bool(envelope and (envelope.get("asin", asin) != asin
                    or canonical_source_field(envelope.get("field", field)) != field
                    or (envelope.get("source_hash") not in expected_hashes and not source_missing_envelope)))
                if conflict:
                    self.reader.conflict("FIELD_SOURCE_BINDING_MISMATCH", result_path, asin=asin, field=field)
                parsed = service._structured_items(field, text)
                # Only the details and bullets contracts create child units.
                if field not in {"product_details", "feature_bullets"}:
                    parsed = None
                children = []
                if parsed is not None:
                    actual = envelope.get("items") or []
                    by_index = {item.get("item_index"): item for item in actual if isinstance(item, dict)}
                    if len(by_index) != len(actual):
                        conflict = True
                        self.reader.conflict("DUPLICATE_SAVED_ITEM_ID", result_path, asin=asin, field=field)
                    for index, (label, value) in enumerate(parsed):
                        item = by_index.get(index, {})
                        if item and (item.get("source_text") != value or item.get("label") != label):
                            conflict = True
                            self.reader.conflict("ITEM_SOURCE_BINDING_MISMATCH", result_path, asin=asin, field=field, item_index=index)
                            item = {}
                        child = self._unit(asin=asin, field=field, label=label, value=value, index=index,
                            envelope=item, service=service, batch_id=batch_id, result_path=result_path,
                            closed=bool(saved and item), preclean_blocked=blocked, source_missing=False,
                            source_blocked=source_blocked, inherited=bool(saved and envelope.get("translation_status") == "cached"))
                        if index not in by_index and saved:
                            child["saved_child_missing"] = True
                        children.append(child)
                        self.items.append(child)
                    extra = set(by_index) - set(range(len(parsed)))
                    if extra:
                        conflict = True
                        self.reader.conflict("UNEXPECTED_SAVED_ITEMS", result_path, asin=asin, field=field,
                                             extra_indices=list(extra))
                    states = [child["completion_state"] for child in children]
                    facts = text_facts(envelope)
                    if all(item == "COMPLETE" for item in states) and saved and envelope.get("qa_status") == "pass":
                        parent_state = "COMPLETE"
                    elif states and all(item in {"QA_PASS", "COMPLETE"} for item in states):
                        parent_state = "QA_PASS" if saved and envelope.get("qa_status") == "pass" else "PARTIAL"
                    elif any(child["has_text"] for child in children) or facts["has_text"]:
                        parent_state = "PARTIAL"
                    else:
                        priority = ["SENT_PENDING_OR_UNKNOWN", "ENTERED_NOT_SENT", "CLAIMED_NOT_ENTERED", "PRECLEAN_BLOCKED", "SOURCE_REVIEW_BLOCKED"]
                        parent_state = next((val for val in priority if val in states), "NOT_STARTED")
                    parent = {"asin": asin, "source_field": field, "target_field": target_field_for(field),
                        "source_hash": source_hash(text), "completion_state": parent_state,
                        "provenance": sorted({child["provenance"] for child in children}),
                        "expected_items": len(parsed), "saved_items": len(actual),
                        "child_state_counts": _counts(children, "completion_state"), **facts}
                else:
                    parent = self._unit(asin=asin, field=field, label=None, value=text, index=None,
                        envelope=envelope, service=service, batch_id=batch_id, result_path=result_path,
                        closed=bool(saved), preclean_blocked=blocked, source_missing=missing,
                        source_blocked=source_blocked, field_cache_binding=field_cache_binding if envelope else "")
                    if old_namespace and parent["completion_state"] in {"COMPLETE", "QA_PASS"}:
                        parent["completion_state"] = "TEXT_AVAILABLE_UNREVIEWED"
                    parent.update(expected_items=0, saved_items=0)
                parent.update(batch_id=batch_id, saved_result_path=result_path,
                    raw_source_hash=record.get("raw_fields", {}).get(field, {}).get("source_hash", "UNKNOWN"),
                    saved_result_observed=bool(saved), saved_translation_status=envelope.get("translation_status", "UNKNOWN"),
                    saved_qa_status=envelope.get("qa_status", "UNKNOWN"),
                    translation_schema=envelope.get("schema_version", "UNKNOWN"),
                    saved_history=self.saved_history.get((asin, field), []),
                    reference_available=bool(reference), preclean_blocked=blocked, source_review_blocked=source_blocked,
                    saved_structurally_complete=bool(saved and envelope.get("qa_status") == "pass"
                        and envelope.get("translation_status") in {"success", "cached"}
                        and (parsed is None or len(envelope.get("items", [])) == len(parsed)
                             and all(child["saved_qa_status"] == "pass" for child in children))))
                if conflict:
                    parent["completion_state"] = "EVIDENCE_CONFLICT"
                elif blocked and not reference and parent["completion_state"] in {"QA_PASS", "COMPLETE"}:
                    parent["completion_state"] = "PRECLEAN_BLOCKED"
                self.fields.append(parent)
            self._owner_exclusions(asin)
        for asin in sorted(self.selected):
            parents = [row for row in self.fields if row["asin"] == asin]
            if not parents:
                continue
            required = [row for row in parents if row["completion_state"] != "SOURCE_MISSING"]
            states = [row["completion_state"] for row in required]
            completed = bool(states and all(val == "COMPLETE" for val in states))
            good_text = any(row.get("has_text") for row in parents)
            sku_state = ("EVIDENCE_CONFLICT" if "EVIDENCE_CONFLICT" in states else "COMPLETE" if completed
                         else "PARTIAL" if good_text else "NOT_STARTED" if all(val == "NOT_STARTED" for val in states)
                         else "BLOCKED_OR_UNKNOWN")
            items = [row for row in self.items if row["asin"] == asin]
            self.sku_rows.append({"asin": asin, "completion_state": sku_state,
                "expected_parent_fields": len(parents), "required_parent_fields": len(required),
                "parent_state_counts": _counts(parents, "completion_state"), "has_text": good_text,
                "has_saved_parent_text": any(row.get("saved_result_observed") and row.get("has_text") for row in parents),
                "has_quality_failure": any(row.get("saved_qa_status") == "qa_failed" for row in parents + items),
                "has_pending_or_unknown": any(row["completion_state"] in {
                    "CLAIMED_NOT_ENTERED", "ENTERED_NOT_SENT", "SENT_PENDING_OR_UNKNOWN", "TEXT_AVAILABLE_UNREVIEWED"}
                    for row in parents + items),
                "saved_all_required_structurally_complete": bool(required and all(row.get("saved_structurally_complete") for row in required)),
                "formal_release_ready": False})

    def _owner_exclusions(self, asin: str) -> None:
        source = self.source_records.get(asin, {})
        exclusion = source.get("owner_attribute_exclusions", {})
        for index, item in enumerate(exclusion.get("excluded_items", [])):
            locator = item.get("locator", {})
            self.items.append({"asin": asin, "source_field": "product_details", "target_field": "product_details_zh",
                "item_id": "owner-excluded:" + str(locator.get("position", index)), "item_index": locator.get("position"),
                "source_hash": source_hash(str(locator.get("value_raw", ""))),
                "completion_state": "SOURCE_REVIEW_BLOCKED", "owner_excluded": True, "required": False,
                "provenance": "UNKNOWN", "has_text": False, "saved_qa_status": "EXCLUDED_BY_OWNER",
                "latest_qa_status": "NOT_APPLICABLE", "exclusion_evidence": item})

    def bind_requests(self) -> list[dict]:
        ledger = []
        by_semantic = defaultdict(list)
        cache_counts = Counter(semantic(key) for key in self.cache.get("memory", {}))
        for row in self.http:
            by_semantic[row["semantic_key"]].append(row)
        for stable in sorted(set(by_semantic) | set(self.units)):
            units = self.units.get(stable, [])
            sent = by_semantic.get(stable, [])
            first = self.cache.get("memory_results", {}).get(stable, {})
            derivatives = self.derivatives.get(stable, [])
            # This count is ORIGINAL first-attempt QA, never a later review.
            original_qa = first.get("qa_status", "UNKNOWN")
            beneficiaries = sorted({row["asin"] for row in units})
            for row in sent:
                row.update(original_qa_status=original_qa,
                    derived_qa_status=derivatives[-1]["qa_status"] if derivatives else "UNKNOWN",
                    beneficiary_asins=beneficiaries,
                    beneficiary_bindings=[{"asin": unit["asin"], "field": unit["source_field"],
                        "item_id": unit["item_id"], "source_hash": unit["source_hash"]} for unit in units])
                entries = self.events.get(row["canonical_key"] + ":entries", [])
                owners = {entry.get("asin") for entry in entries if entry.get("asin")}
                verified_owners = owners & set(beneficiaries)
                row["request_owner"] = next(iter(verified_owners)) if len(verified_owners) == 1 else "UNKNOWN"
                if not units:
                    self.reader.conflict("HTTP_BENEFICIARY_UNLINKED", row["event_path"],
                                         batch_id=row["batch_id"], key=row["canonical_key"])
            ledger.append({"semantic_key": stable, "http_attempts": len(sent),
                "provider_entry_observed": any(self.events.get(unit["canonical_key"] + ":entries") for unit in units),
                "claim_observed": any(self.events.get(unit["canonical_key"] + ":claims") for unit in units),
                "provider_aliases": sorted({row["provider_alias"] for row in sent}),
                "http_status_counts": _counts(sent, "http_status"), "beneficiary_asins": beneficiaries,
                "beneficiary_units": [{"asin": unit["asin"], "field": unit["source_field"], "item_id": unit["item_id"]} for unit in units],
                "original_qa_status": original_qa if sent else "NOT_A_NEW_REQUEST",
                "derived_reviews": derivatives, "latest_qa_status": "UNKNOWN",
                "first_cache_evidence_hash": source_hash(json.dumps(first, ensure_ascii=False, sort_keys=True)) if first else "",
                "cache_namespace_entries_count_not_fields": cache_counts[stable],
                "duplicate_semantic_request": len(sent) > 1,
                "unknown_send_outcomes": sum(row["send_outcome"] != "RESPONSE_OBSERVED" for row in sent)})
        return ledger


def _summary(rec: Reconciler, semantics: list[dict], stability: str) -> dict:
    quality_rows = [row for row in rec.http if row["http_status"] == 200]
    return {"schema_version": "translation-reconciliation-v1", "observation_state": stability,
        "atomic_cross_file_snapshot": False, "formal_release": False, "network_requests_by_tool": 0,
        "selected_unique_skus": len(rec.selected), "saved_result_unique_skus": len({asin for asin, _ in rec.saved}),
        "sku_state_counts": _counts(rec.sku_rows, "completion_state"),
        "skus_with_text": sum(row.get("has_text", False) for row in rec.sku_rows),
        "skus_with_saved_parent_text": sum(row.get("has_saved_parent_text", False) for row in rec.sku_rows),
        "skus_with_quality_failure": sum(row.get("has_quality_failure", False) for row in rec.sku_rows),
        "skus_with_pending_or_unknown": sum(row.get("has_pending_or_unknown", False) for row in rec.sku_rows),
        "saved_all_required_structurally_complete_skus": sum(row.get("saved_all_required_structurally_complete", False) for row in rec.sku_rows),
        "parent_field_state_counts": _counts(rec.fields, "completion_state"),
        "structured_item_state_counts": _counts(rec.items, "completion_state"),
        "unit_provenance_counts": _counts([row for values in rec.units.values() for row in values], "provenance"),
        "text_available_unit_provenance_counts": _counts([row for values in rec.units.values() for row in values
                                                         if row["has_text"]], "provenance"),
        "owner_excluded_items": sum(row.get("owner_excluded", False) for row in rec.items),
        "admitted_reference_parent_fields": len(rec.references),
        "http_attempts": len(rec.http), "unique_sent_semantic_keys": len({row["semantic_key"] for row in rec.http}),
        "http_status_counts": _counts(rec.http, "http_status"),
        "original_http200_qa_counts": _counts(quality_rows, "original_qa_status"),
        "semantic_dispatches_provider_entered": len({key[:-len(":entries")] for key, val in rec.events.items() if key.endswith(":entries") and val}),
        "duplicate_sent_semantic_keys": sum(row["duplicate_semantic_request"] for row in semantics),
        "unlinked_http_attempts": sum(not row["beneficiary_asins"] for row in rec.http),
        "unknown_http_outcomes": sum(row["send_outcome"] != "RESPONSE_OBSERVED" for row in rec.http),
        "source_gates": rec.source_gate, "batches": rec.batches,
        "cache_namespace_counts_DIAGNOSTIC_ONLY": {n: len(rec.cache.get(n, {})) for n in ("entries", "memory", "results", "memory_results")},
        "current_qa_not_automatically_executed": True,
        "raw_saved_state_blocked_parent_fields": sum(value[0].get("translation_status") not in {
            "success", "cached", "source_missing"} for value in rec.saved.values()),
        "historical_checkpoints": rec.historical_checkpoints,
        "reconciliation_status": "COMPLETED_WITH_EVIDENCE_CONFLICTS" if any(row["code"] not in {
            "CHANGED_ACROSS_OBSERVATION", "CHANGED_DURING_READ"} for row in rec.reader.conflicts) else "OBSERVED",
        "conflict_counts": _counts(rec.reader.conflicts, "code"),
        "unverifiable": ["Cross-file atomic snapshot", "Unrecorded HTTP request context or attempts",
                         "Latest-code QA without an explicitly bound review", "OS lock owner from a persistent lock filename",
                         "Remote Qwen billing beyond observed usage", "Historical provider identity from legacy text"]}


def reconcile(config: dict, output: str | Path) -> dict:
    """Append-only report directory; source/cache corruption is reportable, not repaired."""
    reader = EvidenceReader(config["allowed_roots"])
    out = Path(output).resolve()
    protected = [Path(spec["directory"]).resolve() for spec in config["batches"]]
    protected += [Path(p).resolve() for p in config.get("protected_roots", [])]
    if any(out == root or root in out.parents or out in root.parents for root in protected):
        raise ValueError("OUTPUT_OVERLAPS_ORIGINAL_EVIDENCE")
    if out.exists():
        raise FileExistsError("RECONCILIATION_OUTPUT_ALREADY_EXISTS")
    start = utc()
    code = _version()
    rec = Reconciler(config, reader)
    rec.load()
    rec.build_fields()
    semantics = rec.bind_requests()
    stable = reader.finish()
    active = bool(config.get("runtime_observation", {}).get("matching_process_alive"))
    stability = "ACTIVE_PROVISIONAL" if active or not stable else "STABLE_OBSERVED_NONATOMIC"
    summary = _summary(rec, semantics, stability)
    summary.update(observation_started_at=start, observation_finished_at=utc(), code=code,
                   runtime_observation=config.get("runtime_observation", {}))
    out.mkdir(parents=True)
    _json(out / "reconciliation_summary.json", summary)
    for rows in (rec.sku_rows, rec.fields, rec.items, semantics, rec.http):
        for row in rows:
            row.update(report_code_head=code["head"], observation_state=stability,
                       observation_started_at=start, input_evidence_manifest=str(out / "manifest.json"))
    _csv(out / "asin_completion.csv", rec.sku_rows, ["asin", "completion_state"])
    _csv(out / "parent_field_ledger.csv", rec.fields, ["asin", "source_field", "completion_state"])
    _csv(out / "structured_item_ledger.csv", rec.items, ["asin", "source_field", "item_id", "completion_state"])
    _csv(out / "semantic_request_ledger.csv", semantics, ["semantic_key", "http_attempts"])
    _csv(out / "provider_http_ledger.csv", rec.http, ["batch_id", "semantic_key", "http_attempt"])
    with (out / "evidence_conflicts.jsonl").open("w", encoding="utf-8") as handle:
        for row in reader.conflicts:
            handle.write(json.dumps(scrub(row), ensure_ascii=False) + "\n")
    for name, asins in config.get("cohorts", {}).items():
        if Path(name).name != name:
            raise ValueError("COHORT_NAME_INVALID")
        directory = out / name
        directory.mkdir()
        selected = {row["asin"] if isinstance(row, dict) else row for row in asins}
        _csv(directory / "asin_completion.csv", [row for row in rec.sku_rows if row["asin"] in selected], ["asin", "completion_state"])
        _csv(directory / "parent_field_ledger.csv", [row for row in rec.fields if row["asin"] in selected], ["asin", "source_field"])
        _csv(directory / "structured_item_ledger.csv", [row for row in rec.items if row["asin"] in selected], ["asin", "item_id"])
        _json(directory / "summary.json", {"selected_skus": len(selected), "scope_asins": sorted(selected),
            "observation_state": stability if name != "saved_state" else "SAVED_RESULT_VIEW_NOT_LATEST_QA",
            "sku_state_counts": _counts([row for row in rec.sku_rows if row["asin"] in selected], "completion_state"),
            "parent_state_counts": _counts([row for row in rec.fields if row["asin"] in selected], "completion_state"),
            "formal_release": False})
    report = ["# Translation V2 offline reconciliation", "", f"Code HEAD: `{code['head']}`",
        f"Observation: `{stability}`. No atomic cross-file snapshot is claimed.",
        f"Selected ASINs: {len(rec.selected)}; saved result ASINs: {summary['saved_result_unique_skus']}.",
        f"HTTP attempts: {len(rec.http)}; unique sent semantics: {summary['unique_sent_semantic_keys']}.",
        "", "QA_PASS means saved QA only. COMPLETE additionally requires a closed saved result,",
        "full child coverage and explicit current-rule review of the same rendered text.",
        "Owner exclusions are separate blocked raw-trace items, not translation success.",
        "Original saved QA, derivative reviews and current-code QA are independent columns.",
        "The four cache namespace sizes are diagnostics only and must never be added as field counts.",
        "", "## Limits", "", *summary["unverifiable"], "",
        "## Findings", "", json.dumps(summary["conflict_counts"], ensure_ascii=False), "",
        "Full input receipts and hashes: manifest.json. Stable reads do not imply a frozen production run."]
    (out / "reconciliation_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (out / "cache_shutdown_diagnostics.md").write_text(
        "Quantified diagnostics are separate: run python -m amazon_es_bestseller.translation.reconciliation benchmark.\n"
        "This report never modifies a cache or production process.\n", encoding="utf-8")
    _json(out / "manifest.json", {"schema_version": "translation-reconciliation-observation-v1", "code": code,
        "observation_started_at": start, "observation_finished_at": utc(), "observation_state": stability,
        "atomic_cross_file_snapshot": False, "input_files": list(reader.receipts.values()),
        "config_sha256": digest(json.dumps(scrub(config), sort_keys=True).encode("utf-8")),
        "runtime_observation": config.get("runtime_observation", {}),
        "artifacts": {str(path.relative_to(out)): digest(path.read_bytes()) for path in out.rglob("*") if path.is_file()}})
    return summary
