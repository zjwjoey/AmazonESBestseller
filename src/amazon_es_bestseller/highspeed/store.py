"""Transactional indexes over immutable HTML/page journals, without ASIN keys.

SQLite batches avoid rewriting an ever-growing JSON registry. Published page
journals are the recoverable authority; the database is a checked index.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3
import uuid

from ..multisource.contracts import timestamp, canonical_page_url
from ..multisource.storage import (atomic_json, canonical_bytes, digest, file_lock,
    immutable_bytes, immutable_json, read_sealed, sealed)
from .links import normalize_product_url, candidate_id, NORMALIZER_VERSION


@lru_cache(maxsize=100000)
def _current_review_key(normalized_url, normalizer_version):
    """Cache pure derived review keys, never identity/auth/raw modifications."""
    return normalize_product_url(normalized_url).get("possible_duplicate_key") or normalized_url


def _safe(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(value)) or value in {".", ".."}:
        raise ValueError("INVALID_HIGH_SPEED_RUN_ID")
    return str(value)


def _json(value):
    return canonical_bytes(value).decode("utf-8")


class RunStore:
    def __init__(self, root):
        from .path_checks import validate_store_root
        self.root = validate_store_root(root)
        marker = self.root / ".highspeed_store.json"
        if self.root.exists() and not marker.exists() and any(self.root.iterdir()):
            raise ValueError("ISOLATED_EMPTY_HIGH_SPEED_STORE_REQUIRED")
        self.root.mkdir(parents=True, exist_ok=True)
        immutable_json(marker, {"schema_version": 2, "module": "highspeed_multisource_v2"})
        if json.loads(marker.read_text(encoding="utf-8")) != {"schema_version": 2, "module": "highspeed_multisource_v2"}:
            raise ValueError("HIGH_SPEED_STORE_MARKER_CONFLICT")
        self.db = sqlite3.connect(self.root / "index.sqlite3", timeout=20, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        # Keep FULL durability with an 8192-frame checkpoint threshold (~32 MiB
        # for 4-KiB pages; one bounded transaction can exceed the threshold).
        # The default 1000 frames repeatedly flushes
        # the random URL/index pages during 500-card immutable transactions.
        self.db.execute("PRAGMA wal_autocheckpoint=8192")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,plan_hash TEXT NOT NULL,
          plan_json TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT,status TEXT NOT NULL,metadata_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,
          kind TEXT NOT NULL,local_day TEXT NOT NULL,observed_at TEXT NOT NULL,status TEXT NOT NULL,data_json TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES runs(run_id));
        CREATE INDEX IF NOT EXISTS request_run_day ON requests(run_id,local_day);
        CREATE TABLE IF NOT EXISTS responses(request_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,data_json TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id));
        CREATE TABLE IF NOT EXISTS pages(run_id TEXT NOT NULL,page_key TEXT NOT NULL,journal_path TEXT NOT NULL,
          data_json TEXT NOT NULL,PRIMARY KEY(run_id,page_key),FOREIGN KEY(run_id) REFERENCES runs(run_id));
        CREATE TABLE IF NOT EXISTS candidates(candidate_id TEXT PRIMARY KEY,url TEXT NOT NULL UNIQUE,possible_key TEXT);
        CREATE INDEX IF NOT EXISTS candidate_possible ON candidates(possible_key);
        CREATE TABLE IF NOT EXISTS observations(observation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,page_key TEXT NOT NULL,
          candidate_id TEXT,raw_url TEXT,dedup_status TEXT NOT NULL,data_json TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES runs(run_id),FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id));
        CREATE INDEX IF NOT EXISTS observation_run ON observations(run_id);
        CREATE INDEX IF NOT EXISTS observation_candidate ON observations(run_id,candidate_id,raw_url);
        CREATE TABLE IF NOT EXISTS run_candidates(run_id TEXT NOT NULL,candidate_id TEXT NOT NULL,
          first_observation_id TEXT NOT NULL,first_seen TEXT,last_seen TEXT,observation_count INTEGER NOT NULL,
          PRIMARY KEY(run_id,candidate_id),FOREIGN KEY(run_id) REFERENCES runs(run_id));
        CREATE TABLE IF NOT EXISTS events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,data_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS destination_reviews(review_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,data_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pending_pages(run_id TEXT NOT NULL,journal_path TEXT NOT NULL,raw_count INTEGER NOT NULL,
          PRIMARY KEY(run_id,journal_path));
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def run_lock(self, run_id):
        _safe(run_id)
        return file_lock(self.root / "highspeed_runner.lock")

    def _directory(self, run_id):
        return self.root / "runs" / _safe(run_id)

    def begin_run(self, plan, *, started_at=None):
        run_id = _safe(plan["run_id"])
        from .path_checks import validate_run_storage_root
        validate_run_storage_root(self.root, run_id)
        original = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        hashed = digest(plan)
        if original:
            if original["plan_hash"] != hashed:
                raise ValueError("IMMUTABLE_HIGH_SPEED_PLAN_CONFLICT")
            self.recover(run_id)
            return self.get_run(run_id)
        directory = self._directory(run_id)
        immutable_json(directory / "plan.json", sealed(plan))
        started = started_at or timestamp()["observed_at"]
        parsed_start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        if parsed_start.tzinfo is None:
            raise ValueError("AWARE_EXECUTOR_START_TIME_REQUIRED")
        started = parsed_start.astimezone(timezone.utc).isoformat()
        # A start journal survives a crash before SQLite publication.
        start_path = directory / "started.json"
        if start_path.exists():
            started = read_sealed(start_path)["started_at"]
        else:
            immutable_json(start_path, sealed({"run_id": run_id, "started_at": started, "plan_hash": hashed}))
        with self._transaction():
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                (run_id, hashed, _json(plan), started, None, "RUNNING", "{}"))
        self.recover(run_id)
        return self.get_run(run_id)

    def get_run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (_safe(run_id),)).fetchone()
        if row is None:
            raise ValueError("HIGH_SPEED_RUN_NOT_FOUND")
        plan = json.loads(row["plan_json"])
        return {**plan, **json.loads(row["metadata_json"]), "plan": plan,
            "started_at": row["started_at"], "ended_at": row["ended_at"], "status": row["status"]}

    def update_run(self, run_id, **fields):
        run = self.get_run(run_id)
        metadata = dict(run.get("metadata", {}))
        row = self.db.execute("SELECT metadata_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        metadata.update(json.loads(row["metadata_json"]))
        explicit_end = "ended_at" in fields
        ended, status = fields.pop("ended_at", run.get("ended_at")), fields.pop("status", run["status"])
        if status == "RUNNING":
            if not explicit_end:
                ended = None
            # A stopped attempt is not this resumed attempt's completion.
            for name in ("finished_at", "completed_at"):
                metadata.pop(name, None)
        elif not explicit_end and fields.get("finished_at"):
            ended = fields["finished_at"]
        metadata.update(fields)
        with self._transaction():
            self.db.execute("UPDATE runs SET ended_at=?,status=?,metadata_json=? WHERE run_id=?",
                (ended, status, _json(metadata), run_id))
        return self.get_run(run_id)

    def get_gate(self):
        path = self.root / "access_gate.json"
        gate = read_sealed(path) if path.exists() else {"state": "NORMAL"}
        if gate.get("state") != "NORMAL":
            return gate
        handled=set(gate.get("human_handled_request_ids",[])) | set(gate.get("agent_handled_request_ids",[]))
        for row in self.db.execute("SELECT request_id,run_id FROM requests WHERE status='RESERVED'"):
            if row["request_id"] in handled:
                continue
            receipt = self._directory(row["run_id"]) / "responses" / (row["request_id"] + ".json")
            if receipt.exists():
                value = read_sealed(receipt)
                if value.get("origin") == "LIVE_CAPTURE" and value.get("access_state") != "NORMAL":
                    return {"state": value.get("access_state", "UNKNOWN"),
                        "evidence_path": str(receipt), "recovered_from_saved_receipt": True}
        # A crash after receipt publication must not lose a restriction. Only
        # real LIVE receipts are relevant; fixtures cannot restrict real access.
        for row in self.db.execute("SELECT data_json FROM responses ORDER BY rowid DESC"):
            value = json.loads(row[0])
            if value.get("request_id") in handled:
                continue
            if value.get("origin") == "LIVE_CAPTURE" and value.get("access_state") != "NORMAL":
                return {"state": value.get("access_state", "UNKNOWN"),
                    "evidence_path": value["evidence_path"], "recovered_from_saved_receipt": True}
        return gate

    def set_gate(self, state, **context):
        prior = self.get_gate()
        if state == "NORMAL" and prior.get("state") != "NORMAL":
            raise ValueError("EXPLICIT_HUMAN_GATE_RECOVERY_REQUIRED")
        value = {"state": state, **context, **timestamp(), "previous_gate": prior}
        atomic_json(self.root / "access_gate.json", sealed(value))
        return value

    def recover_gate(self, authorization):
        """Explicit human handling of this exact local gate, never an auto retry.

        The shared project gate is intentionally untouched and still has to be
        human handled before any live authorization can pass.
        """
        from .budgets import aware_time
        prior=self.get_gate()
        now=aware_time()
        if (authorization.get("approved") is not True or authorization.get("human_handled") is not True
                or not authorization.get("approved_by") or not authorization.get("approval_id")
                or not authorization.get("reviewed_at") or not authorization.get("expires_at")
                or authorization.get("store_root") != str(self.root)
                or authorization.get("gate_sha256") != digest(prior)
                or aware_time(authorization.get("reviewed_at")) > now
                or aware_time(authorization.get("expires_at")) <= now):
            raise ValueError("EXACT_VALID_HUMAN_GATE_RECOVERY_AUTHORIZATION_REQUIRED")
        if prior.get("state") == "NORMAL":
            raise ValueError("NO_RESTRICTED_GATE_TO_RECOVER")
        handled=set()
        current_path=self.root/"access_gate.json"
        if current_path.exists():
            handled.update(read_sealed(current_path).get("human_handled_request_ids",[]))
        for row in self.db.execute("SELECT data_json FROM responses"):
            receipt=json.loads(row[0])
            if receipt.get("origin") == "LIVE_CAPTURE" and receipt.get("access_state") != "NORMAL":
                if aware_time(receipt.get("observed_at")) > aware_time(authorization["reviewed_at"]):
                    raise ValueError("GATE_RESTRICTION_NEWER_THAN_HUMAN_HANDLING")
                handled.add(receipt["request_id"])
        for row in self.db.execute("SELECT request_id,run_id FROM requests WHERE status='RESERVED'"):
            path=self._directory(row["run_id"])/"responses"/(row["request_id"]+".json")
            if path.exists():
                receipt=read_sealed(path)
                if receipt.get("origin") == "LIVE_CAPTURE" and receipt.get("access_state") != "NORMAL":
                    if aware_time(receipt.get("observed_at")) > aware_time(authorization["reviewed_at"]):
                        raise ValueError("GATE_RESTRICTION_NEWER_THAN_HUMAN_HANDLING")
                    handled.add(receipt["request_id"])
        evidence={"prior_gate":prior,"authorization":authorization,**timestamp()}
        path=self.root/"gate_recoveries"/(uuid.uuid4().hex+".json")
        immutable_json(path,sealed(evidence))
        value={"state":"NORMAL","human_handled_request_ids":sorted(handled),
            "human_recovery_evidence":str(path),"previous_gate":prior,**timestamp()}
        atomic_json(current_path,sealed(value))
        return value

    def save_html(self, html):
        data = html.encode("utf-8") if isinstance(html, str) else bytes(html)
        hashed = digest(data)
        path = self.root / "html" / hashed[:2] / (hashed + ".html")
        immutable_bytes(path, data)
        return {"evidence_path": str(path), "html_path": str(path), "sha256": hashed, "evidence_sha256": hashed}

    def reserve_request(self, run_id, request):
        run = self.get_run(run_id)
        limits = run["plan"]
        if request.get("limits"):
            for name in ("max_requests_per_run", "max_requests_per_day", "max_pages_per_run",
                    "max_raw_observations_per_run", "history_observation_limit", "max_duration_hours"):
                if request["limits"].get(name) != limits.get(name):
                    raise ValueError("IMMUTABLE_REQUEST_BUDGET_CONFLICT")
        request_id = request.get("request_id") or "req_" + uuid.uuid4().hex
        observed = timestamp(request.get("observed_at"))["observed_at"]
        day = timestamp(observed)["local_date"]
        kind = request.get("kind", "PAGE")
        if kind not in {"PAGE", "ACP"}:
            raise ValueError("CONTROLLED_REQUEST_KIND_REQUIRED")
        with self._transaction():
            count = self.db.execute("SELECT count(*) FROM requests WHERE run_id=?", (run_id,)).fetchone()[0]
            daily = self.db.execute("SELECT count(*) FROM requests WHERE local_day=?", (day,)).fetchone()[0]
            page_count = self.db.execute("SELECT count(*) FROM requests WHERE run_id=? AND kind='PAGE'", (run_id,)).fetchone()[0]
            if count >= limits["max_requests_per_run"]:
                raise ValueError("RUN_REQUEST_BUDGET_EXHAUSTED")
            if limits.get("max_requests_per_day") is not None and daily >= limits["max_requests_per_day"]:
                raise ValueError("DAILY_REQUEST_BUDGET_EXHAUSTED")
            if kind == "PAGE" and page_count >= limits["max_pages_per_run"]:
                raise ValueError("RUN_PAGE_BUDGET_EXHAUSTED")
            cap_names = ("max_requests_per_run", "max_requests_per_day", "max_pages_per_run",
                "max_raw_observations_per_run", "history_observation_limit", "max_duration_hours")
            compact_request = {k: v for k, v in request.items() if k != "limits"}
            value = {**compact_request, "limits": {k: limits.get(k) for k in cap_names},
                "plan_sha256": digest(run["plan"]), "request_id": request_id, "run_id": run_id,
                "observed_at": observed, "local_day": day, "status": "RESERVED"}
            self.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?)",
                (request_id, run_id, kind, day, observed, "RESERVED", _json(value)))
        immutable_json(self._directory(run_id) / "requests" / (request_id + ".reserved.json"), sealed(value))
        return value

    def save_response(self, run_id, reservation, response):
        request_id = reservation["request_id"] if isinstance(reservation, dict) else str(reservation)
        reserved = self.db.execute("SELECT run_id,data_json FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if reserved is None or reserved["run_id"] != run_id:
            raise ValueError("RESPONSE_REQUIRES_OWN_RESERVED_REQUEST")
        reservation = json.loads(reserved["data_json"])
        response = dict(response)
        text = response.pop("text", response.pop("body", ""))
        if isinstance(response.get("response"), dict):
            nested = dict(response["response"])
            text = nested.pop("body", nested.pop("text", text))
            response["response"] = nested
        body = self.save_html(text)
        receipt_path = self._directory(run_id) / "responses" / (request_id + ".json")
        status = response.get("access_state", response.get("access_status", "UNKNOWN"))
        value = {**response, **body, "request_id": request_id, "run_id": run_id,
            "kind": reservation["kind"], "request_url": reservation.get("request_url", reservation.get("url")),
            "observed_at": response.get("observed_at", reservation.get("observed_at")),
            "response_body_path": body["html_path"], "evidence_path": str(receipt_path),
            "access_state": status}
        immutable_json(receipt_path, sealed(value))
        with self._transaction():
            self.db.execute("INSERT OR REPLACE INTO responses VALUES(?,?,?)", (request_id, run_id, _json(value)))
            self.db.execute("UPDATE requests SET status=? WHERE request_id=?", (status, request_id))
        return {**value, "text": text, "body": text}

    def requests(self, run_id):
        result = []
        for row in self.db.execute("SELECT data_json,status FROM requests WHERE run_id=? ORDER BY observed_at,request_id", (run_id,)):
            result.append({**json.loads(row["data_json"]), "status": row["status"]})
        return result

    def latest_request_time(self):
        row=self.db.execute("SELECT max(observed_at) FROM requests").fetchone()
        return row[0]

    def iter_responses(self, run_id, *, with_body=False):
        for row in self.db.execute("SELECT data_json FROM responses WHERE run_id=?", (run_id,)):
            value = json.loads(row[0])
            if with_body:
                data = Path(value["html_path"]).read_bytes()
                if digest(data) != value["sha256"]:
                    raise ValueError("HIGH_SPEED_RESPONSE_EVIDENCE_CORRUPT")
                value["text"] = value["body"] = data.decode("utf-8")
            yield value

    def get_response(self, request_id, *, with_body=True):
        row = self.db.execute("SELECT data_json FROM responses WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if with_body:
            data = Path(value["html_path"]).read_bytes()
            if digest(data) != value["sha256"]:
                raise ValueError("HIGH_SPEED_RESPONSE_EVIDENCE_CORRUPT")
            value["text"] = value["body"] = data.decode("utf-8")
        return value

    def commit_page(self, run_id, page_key, page, observations):
        run = self.get_run(run_id)
        rows = [dict(value) for value in observations]
        value = {"run_id": run_id, "page_key": str(page_key), "page": dict(page), "observations": rows}
        path = self._directory(run_id) / "pages" / (digest(str(page_key))[:16] + "_" + digest(value)[:16] + ".json")
        immutable_json(path, sealed(value))
        try:
            self._index_page(value, path, run["plan"])
        except BaseException:
            self.db.execute("INSERT OR REPLACE INTO pending_pages VALUES(?,?,?)", (run_id, str(path), len(rows)))
            raise
        self.db.execute("DELETE FROM pending_pages WHERE run_id=? AND journal_path=?", (run_id,str(path)))
        return {"page_key": page_key, "observation_count": len(rows), **self.summary(run_id)}

    def _index_page(self, value, journal_path, plan):
        run_id, page_key, page = value["run_id"], value["page_key"], value["page"]
        indexed = self.db.execute("SELECT journal_path FROM pages WHERE run_id=? AND page_key=?", (run_id, page_key)).fetchone()
        if indexed and indexed[0] == str(journal_path):
            return
        rows = value["observations"]
        page_source_eligible = (page.get("access_status") == "NORMAL"
            and page.get("source_type_verified") is True and page.get("source_context_valid") is True)
        existing_count = self.db.execute("SELECT count(*) FROM observations WHERE run_id=?", (run_id,)).fetchone()[0]
        history_count = self.db.execute("SELECT count(*) FROM observations").fetchone()[0]
        with self._transaction():
            inserted = 0
            for slot, raw in enumerate(rows):
                source_eligible = page_source_eligible and raw.get("source_evidence_eligible") is not False
                oid = raw.get("observation_id") or "obs_" + uuid.uuid5(uuid.NAMESPACE_URL,
                    "|".join((run_id, str(page.get("source_id", "")), canonical_page_url(page.get("page_url", "")),
                        str(raw.get("slot_position", slot)), str(raw.get("rank_context", ""))))).hex
                raw_hash = digest(raw)
                prior_row = self.db.execute("SELECT run_id,page_key,data_json FROM observations WHERE observation_id=?", (oid,)).fetchone()
                parent_oid = None
                if prior_row:
                    if prior_row["run_id"] != run_id or prior_row["page_key"] != page_key:
                        raise ValueError("DUPLICATE_OBSERVATION_SLOT_ACROSS_PAGES")
                    if json.loads(prior_row["data_json"]).get("raw_sha256") == raw_hash:
                        continue
                    parent_oid, oid = oid, oid + "_rev_" + raw_hash[:16]
                    if self.db.execute("SELECT 1 FROM observations WHERE observation_id=?", (oid,)).fetchone():
                        continue
                inserted += 1
                if existing_count + inserted > plan.get("max_raw_observations_per_run", 150000):
                    raise ValueError("RAW_OBSERVATION_RUN_BUDGET_EXHAUSTED")
                if plan.get("history_observation_limit") is not None and history_count + inserted > plan["history_observation_limit"]:
                    raise ValueError("HISTORY_OBSERVATION_STORAGE_BUDGET_EXHAUSTED")
                url = raw.get("raw_product_url", raw.get("product_url_raw"))
                normalized = normalize_product_url(url, base_url=page.get("page_url") or "https://www.amazon.es")
                valid = normalized.get("is_valid", bool(normalized.get("normalized_product_url")))
                cid, status = None, "INVALID_PRODUCT_LINK"
                if valid:
                    key = normalized["normalized_product_url"]
                    cid = candidate_id(key)
                    self.db.execute("INSERT OR IGNORE INTO candidates VALUES(?,?,?)",
                        (cid, key, normalized.get("possible_duplicate_key")))
                    prior = self.db.execute("SELECT 1 FROM run_candidates WHERE run_id=? AND candidate_id=?", (run_id, cid)).fetchone()
                    if prior:
                        exact = self.db.execute("SELECT 1 FROM observations WHERE run_id=? AND candidate_id=? AND raw_url=? LIMIT 1",
                            (run_id, cid, str(url or ""))).fetchone()
                        status = "EXACT_DUPLICATE" if exact else "NORMALIZED_DUPLICATE"
                    else:
                        status = "UNIQUE_LINK_CANDIDATE"
                    observed = raw.get("observed_at", page.get("observed_at", timestamp()["observed_at"]))
                    if source_eligible:
                        self.db.execute("INSERT INTO run_candidates VALUES(?,?,?,?,?,1) ON CONFLICT(run_id,candidate_id) DO UPDATE SET "
                            "last_seen=max(last_seen,excluded.last_seen),observation_count=observation_count+1",
                            (run_id, cid, oid, observed, observed))
                    else:
                        status = "IDENTITY_UNCONFIRMED"
                row_data = {**raw, "observation_id": oid, "run_id": run_id,
                    "origin": raw.get("origin", page.get("origin", plan.get("origin", "OFFLINE_FIXTURE"))),
                    "snapshot_id": raw.get("snapshot_id", plan.get("snapshot_id", run_id)),
                    "source_id": raw.get("source_id", page.get("source_id")),
                    "source_type": raw.get("source_type", page.get("source_type")),
                    "source_url": raw.get("source_url", page.get("page_url")),
                    "research_category": raw.get("research_category", page.get("research_category")),
                    "observed_at": raw.get("observed_at", page.get("observed_at")),
                    "candidate_id": cid, "normalized_product_url": normalized.get("normalized_product_url"),
                    "absolute_product_url": normalized.get("absolute_product_url"),
                    "url_identity_status": "IDENTITY_UNCONFIRMED" if valid else "INVALID_PRODUCT_LINK",
                    "dedup_status": status, "normalization_reason": normalized.get("reason"),
                    "evidence_path": raw.get("evidence_path", page.get("evidence_path")),
                    "raw_sha256": raw_hash, "parent_observation_id": parent_oid}
                row_data["source_evidence_eligible"] = source_eligible
                row_data["source_binding_status"] = "VERIFIED_LIST_SOURCE" if source_eligible else "UNCONFIRMED_LIST_SOURCE"
                self.db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?)",
                    (oid, run_id, page_key, cid, str(url or ""), status, _json(row_data)))
            self.db.execute("INSERT INTO pages VALUES(?,?,?,?) ON CONFLICT(run_id,page_key) DO UPDATE SET "
                "journal_path=excluded.journal_path,data_json=excluded.data_json", (run_id, page_key, str(journal_path), _json(page)))

    def recover(self, run_id):
        plan = self.get_run(run_id)["plan"]
        directory = self._directory(run_id)
        for path in (directory / "requests").glob("*.reserved.json"):
            value = read_sealed(path)
            self.db.execute("INSERT OR IGNORE INTO requests VALUES(?,?,?,?,?,?,?)",
                (value["request_id"], run_id, value["kind"], value["local_day"], value["observed_at"], "RESERVED", _json(value)))
        for path in (directory / "responses").glob("*.json"):
            value = read_sealed(path)
            data = Path(value["html_path"]).read_bytes()
            if digest(data) != value["sha256"]:
                raise ValueError("HIGH_SPEED_RESPONSE_EVIDENCE_CORRUPT")
            with self._transaction():
                self.db.execute("INSERT OR IGNORE INTO responses VALUES(?,?,?)", (value["request_id"], run_id, _json(value)))
                self.db.execute("UPDATE requests SET status=? WHERE request_id=?", (value["access_state"], value["request_id"]))
        for path in sorted((directory / "pages").glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)):
            value = read_sealed(path)
            page = value["page"]
            if page.get("evidence_path") and page.get("evidence_sha256"):
                if digest(Path(page["evidence_path"]).read_bytes()) != page["evidence_sha256"]:
                    raise ValueError("HIGH_SPEED_PAGE_EVIDENCE_CORRUPT")
            self._index_page(value, path, plan)
            self.db.execute("DELETE FROM pending_pages WHERE run_id=? AND journal_path=?", (run_id,str(path)))
        for path in (directory / "reviews").glob("*.json"):
            value = read_sealed(path)
            self.db.execute("INSERT OR IGNORE INTO destination_reviews VALUES(?,?,?)",
                (value["review"]["review_id"], run_id, _json(value)))

    def apply_review(self, run_id, review):
        """Validate supplied saved proof offline; add an immutable derived view."""
        from .reviews import validate_destination_review
        run = self.get_run(run_id)
        if run.get("origin") not in {"LIVE_CAPTURE", "HISTORICAL_REPLAY"}:
            raise ValueError("IDENTITY_REVIEW_REQUIRES_REAL_CAPTURE_CANDIDATES")
        lookup = {c["candidate_id"]: c for c in self._base_candidates(run_id)}
        projection = validate_destination_review(review, lookup)
        existing = self._review_projection(run_id)
        if set(existing) & set(projection):
            raise ValueError("IDENTITY_REVIEW_ALREADY_EXISTS_FOR_CANDIDATE")
        for new in projection.values():
            for old in existing.values():
                if new["final_product_url"] == old["final_product_url"] and (
                        new["review_evidence"].get("selection_evidence") != old["review_evidence"].get("selection_evidence")):
                    raise ValueError("IDENTITY_CONFLICT_AT_VERIFIED_DESTINATION")
        value = {"run_id":run_id,"review":review,"projection":projection}
        path = self._directory(run_id)/"reviews"/(digest(review["review_id"])[:24]+".json")
        immutable_json(path,sealed(value))
        self.db.execute("INSERT INTO destination_reviews VALUES(?,?,?)",(review["review_id"],run_id,_json(value)))
        return {"review_id":review["review_id"],"review_path":str(path),"projection":projection}

    def _review_projection(self, run_id):
        from .reviews import validate_destination_review
        rows = list(self.db.execute("SELECT data_json FROM destination_reviews WHERE run_id=?",(run_id,)))
        if not rows:
            return {}
        candidates={c["candidate_id"]:c for c in self._base_candidates(run_id)}
        result={}
        for row in rows:
            indexed=json.loads(row[0])
            path=self._directory(run_id)/"reviews"/(digest(indexed["review"]["review_id"])[:24]+".json")
            value=read_sealed(path)
            if value != indexed or value["run_id"] != run_id:
                raise ValueError("IDENTITY_REVIEW_JOURNAL_CONFLICT")
            projection=validate_destination_review(value["review"],candidates)
            if projection != value["projection"] or set(result)&set(projection):
                raise ValueError("IDENTITY_REVIEW_PROJECTION_CONFLICT")
            for cid, facts in projection.items():
                facts={**facts,"review_journal_path":str(path),"review_journal_sha256":digest(path.read_bytes())}
                if facts["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE":
                    facts["independent_purchase_evidence"]={"reviewed":True,"review_actor":facts["reviewed_by"],
                        "observed_at":facts["reviewed_at"],"independent_purchase_confirmed":True,
                        "evidence_path":str(path),"sha256":digest(path.read_bytes())}
                result[cid]=facts
        return result

    def pages(self, run_id):
        return [{**json.loads(row["data_json"]), "page_key": row["page_key"], "journal_path": row["journal_path"]}
            for row in self.db.execute("SELECT * FROM pages WHERE run_id=?", (run_id,))]

    def iter_observations(self, run_id):
        views = {c["candidate_id"]:c for c in self.iter_candidates(run_id)}
        pages={p["page_key"]:p for p in self.pages(run_id)}
        for row in self.db.execute("SELECT data_json,page_key FROM observations WHERE run_id=? ORDER BY rowid", (run_id,)):
            value=json.loads(row[0]); view=views.get(value.get("candidate_id"))
            if "source_evidence_eligible" not in value:
                page=pages.get(row[1],{})
                value["source_evidence_eligible"]=(page.get("access_status") == "NORMAL" and
                    page.get("source_type_verified") is True and page.get("source_context_valid") is True)
            if view:
                value.setdefault("original_possible_duplicate_key", value.get("possible_duplicate_key"))
                value.setdefault("original_normalizer_version", value.get("normalizer_version"))
                value.update({k:view[k] for k in ("possible_duplicate_key","target_group_key","target_eligible",
                    "url_identity_status","final_product_url", "current_normalizer_version",
                    "possible_duplicate_key_version")})
                if view["url_identity_status"] == "VERIFIED_SAME_DESTINATION":
                    value["original_dedup_status"]=value["dedup_status"]
                    value["dedup_status"]="VERIFIED_SAME_DESTINATION"
            if value["source_evidence_eligible"] is False:
                value["target_eligible"]=False
            yield value

    def _base_candidates(self, run_id):
        groups = {}
        for row in self.db.execute("SELECT c.candidate_id,c.url FROM candidates c JOIN run_candidates r USING(candidate_id) WHERE r.run_id=?", (run_id,)):
            key = _current_review_key(row["url"], NORMALIZER_VERSION)
            representative, members = groups.get(key, (row["candidate_id"], 0))
            groups[key] = (min(representative, row["candidate_id"]), members + 1)
        for row in self.db.execute("SELECT c.*,r.*,o.data_json FROM candidates c JOIN run_candidates r USING(candidate_id) "
                "JOIN observations o ON o.observation_id=r.first_observation_id WHERE r.run_id=? ORDER BY r.rowid", (run_id,)):
            first = json.loads(row["data_json"])
            current_key = _current_review_key(row["url"], NORMALIZER_VERSION)
            representative, members = groups[current_key]
            yield {**first, "candidate_id": row["candidate_id"], "normalized_product_url": row["url"],
                "first_seen": row["first_seen"], "first_observed_at": row["first_seen"], "last_seen": row["last_seen"],
                "observation_count": row["observation_count"], "possible_duplicate_key": current_key,
                "original_possible_duplicate_key": row["possible_key"],
                "original_normalizer_version": first.get("normalizer_version"),
                "current_normalizer_version": NORMALIZER_VERSION,
                "possible_duplicate_key_version": NORMALIZER_VERSION,
                "dedup_status": "POSSIBLE_DUPLICATE" if members > 1 else "UNIQUE_LINK_CANDIDATE",
                "target_group_key": current_key,
                "target_eligible": row["candidate_id"] == representative,
                "url_identity_status": "IDENTITY_UNCONFIRMED", "strict_buyable_confirmed": False,
                "final_product_url": None}

    def iter_candidates(self, run_id):
        values=list(self._base_candidates(run_id))
        reviews=self._review_projection(run_id)
        if not reviews:
            yield from values
            return
        parent={c["target_group_key"]:c["target_group_key"] for c in values}
        by_url={c["normalized_product_url"]:c for c in values}
        def find(key):
            parent.setdefault(key,key)
            if parent[key] != key: parent[key]=find(parent[key])
            return parent[key]
        for c in values:
            proof=reviews.get(c["candidate_id"])
            if proof and proof["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE":
                direct=by_url.get(proof["final_product_url"])
                if direct:
                    parent[find(direct["target_group_key"])]=find(c["target_group_key"])
            if proof and proof["url_identity_status"] == "VERIFIED_SAME_DESTINATION":
                key="verified_destination:"+proof["verified_destination_key"]
                parent[find(c["target_group_key"])]=find(key)
                direct=by_url.get(proof["final_product_url"])
                if direct:
                    parent[find(direct["target_group_key"])]=find(key)
        independent={}
        for c in values:
            proof=reviews.get(c["candidate_id"])
            if proof and proof["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE":
                independent.setdefault(find(c["target_group_key"]),[]).append(c["candidate_id"])
        groups={}
        for c in values:
            cid=c["candidate_id"]; base=find(c["target_group_key"]); proof=reviews.get(cid)
            c.update(proof or {})
            key=cid if proof and proof["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE" else (
                min(independent[base]) if base in independent else base)
            c["target_group_key"]=key
            groups.setdefault(key,[]).append(c)
        for members in groups.values():
            verified=[c["candidate_id"] for c in members if c["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE"]
            representative=min(verified or [c["candidate_id"] for c in members])
            for c in members:
                c["target_eligible"]=c["candidate_id"] == representative
                c["strict_buyable_confirmed"]=c["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE"
        yield from values

    def candidate_count(self, run_id):
        # Do not meet 30k with query/fragment or route aliases of one possible
        # product. IDs and URLs stay separate; only the target count is a
        # conservative lower bound, not an automatic product merge.
        if self.db.execute("SELECT 1 FROM destination_reviews WHERE run_id=? LIMIT 1",(run_id,)).fetchone():
            return sum(c["target_eligible"] for c in self.iter_candidates(run_id))
        return len({_current_review_key(row[0], NORMALIZER_VERSION)
            for row in self.db.execute("SELECT c.url FROM candidates c JOIN run_candidates r USING(candidate_id) WHERE r.run_id=?", (run_id,))})

    def normalized_candidate_count(self, run_id):
        return self.db.execute("SELECT count(*) FROM run_candidates WHERE run_id=?", (run_id,)).fetchone()[0]

    def summary(self, run_id):
        requests = self.db.execute("SELECT count(*) FROM requests WHERE run_id=?", (run_id,)).fetchone()[0]
        days = dict(self.db.execute("SELECT local_day,count(*) FROM requests GROUP BY local_day").fetchall())
        run_days = dict(self.db.execute("SELECT local_day,count(*) FROM requests WHERE run_id=? GROUP BY local_day", (run_id,)).fetchall())
        page_requests = self.db.execute("SELECT count(*) FROM requests WHERE run_id=? AND kind='PAGE'", (run_id,)).fetchone()[0]
        counts = dict(self.db.execute("SELECT dedup_status,count(*) FROM observations WHERE run_id=? GROUP BY dedup_status", (run_id,)).fetchall())
        raw = sum(counts.values())
        unique = self.candidate_count(run_id)
        possible_groups = {}
        for row in self.db.execute("SELECT c.url FROM candidates c JOIN run_candidates r USING(candidate_id) WHERE r.run_id=?", (run_id,)):
            key = _current_review_key(row[0], NORMALIZER_VERSION)
            possible_groups[key] = possible_groups.get(key, 0) + 1
        possible = sum(members for members in possible_groups.values() if members > 1)
        reviewed=list(self.iter_candidates(run_id)) if self.db.execute("SELECT 1 FROM destination_reviews WHERE run_id=? LIMIT 1",(run_id,)).fetchone() else []
        pending=[{"journal_path":r["journal_path"],"raw_observations":r["raw_count"]} for r in
            self.db.execute("SELECT * FROM pending_pages WHERE run_id=?",(run_id,))]
        return {"requests": requests, "request_count": requests, "requests_by_day": days,
            "run_requests_by_day":run_days,"daily_budget_scope":"ALL_RUNS_IN_ISOLATED_V2_STORE",
            "pending_page_journals":pending,"unindexed_preserved_raw_observations":sum(r["raw_observations"] for r in pending),
            "page_requests": page_requests, "page_count": len(self.pages(run_id)),
            "observations": raw, "raw_count": raw, "raw_observation_count": raw,
            "history_observations": self.db.execute("SELECT count(*) FROM observations").fetchone()[0],
            "valid_link_count": raw - counts.get("INVALID_PRODUCT_LINK", 0),
            "unique_candidate_count": unique, "unique_links": unique,
            "normalized_unique_url_count": self.normalized_candidate_count(run_id),
            "exact_duplicates": counts.get("EXACT_DUPLICATE", 0), "normalized_duplicates": counts.get("NORMALIZED_DUPLICATE", 0),
            "invalid_links": counts.get("INVALID_PRODUCT_LINK", 0), "identity_unconfirmed": unique,
            "possible_duplicates": possible, "strict_buyable_confirmed": sum(c.get("strict_buyable_confirmed",False) for c in reviewed),
            "verified_same_destination": sum(c["url_identity_status"] == "VERIFIED_SAME_DESTINATION" for c in reviewed), "dedup_counts": counts}

    def append_event(self, run_id, event):
        value = {**event, "run_id": run_id, "recorded_at": timestamp()["observed_at"]}
        self.db.execute("INSERT INTO events(run_id,data_json) VALUES(?,?)", (run_id, _json(value)))
        return value

    def events(self, run_id):
        return [json.loads(row[0]) for row in self.db.execute("SELECT data_json FROM events WHERE run_id=? ORDER BY event_id", (run_id,))]

    def load_run(self, run_id):
        run = self.get_run(run_id)
        return {"run": run, "manifest": {**run, **self.summary(run_id)}, "pages": self.pages(run_id),
            "observations": list(self.iter_observations(run_id)), "requests": self.requests(run_id),
            "responses": list(self.iter_responses(run_id)), "candidates": list(self.iter_candidates(run_id)),
            "events": self.events(run_id)}
