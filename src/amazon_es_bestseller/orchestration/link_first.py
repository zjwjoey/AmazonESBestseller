"""Serial, opt-in link-first collection. Legacy ASIN-frozen phases are unchanged.

Ranking segments and page HTML are immutable. Detail identity is resolved only
after navigation. This runner emits candidates, never a formal release.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..access.browser import BrowserSession
from ..access.detector import detect_access_status
from ..access.location import inspect_delivery_location
from ..collection.detail import parse_detail_page
from ..collection.ranking_links import parse_ranking_links, safe_product_link
from ..normalization.category import category_levels
from ..normalization.price import parse_price
from ..normalization.spanish_candidate import spanish_candidate_fields
from ..quality.source_fields import audit_source_fields, source_gate
from ..quality.staged_fields import audit_detail_fields, resolve_selected_identity
from ..runtime_state import VersionedCheckpointStore
from .checkpoint import read_json, write_json_atomic
from .phases import canonical_json_sha256, git_head
from .plan import category_rank_filter, resolve_task_path, validate_task_plan

CONTRACT = "link-first-collection-v1"
RESTRICTED = {"CHALLENGE", "BLOCKED", "RATE_LIMITED"}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_code_hash(root: Path) -> str:
    files = sorted((root / "src" / "amazon_es_bestseller").rglob("*.py"))
    entry = root / "tools" / "link_first_collection.py"
    if entry.exists():
        files.append(entry)
    return canonical_json_sha256({str(p.relative_to(root)): file_hash(p) for p in files})


def immutable_json(path: Path, value: object) -> None:
    if path.exists():
        if canonical_json_sha256(read_json(path, None)) != canonical_json_sha256(value):
            raise ValueError("IMMUTABLE_ARTIFACT_DRIFT: " + str(path))
    else:
        write_json_atomic(path, value)


def page_url(source: str, page: int) -> str:
    parts = urlsplit(source)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "pg"]
    if page > 1:
        query.append(("pg", str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class LinkFirstRunner:
    def __init__(self, plan_path: Path, output: Path, root: Path, *, sample_size: int = 50,
                 continue_after_sample: bool = False, resume: bool = False,
                 migrate_delivery_preflight: bool = False,
                 migrate_delivery_auto: bool = False,
                 migrate_delivery_auto_finish: bool = False,
                 session_factory=BrowserSession, sleep=time.sleep, now=time.time):
        self.plan_path, self.output, self.root = plan_path.resolve(), output.resolve(), root.resolve()
        raw = read_json(self.plan_path, None)
        self.plan = validate_task_plan(raw, plan_path=self.plan_path, project_root=self.root)
        if sample_size < 1 or sample_size > self.plan["target_unique"]:
            raise ValueError("INVALID_SAMPLE_SIZE")
        snapshot = resolve_task_path(raw["source_snapshot"], project_root=self.root)
        self.metadata = {"contract": CONTRACT, "run_id": self.output.name,
                         "reviewed_task_id": raw.get("task_id"), "reviewed_plan": str(self.plan_path),
                         "reviewed_plan_sha256": canonical_json_sha256(raw),
                         "reviewed_plan_file_sha256": file_hash(self.plan_path),
                         "source_snapshot": str(snapshot), "source_snapshot_sha256": file_hash(snapshot),
                         "code_head": git_head(self.root), "code_tree_sha256": runtime_code_hash(self.root),
                         "python_executable": sys.executable, "runner_module_file": str(Path(__file__).resolve()),
                         "runtime_root": str(self.root), "target_unique": self.plan["target_unique"],
                         "identity_policy": "selected-detail-child", "snapshot_asin_extraction": False,
                         "scheduler": "serial", "cooldown_seconds": self.plan["scheduler"]["fallback_cooldown_seconds"],
                         "headful": True, "translation": "disabled", "previous_details_reuse": False,
                         "sample_size": sample_size, "continue_after_sample": continue_after_sample,
                         "formal_release": False}
        self.metadata.update(self.runtime_metadata())
        self.output.mkdir(parents=True, exist_ok=True)
        metadata_path = self.output / "run_metadata.json"
        if metadata_path.exists() and not resume:
            raise ValueError("RUN_EXISTS_USE_EXPLICIT_RESUME")
        if metadata_path.exists() and read_json(metadata_path, None) != self.metadata:
            original = read_json(metadata_path, None)
            migration_path = self.output / "code_migrations" / "delivery_preflight_v1.json"
            comparable = dict(self.metadata)
            comparable["code_tree_sha256"] = original["code_tree_sha256"]
            if comparable != original:
                raise ValueError("RUN_METADATA_DRIFT")
            if migrate_delivery_preflight and not migration_path.exists():
                prior = read_json(self.output / "checkpoints" / "link_first_state.latest.json", {})
                # The store envelope is decoded by its own verified loader.
                store = VersionedCheckpointStore(self.output / "checkpoints", "link_first_state")
                state = store.load_or_migrate(default={})
                if (state.get("status") != "ERROR" or state.get("error") != "SPAIN_DELIVERY_NOT_VERIFIED"
                        or state.get("completed_segments") or state.get("detail_results")
                        or any((self.output / "ranking_snapshots").rglob("*.json"))
                        or any((self.output / "detail_evidence").rglob("*.json"))):
                    raise ValueError("PREFLIGHT_MIGRATION_NOT_ELIGIBLE")
                immutable_json(migration_path, {
                    "reason": "DELIVERY_PREFLIGHT_SINGLE_CHECK_EXIT_FIX", "created_at": utc(),
                    "original_metadata_sha256": file_hash(metadata_path),
                    "old_code_tree_sha256": original["code_tree_sha256"],
                    "new_code_tree_sha256": self.metadata["code_tree_sha256"],
                    "prior_checkpoint": prior, "prior_state": state,
                    "prior_state_sha256": canonical_json_sha256(state),
                    "preflight_evidence": {str(p.relative_to(self.output)): file_hash(p)
                                           for p in sorted((self.output / "preflight").glob("*")) if p.is_file()}})
            migration = read_json(migration_path, {})
            state = migration.get("prior_state", {})
            if (migration.get("reason") != "DELIVERY_PREFLIGHT_SINGLE_CHECK_EXIT_FIX"
                    or migration.get("original_metadata_sha256") != file_hash(metadata_path)
                    or migration.get("old_code_tree_sha256") != original["code_tree_sha256"]
                    or migration.get("prior_state_sha256") != canonical_json_sha256(state)
                    or state.get("error") != "SPAIN_DELIVERY_NOT_VERIFIED"
                    or state.get("status") != "ERROR" or state.get("completed_segments")
                    or state.get("detail_results")):
                raise ValueError("INVALID_PREFLIGHT_CODE_MIGRATION")
            for relative, expected in migration["preflight_evidence"].items():
                if file_hash(self.output / relative) != expected:
                    raise ValueError("MIGRATION_EVIDENCE_DRIFT")
            effective_hash = migration["new_code_tree_sha256"]
            predecessor = migration_path
            for number, authorized, reason in (
                    (1, migrate_delivery_auto, "CONNECT_EXISTING_SPAIN_DELIVERY_UI"),
                    (2, migrate_delivery_auto_finish, "STABLE_DELIVERY_HECHO_SELECTOR_FIX")):
                auto_migration_path = self.output / "code_migrations" / ("delivery_auto_v%d.json" % number)
                if authorized and not auto_migration_path.exists():
                    state = VersionedCheckpointStore(self.output / "checkpoints", "link_first_state").load_or_migrate(default={})
                    if (state.get("status") != "ERROR" or not predecessor.exists()
                            or state.get("error") != "RUN_CODE_DRIFT_REQUIRES_AUDITED_REPARSE"
                            or state.get("completed_segments") or state.get("detail_results")
                            or any((self.output / "ranking_snapshots").rglob("*.json"))
                            or any((self.output / "detail_evidence").rglob("*.json"))):
                        raise ValueError("DELIVERY_AUTO_MIGRATION_NOT_ELIGIBLE")
                    immutable_json(auto_migration_path, {
                        "reason": reason, "created_at": utc(),
                        "predecessor_sha256": file_hash(predecessor),
                        "old_code_tree_sha256": effective_hash,
                        "new_code_tree_sha256": self.metadata["code_tree_sha256"],
                        "original_metadata_sha256": file_hash(metadata_path),
                        "prior_state": state, "prior_state_sha256": canonical_json_sha256(state),
                        "preflight_evidence": {str(p.relative_to(self.output)): file_hash(p)
                                               for p in sorted((self.output / "preflight").glob("*")) if p.is_file()}})
                if not auto_migration_path.exists():
                    predecessor = auto_migration_path
                    continue
                auto = read_json(auto_migration_path, {})
                state = auto.get("prior_state", {})
                if (auto.get("reason") != reason or not predecessor.exists()
                        or auto.get("predecessor_sha256") != file_hash(predecessor)
                        or auto.get("old_code_tree_sha256") != effective_hash
                        or auto.get("original_metadata_sha256") != file_hash(metadata_path)
                        or auto.get("prior_state_sha256") != canonical_json_sha256(state)
                        or state.get("status") != "ERROR"
                        or state.get("error") != "RUN_CODE_DRIFT_REQUIRES_AUDITED_REPARSE"
                        or state.get("completed_segments") or state.get("detail_results")):
                    raise ValueError("INVALID_DELIVERY_AUTO_MIGRATION")
                for relative, expected in auto["preflight_evidence"].items():
                    if file_hash(self.output / relative) != expected:
                        raise ValueError("MIGRATION_EVIDENCE_DRIFT")
                effective_hash = auto["new_code_tree_sha256"]
                predecessor = auto_migration_path
            if effective_hash != self.metadata["code_tree_sha256"]:
                raise ValueError("INVALID_PREFLIGHT_CODE_MIGRATION")
        else:
            immutable_json(metadata_path, self.metadata)
        immutable_json(self.output / "reviewed_plan_copy.json", raw)
        self.store = VersionedCheckpointStore(self.output / "checkpoints", "link_first_state")
        self.state = self.store.load_or_migrate(default={"phase": "SAMPLE", "status": "PREPARED",
                                                        "completed_segments": {}, "detail_results": {},
                                                        "last_category": "", "cooldown_until": 0})
        self.session_factory, self.sleep, self.now = session_factory, sleep, now
        self.links = []
        self.records = {}
        self.selected = {}
        self._systemic = Counter(self.state.get("systemic_counts", {}))
        self._recover()

    def runtime_metadata(self):
        """Opt-in executors bind their extra inputs without changing legacy defaults."""
        return {}

    def parse_detail_evidence(self, html, identity, link, meta):
        return parse_detail_page(html, identity["asin"])

    def selection_eligible(self, row, link):
        """Optional same-batch identity exclusion; default collection is unchanged."""
        return True

    def _recover(self):
        # Verify immutable data and evidence BEFORE a browser can be created.
        for segment, expected_hash in self.state["completed_segments"].items():
            path = self.output / "ranking_snapshots" / segment / "manifest.json"
            value = read_json(path, None)
            if canonical_json_sha256(value) != expected_hash:
                raise ValueError("SNAPSHOT_MANIFEST_DRIFT")
            for evidence in value["pages"]:
                if file_hash(self.output / evidence["html_file"]) != evidence["html_sha256"]:
                    raise ValueError("RANKING_HTML_DRIFT")
            self.links.extend(value["records"])
        for item_id, binding in self.state["detail_results"].items():
            path = self.output / binding["result_file"]
            if file_hash(path) != binding["sha256"]:
                raise ValueError("DETAIL_RESULT_DRIFT")
            result = read_json(path, None)
            if result.get("html_file") and file_hash(self.output / result["html_file"]) != result["html_sha256"]:
                raise ValueError("DETAIL_HTML_DRIFT")
            if result["status"] == "SUCCESS":
                row = result["record"]
                self.records.setdefault(row["asin"], row)
                if result.get("selected"):
                    self.selected[row["asin"]] = {"item_id": item_id, "research_category": result["research_category"],
                                                   "source_url": result["source_url"]}

    def save(self, status: str, **extra):
        self.state.update(status=status, updated_at=utc(), systemic_counts=dict(self._systemic), **extra)
        self.store.save(self.state)
        write_json_atomic(self.output / "progress.json", {
            "contract": CONTRACT, "status": status, "phase": self.state["phase"],
            "updated_at": self.state["updated_at"], "snapshot_links": len(self.links),
            "attempted_links": len(self.state["detail_results"]), "confirmed_unique": len(self.records),
            "selected_unique": len(self.selected), "target_unique": self.plan["target_unique"],
            "active_url": self.state.get("active_url", ""),
            "category": self.state.get("last_category", ""),
            "cooldown_until": self.state.get("cooldown_until"),
            "error": self.state.get("error", ""), "translation": "disabled"})

    def guards(self):
        if (file_hash(self.plan_path) != self.metadata["reviewed_plan_file_sha256"]
                or file_hash(Path(self.metadata["source_snapshot"])) != self.metadata["source_snapshot_sha256"]):
            raise ValueError("PLAN_OR_SOURCE_EVIDENCE_DRIFT")
        if runtime_code_hash(self.root) != self.metadata["code_tree_sha256"]:
            raise ValueError("RUN_CODE_DRIFT_REQUIRES_AUDITED_REPARSE")

    def _manual_hold(self, status: str, error: str):
        """No page actions/requests while held. Explicit human resume is required."""
        token = hashlib.sha256((utc() + error).encode()).hexdigest()
        self.save(status, error=error, hold_token=token)
        print(status + ": " + error, flush=True)
        while True:
            control = read_json(self.output / "operator_control.json", {})
            if control.get("hold_token") == token and control.get("action") == "resume" and control.get("human_handled") is True:
                self.guards()
                self.save("RUNNING", error="", hold_token="")
                return
            self.sleep(1)

    def _access_clear_by_human(self, session, error: str):
        while True:
            self._manual_hold("ACCESS_STOP", error)
            html = session.page.content()
            if detect_access_status(200, html).value == "NORMAL":
                return html

    def _category(self, group: str):
        previous = self.state.get("last_category")
        if previous and previous != group:
            deadline = self.state.get("cooldown_until") or self.now() + self.metadata["cooldown_seconds"]
            self.save("COOLDOWN", cooldown_until=deadline, next_category=group)
            while self.now() < deadline:
                self.sleep(min(1, deadline - self.now()))
        self.save("RUNNING", last_category=group, cooldown_until=0)

    def _verify_delivery_location(self, session, html, *, auto_setup=False):
        """Read-only verification; failed human acknowledgements never close the page."""
        directory = self.output / "preflight"
        directory.mkdir(parents=True, exist_ok=True)
        check = len(list(directory.glob("delivery_check_*.html")))
        while True:
            access = detect_access_status(200, html).value
            location = inspect_delivery_location(html)
            check += 1
            path = directory / ("delivery_check_%03d.html" % check)
            path.write_text(html, encoding="utf-8")
            immutable_json(path.with_suffix(".meta.json"), {
                "observed_at": utc(), "final_url": str(session.page.url),
                "header_text": location.text, "is_spain": location.is_spain,
                "access_state": access, "html_sha256": file_hash(path)})
            if access in RESTRICTED:
                html = self._access_clear_by_human(session, "Delivery verification observed " + access)
                continue
            if location.is_spain is True:
                return location
            if auto_setup and not self.state.get("delivery_auto_attempted_v2"):
                self.guards()
                self.save("RUNNING", delivery_auto_attempted=True, delivery_auto_attempted_v2=True, error="")
                error = ""
                try:
                    session.ensure_spain_delivery("28001", use_current_page=True)
                except Exception as exc:
                    error = str(exc)
                html = session.page.content()
                attempt_path = directory / "delivery_auto_attempt.json"
                if attempt_path.exists():
                    attempt_path = directory / "delivery_auto_attempt_v2.json"
                immutable_json(attempt_path, {
                    "observed_at": utc(), "postal_code": "28001", "error": error,
                    "diagnostics": getattr(session, "delivery_diagnostics", {}),
                    "access_state": detect_access_status(200, html).value})
                continue
            self._manual_hold("DELIVERY_LOCATION_HOLD",
                              "Spain delivery not verified; current header: " + (location.text or "[missing]"))
            # Allow the operator's delivery modal/header to settle, without navigation.
            for _ in range(5):
                html = session.page.content()
                if (detect_access_status(200, html).value in RESTRICTED
                        or inspect_delivery_location(html).is_spain is True):
                    break
                self.sleep(1)

    def _capture(self, session, url: str, destination: Path, *, ranking=False):
        self.guards()
        self.save("RUNNING", active_url=url)
        destination.mkdir(parents=True, exist_ok=True)
        attempt = len(list(destination.glob("attempt_*.html")))
        failures = 0
        while True:
            attempt += 1
            status, html, error = None, "", ""
            try:
                status = session.goto(url)
                # Inspect before waiting, scrolling, or any other page action.
                html = session.page.content()
                state = detect_access_status(status, html).value
                if state not in RESTRICTED:
                    session.wait_for_product_page()
                    if ranking and state == "NORMAL":
                        session.load_lazy_ranking_content()
                    elif not ranking and state == "NORMAL":
                        session.wait_for_price_text()
                    html = session.page.content()
                    state = detect_access_status(status, html).value
                    # Accept only a rendered 202 shell with explicit Amazon page evidence.
                    if status == 202 and state == "UNKNOWN":
                        from bs4 import BeautifulSoup
                        dom = BeautifulSoup(html, "lxml")
                        if dom.select_one("#productTitle, #nav-global-location-popover-link, #glow-ingress-line2, #gridItemRoot"):
                            state = detect_access_status(200, html).value
            except Exception as exc:
                error = str(exc)
                try:
                    html = session.page.content()
                except Exception:
                    pass
                state = detect_access_status(status, html).value
            html_path = destination / ("attempt_%03d.html" % attempt)
            html_path.write_text(html, encoding="utf-8")
            meta = {"requested_url": url, "final_url": str(session.page.url), "http_status": status,
                    "access_state": state, "observed_at": utc(), "error": error,
                    "html_file": str(html_path.relative_to(self.output)), "html_sha256": file_hash(html_path)}
            immutable_json(html_path.with_suffix(".meta.json"), meta)
            if state in RESTRICTED:
                # No new navigation, even if a premature human resume leaves the restriction.
                html = self._access_clear_by_human(session, "%s %s" % (state, url))
                resolved = "NORMAL"
                recovered_path = destination / ("human_handled_%03d.html" % attempt)
                recovered_path.write_text(html, encoding="utf-8")
                meta.update(html_file=str(recovered_path.relative_to(self.output)), html_sha256=file_hash(recovered_path),
                            final_url=str(session.page.url), access_state=resolved, human_handled=True)
                immutable_json(recovered_path.with_suffix(".meta.json"), meta)
                session.wait_between_requests()
                return html, meta
            if error or state != "NORMAL":
                failures += 1
                if failures >= 2:
                    return None, meta
                session.wait_between_requests()
                continue  # one ordinary safe reopen, never a restriction retry
            session.wait_between_requests()
            return html, meta

    def _snapshot(self, session, category: dict, role: str):
        group = category["research_category"]
        segment = hashlib.sha256((str(self.output) + "|" + group + "|" + role).encode()).hexdigest()[:16]
        if segment in self.state["completed_segments"]:
            return
        directory = self.output / "ranking_snapshots" / segment
        statuses, rows = [], []
        sources = [s for s in category["sources"] if s["role"] == role]
        for index, source in enumerate(sources):
            for page in range(1, int(source.get("pages_per_url", category["pages_per_url"])) + 1):
                evidence_dir = directory / ("source_%03d_page_%02d" % (index, page))
                page_result = evidence_dir / "page_result.json"
                if page_result.exists():
                    result = read_json(page_result, None)
                    if file_hash(self.output / result["evidence"]["html_file"]) != result["evidence"]["html_sha256"]:
                        raise ValueError("RANKING_HTML_DRIFT")
                else:
                    html, meta = self._capture(session, page_url(source["source_url"], page), evidence_dir, ranking=True)
                    if html is None:
                        self._manual_hold("LOAD_FAILURE_HOLD", "Ranking load failed twice: " + source["source_url"])
                        raise ValueError("RANKING_RESTART_REQUIRED_AFTER_LOAD_FAILURE")
                    from ..collection.quota import normalize_source_url
                    if normalize_source_url(meta["final_url"]) != normalize_source_url(source["source_url"]):
                        self._manual_hold("RANKING_REDIRECT_HOLD", "Ranking landed outside its reviewed source: " + meta["final_url"])
                        raise ValueError("RANKING_CONTEXT_UNCONFIRMED")
                    parsed = parse_ranking_links(html, source["source_url"], page, meta["observed_at"], group)
                    if not parsed:
                        self._manual_hold("PARSER_HOLD", "No ranking cards: " + source["source_url"])
                        raise ValueError("RANKING_PARSER_REPAIR_REQUIRED")
                    for row in parsed:
                        row.update(snapshot_id=segment, source_role=role, ranking_page_url=page_url(source["source_url"], page),
                                   html_file=meta["html_file"], html_sha256=meta["html_sha256"])
                    result = {"evidence": meta, "records": parsed}
                    immutable_json(page_result, result)
                statuses.append(result["evidence"])
                rows.extend(result["records"])
        manifest = {"contract": CONTRACT, "snapshot_id": segment, "research_category": group,
                    "role": role, "records": rows, "pages": statuses, "asin_extraction": False,
                    "status": "CAPTURED_LINKS", "source_gate": "NOT_EVALUATED"}
        immutable_json(directory / "manifest.json", manifest)
        self.state["completed_segments"][segment] = canonical_json_sha256(manifest)
        self.links.extend(rows)
        self.save("RUNNING")

    def _eligible_links(self, category):
        group = category["research_category"]
        return sorted((r for r in self.links if r["research_category"] == group
                       and r["snapshot_audit"] == "PASS" and category_rank_filter(r, category, self.plan)),
                      key=lambda r: (r["source_role"] != "primary", r["bestseller_rank"], r["ranking_source_url"]))

    def _category_count(self, group):
        return sum(v["research_category"] == group for v in self.selected.values())

    def _detail(self, session, link: dict, category: dict):
        item_id = link["snapshot_item_id"]
        if item_id in self.state["detail_results"]:
            return
        directory = self.output / "detail_evidence" / item_id
        html, meta = self._capture(session, link["product_link"], directory)
        result = {**meta, "snapshot_item_id": item_id, "research_category": link["research_category"],
                  "source_url": link["ranking_source_url"], "status": "LOAD_FAILED", "selected": False}
        if html is not None:
            identity = resolve_selected_identity(html, link["product_link"], meta["final_url"])
            row = self.parse_detail_evidence(html, identity, link, meta)
            # Category columns use an evidenced path; both distinct paths are retained.
            trail = row.get("detail_category_trail") or link["category_trail"]
            l1, l2, l3, leaf = category_levels(trail)
            row.update(category_l1=l1, category_l2=l2, category_l3=l3, leaf_category=leaf,
                       category_evidence_source="PRODUCT_BREADCRUMB" if row.get("detail_category_trail") else "BESTSELLER_PLACEMENT",
                       product_url=meta["final_url"], final_url=meta["final_url"], requested_url=link["product_link"],
                       observed_at=meta["observed_at"], detail_identity=identity,
                       visible_detail_identity_review=True, html_file=meta["html_file"], html_sha256=meta["html_sha256"],
                       offline_replay_evidence={"html_sha256": meta["html_sha256"]},
                       detail_identity_signals=identity["signals"])
            row["parent_asin_raw"] = row.get("parent_asin")
            row["parent_asin"] = identity.get("parent_asin", "")
            row["variation_family_asins"] = identity.get("variation_family_asins", [])
            audit = audit_detail_fields(html, row, identity)
            # Preserve raw price; invalid or unexplained relationships remain reviewable.
            row.update(spanish_candidate_fields(row,ranking_context=link))
            for name in ("current_price", "original_price"):
                if row.get(name + "_raw") and parse_price(row.get(name + '_raw')) is None:
                    audit["issues"].append(dict(code="SOURCE_VALUE_TYPE_REVIEW", field=name, severity="WARN"))
            original_raw_price = parse_price(row.get('original_price_raw'))
            if row["current_price"] and original_raw_price and original_raw_price <= row["current_price"]:
                audit["issues"].append(dict(code="PRICE_RELATION_REVIEW", field="original_price", severity="WARN"))
            # Deterministic Spanish candidate fields; no translation/provider calls.
            from ..normalization.dates import parse_es_date
            from ..normalization.monthly_bought import parse_monthly_bought
            from ..pipeline import _to_int_spanish, _to_rating_num
            row["brand"] = row.get("brand_raw", "")
            row["rating"] = _to_rating_num(row.get("rating_raw"))
            row["review_count"] = _to_int_spanish(row.get("review_count_raw"))
            row["monthly_bought_min"] = parse_monthly_bought(row.get("monthly_bought_raw"))
            date = parse_es_date(row.get("date_first_available_raw"))
            row["date_first_available"] = date.strftime("%Y-%m-%d") if date else None
            for source_name, target_name in (("rating_raw", "rating"), ("review_count_raw", "review_count"),
                                             ("monthly_bought_raw", "monthly_bought_min"), ("date_first_available_raw", "date_first_available")):
                if row.get(source_name) and row.get(target_name) is None:
                    audit["issues"].append(dict(code="MAPPING_MISSED", field=target_name, severity="WARN"))
            source_check = audit_source_fields([row])
            for i in source_check["issues"]:
                if i["issue_code"] in {"SPEC_UNIT_TYPE_MISMATCH", "UNIT_SEMANTICS_AMBIGUOUS", "SOURCE_SEMANTIC_CONFLICT", "MISPLACED"}:
                    audit["issues"].append(dict(code=i["issue_code"], field=i.get("field", "attributes"), severity="WARN", evidence=i.get("evidence", {})))
            if not row["specification"]:
                audit["issues"].append(dict(code="DERIVED_MISSING" if row.get("attributes") else "SOURCE_MISSING", field="specification", severity="WARN"))
            if audit["status"] != "BLOCK" and audit["issues"]:
                audit["status"] = "WARN"
            result.update(record=row, audit=audit, identity=identity, status="QUARANTINED" if audit["status"] == "BLOCK" else "SUCCESS")
            codes = {i["code"] for i in audit["issues"] if i["severity"] == "BLOCK" and i["code"] in {
                "FIELD_BINDING_ERROR", "PARSER_MISSED", "BULLETS_BINDING_ERROR", "STRUCTURE_ERROR"}}
            for code in set(self._systemic) | codes:
                self._systemic[code] = self._systemic[code] + 1 if code in codes else 0
            if result["status"] == "SUCCESS":
                self.records.setdefault(row["asin"], row)
                group = link["research_category"]
                used_source = sum(v["research_category"] == group and v["source_url"] == link["ranking_source_url"] for v in self.selected.values())
                cap = math.ceil(category["target_unique"] * category["max_single_source_share"])
                eligible = self.selection_eligible(row, link)
                if not eligible:
                    result["selection_excluded"] = True
                if eligible and row["asin"] not in self.selected and used_source < cap and self._category_count(group) < category["target_unique"]:
                    result["selected"] = True
                    self.selected[row["asin"]] = {"item_id": item_id, "research_category": group, "source_url": link["ranking_source_url"]}
        path = directory / "result.json"
        immutable_json(path, result)
        self.state["detail_results"][item_id] = {"result_file": str(path.relative_to(self.output)), "sha256": file_hash(path), "status": result["status"]}
        self.save("RUNNING")
        if any(count >= 3 for count in self._systemic.values()):
            self._manual_hold("SYSTEMATIC_PARSER_HOLD", "Three consecutive parser/binding failures; offline repair required")
            raise ValueError("SYSTEMATIC_PARSER_REPAIR_REQUIRES_AUDITED_RESUME")
        if len(self.state["detail_results"]) % 100 == 0:
            self.batch_audit("batch_%06d" % len(self.state["detail_results"]))

    def batch_audit(self, name):
        statuses, codes, fields = Counter(), Counter(), Counter()
        for binding in self.state["detail_results"].values():
            result = read_json(self.output / binding["result_file"], None)
            statuses[result["status"]] += 1
            for i in result.get("audit", {}).get("issues", []):
                codes[i["code"]] += 1
                fields[i["field"]] += 1
        report = {"contract": CONTRACT, "attempted": len(self.state["detail_results"]),
                  "confirmed_unique": len(self.records), "statuses": dict(statuses), "issues": dict(codes),
                  "field_empty_or_issue_counts": dict(fields), "formal_gate_unchanged": True,
                  "systematic_parser_error": any(v >= 3 for v in self._systemic.values())}
        immutable_json(self.output / "staged_audit" / (name + ".json"), report)
        return report

    def _sample(self, session):
        if self.state["phase"] != "SAMPLE":
            return
        categories = self.plan["categories"]
        base, extra = divmod(self.metadata["sample_size"], len(categories))
        for index, category in enumerate(categories):
            budget = base + (index < extra)
            if not budget:
                continue
            group = category["research_category"]
            already = sum(read_json(self.output / b["result_file"], {})["research_category"] == group for b in self.state["detail_results"].values())
            if already >= budget:
                continue
            self._category(group)
            self._snapshot(session, category, "primary")
            # Round-robin source positions diversify the canary within each category.
            links = self._eligible_links(category)
            sources = sorted({r["ranking_source_url"] for r in links})
            by_source = {source: [x for x in links if x["ranking_source_url"] == source
                                 and x["snapshot_item_id"] not in self.state["detail_results"]]
                         for source in sources}
            ordered = [by_source[source][n] for n in range(budget) for source in sources
                       if len(by_source[source]) > n]
            seen = set()
            for link in ordered:
                if already >= budget:
                    break
                if link["snapshot_item_id"] in seen:
                    continue
                seen.add(link["snapshot_item_id"])
                self._detail(session, link, category)
                already += 1
            if already < budget:
                raise ValueError("SAMPLE_LINK_SHORTFALL: " + group)
        report = self.batch_audit("sample_final")
        hard = {"FIELD_BINDING_ERROR", "PARSER_MISSED", "BULLETS_BINDING_ERROR", "STRUCTURE_ERROR"}
        success = report["statuses"].get("SUCCESS", 0)
        if (hard & set(report["issues"]) or success < math.ceil(self.metadata["sample_size"] * 0.9)
                or report["attempted"] < self.metadata["sample_size"]):
            self.save("SAMPLE_BLOCKED", error="Sample requires offline parser/binding review")
            return
        immutable_json(self.output / "sample_gate.json", {"status": "PASS_COLLECTION_SAMPLE", **report,
                                                         "formal_source_gate": "NOT_EVALUATED"})
        self.save("SAMPLE_COMPLETE", phase="FULL" if self.metadata["continue_after_sample"] else "SAMPLE_DONE")

    def _final(self):
        rankings, products = [], []
        contexts = {}
        for item_id, binding in self.state["detail_results"].items():
            result = read_json(self.output / binding["result_file"], None)
            if result["status"] != "SUCCESS":
                continue
            link = next(r for r in self.links if r["snapshot_item_id"] == item_id)
            context = {**link, "asin": result["record"]["asin"], "identity_resolved_at_detail": True}
            rankings.append(context)
            contexts.setdefault(context["asin"], []).append(context)
        for asin, allocation in self.selected.items():
            row = dict(self.records[asin])
            row["ranking_contexts"] = contexts.get(asin, [])
            selected_context = next(c for c in row["ranking_contexts"] if c["snapshot_item_id"] == allocation["item_id"])
            row.update(bestseller_rank=selected_context["bestseller_rank"], research_category=allocation["research_category"])
            products.append(row)
        immutable_json(self.output / "resolved_ranking_contexts.json", rankings)
        immutable_json(self.output / "spanish_candidate.json", products)
        immutable_json(self.output / "detail_resolved_manifest.json", {"contract": CONTRACT,
                       "candidate_hash": canonical_json_sha256(products), "allocations": self.selected})
        counts = {c["research_category"]: self._category_count(c["research_category"]) for c in self.plan["categories"]}
        quotas = {c["research_category"]: c["target_unique"] for c in self.plan["categories"]}
        quota_ok = counts == quotas and len(products) == self.plan["target_unique"] and len({r["asin"] for r in products}) == len(products)
        formal = audit_source_fields(products, ranking_matrix=rankings)
        immutable_json(self.output / "source_audit.json", formal)
        passed, gate = source_gate(formal)
        report = {"contract": CONTRACT, "status": "COLLECTION_COMPLETE" if quota_ok else "QUOTA_UNIQUE_SHORTFALL",
                  "candidate_count": len(products), "confirmed_unique": len(self.records), "quota_counts": counts,
                  "reviewed_quotas": quotas, "source_gate": gate, "source_gate_passed": passed,
                  "formal_release": False, "translation": "disabled", "review_required": True}
        immutable_json(self.output / "collection_report.json", report)
        self.save(report["status"], phase="FINISHED", active_url="")
        return report

    def run(self, *, profile_dir: str, auto_spain_delivery: bool = False):
        self.guards()
        if self.state["phase"] == "FINISHED":
            return read_json(self.output / "collection_report.json", {})
        with self.session_factory(headless=False, profile_dir=profile_dir) as session:
            session.manual_assist = True
            session.challenge_wait_seconds = 0
            if (self.state["status"] in {"ACCESS_STOP", "SYSTEMATIC_PARSER_HOLD", "PARSER_HOLD", "RANKING_REDIRECT_HOLD", "LOAD_FAILURE_HOLD", "DELIVERY_LOCATION_HOLD"}
                    and not (auto_spain_delivery and self.state["status"] == "DELIVERY_LOCATION_HOLD")):
                self._manual_hold(self.state["status"], "Prior hold requires explicit operator approval before new requests")
            # Preflight capture uses the same access hold before any delivery UI action.
            html, meta = self._capture(session, "https://www.amazon.es/", self.output / "preflight")
            if html is None:
                self._manual_hold("LOAD_FAILURE_HOLD", "Amazon homepage ordinary loading failed twice")
                raise ValueError("PREFLIGHT_FAILED")
            location = self._verify_delivery_location(session, html, auto_setup=auto_spain_delivery)
            session._delivery_location_checked = True
            session._delivery_location = location
            immutable_json(self.output / "delivery_preflight.json", {"location": location.text, "is_spain": True})
            self._sample(session)
            if self.state["phase"] != "FULL":
                return {"status": self.state["status"]}
            for category in self.plan["categories"]:
                group = category["research_category"]
                if self._category_count(group) >= category["target_unique"]:
                    continue
                self._category(group)
                self._snapshot(session, category, "primary")
                for role in ("primary", "reserve"):
                    if self._category_count(group) >= category["target_unique"]:
                        break
                    if role == "reserve":
                        self._snapshot(session, category, role)
                    for link in self._eligible_links(category):
                        if self._category_count(group) >= category["target_unique"]:
                            break
                        if link["source_role"] == role:
                            self._detail(session, link, category)
            return self._final()
