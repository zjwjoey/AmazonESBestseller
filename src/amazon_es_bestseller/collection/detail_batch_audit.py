"""Detail boundary between durable per-item evidence and batch publication."""
from __future__ import annotations

import json
from collections import Counter
from time import perf_counter

from ..audit_ledger import AuditLedger, canonical_json
from ..evidence_store import read_evidence, sha256
from ..task_workspace import atomic_json
from .field_completeness import FieldCompletenessMonitor, check_detail_fields


def checkpoint_payload(checkpoint):
    return {key: checkpoint[key] for key in (
        "status", "record", "source", "detail_status", "capture_meta", "html_evidence", "attempt_count",
    ) if key in checkpoint}


class DetailQualityPause(ValueError):
    pass


class DetailBatchAudit:
    def __init__(self, workspace, publish):
        self.workspace = workspace
        self.publish = publish
        self.ledger = AuditLedger(workspace)
        self.monitor = FieldCompletenessMonitor(workspace.manifest["field_quality_thresholds"])
        self.batch_size = workspace.manifest["audit_batch_size"]
        self.directory = workspace.root / "audit"
        with workspace.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS detail_batches (
                batch_id TEXT PRIMARY KEY, ids TEXT NOT NULL, status TEXT NOT NULL)""")

    def accept(self, asin, checkpoint, *, flush=True, evidence_bytes=None):
        """Called immediately after the authoritative checkpoint is durable."""
        payload = checkpoint_payload(checkpoint)
        previous = self.ledger.lookup("detail", asin, payload)
        if previous:
            if flush:
                self.flush(partial=previous == "ACCESS_STOP", enforce_pause=previous != "ACCESS_STOP")
            return previous
        meta = checkpoint.get("capture_meta") or {}
        ref = checkpoint.get("html_evidence") or {}
        source = ref.get("sha256")
        included = [source] if source else []
        included.extend(checkpoint.get("related_evidence_sha256", ()))
        if source and evidence_bytes is not None:
            if sha256(evidence_bytes) != source:
                raise ValueError("DETAIL_REPLAY_BUFFER_SHA256_MISMATCH:" + asin)
            html = evidence_bytes.decode("utf-8")
        else:
            html = read_evidence(ref["path"], source).decode("utf-8") if source else None
        record = checkpoint.get("record")
        valid, fields, issues = check_detail_fields(asin, record, checkpoint, html, meta)
        if not source and checkpoint.get("status") == "success":
            issues.append({"field": "asin", "code": "DETAIL_HTML_EVIDENCE_MISSING"})
        if not valid and checkpoint.get("status") == "success":
            status = "QUARANTINED"
        elif checkpoint.get("status") in {"access_stop", "challenge_cached"}:
            status = "ACCESS_STOP"
        elif valid:
            status = "WARN" if issues else "PASS"
        else:
            status = "TERMINAL_FAILURE"
        self.ledger.record(stage="detail", unit_id=asin, digests=included, record=payload,
                           status=status, fields=fields, anomalies=issues)
        if flush:
            self.flush(partial=status == "ACCESS_STOP", enforce_pause=status != "ACCESS_STOP")
        return status

    def _pending(self):
        return self.ledger.current("detail", pending_limit=self.batch_size, current_versions=True)

    def flush(self, *, partial=False, enforce_pause=True):
        while True:
            pending = self._pending()
            if not pending or (len(pending) < self.batch_size and not partial):
                break
            started = perf_counter()
            rows = pending[:self.batch_size]
            ids = [row["audit_id"] for row in rows]
            batch_id = sha256(canonical_json(ids).encode("utf-8"))[:24]
            with self.workspace.transaction() as conn:
                conn.execute("INSERT OR IGNORE INTO detail_batches VALUES(?,?,'PENDING')",
                             (batch_id, canonical_json(ids)))
            # Deterministic retry after a crash. Publication must be idempotent
            # and use the exact validated checkpoint version for each audit.
            self.publish(rows)
            batch = self.monitor.summarize(rows)
            current = [row for row in self.ledger.current("detail", current_versions=True) if row["status"] != "ACCESS_STOP"]
            cumulative = self.monitor.summarize(current)
            signals = self.monitor.signals(batch, cumulative)
            cumulative_started = perf_counter()
            self._write_cumulative(current, cumulative, signals, batch_id)
            cumulative_seconds = perf_counter() - cumulative_started
            atomic_json(self.directory / "batches" / (batch_id + ".json"), {
                "batch_id": batch_id, "asins": [row["unit_id"] for row in rows],
                "quality_signals": signals, "fields": batch,
                "cumulative_publication_seconds": cumulative_seconds,
                "publication_seconds": perf_counter() - started,
            })
            # An audit record was committed before any of these original files
            # may be transformed into verified, retrievable archives.
            with self.workspace.transaction() as conn:
                evidence = conn.execute(
                    "SELECT DISTINCT digest FROM evidence_audits WHERE audit_id IN ("
                    + ",".join("?" for _ in ids) + ")", ids).fetchall()
            for digest, in evidence:
                self.ledger.evidence.archive(digest)
            with self.workspace.transaction() as conn:
                conn.executemany("UPDATE audit_attempts SET batch_id=? WHERE audit_id=?",
                                 [(batch_id, audit_id) for audit_id in ids])
                conn.execute("UPDATE detail_batches SET status='COMMITTED' WHERE batch_id=?", (batch_id,))
            if enforce_pause and any(signal["level"] == "PAUSE" for signal in signals):
                self.workspace.bind(task_status="QUALITY_PAUSED")
                raise DetailQualityPause("DETAIL_FIELD_QUALITY_PAUSED")

    def _write_cumulative(self, rows, fields, signals, batch_id):
        counts = Counter(row["status"] for row in rows)
        atomic_json(self.directory / "field_completeness.json", fields)
        atomic_json(self.directory / "cumulative_quality.json",
                    {"detail_status_counts": dict(counts), "latest_batch": batch_id, "signals": signals})
        first_seen = {}
        with self.workspace.transaction() as conn:
            history = conn.execute("""SELECT unit_id,batch_id,anomalies FROM audit_attempts
                WHERE stage='detail' AND batch_id IS NOT NULL AND anomalies<>'[]'
                ORDER BY checked_at,audit_id""").fetchall()
        for historical in history:
            for issue in json.loads(historical["anomalies"]):
                first_seen.setdefault(self._issue_key(historical, issue), historical["batch_id"])
        anomalies = []
        for row in rows:
            published_batch = row["batch_id"] or batch_id
            digest = row["evidence_digest"]
            # The active name also resolves to the verified gzip after
            # archival. It remains stable across interrupted publication.
            location = str(self.ledger.evidence.active / (digest + ".html")) if digest else None
            for issue in row["anomalies"]:
                anomalies.append({
                    "asin": row["unit_id"], "html_sha256": digest, "evidence_path": location,
                    "parser_version": row["parser_version"], "batch_id": published_batch,
                    "first_seen_batch": first_seen.get(self._issue_key(row, issue), published_batch),
                    "status": row["status"], **issue,
                })
        atomic_json(self.directory / "anomaly_asins.json", anomalies)

    @staticmethod
    def _issue_key(row, issue):
        return (row["unit_id"], issue["field"], issue["code"], issue.get("label"))
