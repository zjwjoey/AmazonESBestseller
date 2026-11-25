"""Versioned, idempotent result ledger shared by snapshot and detail audits."""
from __future__ import annotations

import json

from .evidence_store import EvidenceStore, sha256
from .task_workspace import utc_now


def canonical_json(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class AuditLedger:
    def __init__(self, workspace):
        self.workspace = workspace
        self.evidence = EvidenceStore(workspace)
        with workspace.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS audit_attempts (
                audit_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage TEXT NOT NULL,
                unit_id TEXT NOT NULL, evidence_digest TEXT, record_digest TEXT NOT NULL,
                parser_version TEXT NOT NULL, qa_version TEXT NOT NULL,
                checked_at TEXT NOT NULL, status TEXT NOT NULL, fields TEXT NOT NULL,
                anomalies TEXT NOT NULL, batch_id TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS audit_current (
                stage TEXT NOT NULL, unit_id TEXT NOT NULL, audit_id TEXT NOT NULL,
                PRIMARY KEY(stage,unit_id))""")
            conn.execute("CREATE INDEX IF NOT EXISTS audits_stage_batch ON audit_attempts(stage,batch_id)")

    def record(self, *, stage, unit_id, digests, record, status, fields, anomalies,
               parser_version=None, qa_version=None, batch_id=None):
        parser = parser_version or self.workspace.manifest["parser_version"]
        qa = qa_version or self.workspace.manifest["audit_contract_version"]
        task = self.workspace.manifest["task_id"]
        digests = tuple(dict.fromkeys(d for d in digests if d))
        record_digest = sha256(canonical_json(record).encode("utf-8"))
        audit_id = sha256(canonical_json([task, stage, unit_id, digests, record_digest, parser, qa]).encode("utf-8"))
        with self.workspace.transaction() as conn:
            for digest in digests:
                if not conn.execute("SELECT 1 FROM evidence WHERE digest=?", (digest,)).fetchone():
                    raise ValueError("AUDIT_EVIDENCE_NOT_REGISTERED:" + digest)
            prior = conn.execute("SELECT status,fields,anomalies FROM audit_attempts WHERE audit_id=?",
                                 (audit_id,)).fetchone()
            result = (status, canonical_json(fields), canonical_json(anomalies))
            if prior and tuple(prior) != result:
                raise ValueError("AUDIT_RESULT_CHANGED_WITHOUT_VERSION_CHANGE:" + audit_id)
            conn.execute("""INSERT OR IGNORE INTO audit_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (audit_id, task, stage, unit_id, digests[0] if digests else None, record_digest,
                          parser, qa, utc_now(), *result, batch_id))
            conn.execute("""INSERT INTO audit_current VALUES(?,?,?)
                            ON CONFLICT(stage,unit_id) DO UPDATE SET audit_id=excluded.audit_id""",
                         (stage, unit_id, audit_id))
            conn.executemany("INSERT OR IGNORE INTO evidence_audits VALUES(?,?,?,?,?,?,?)",
                             [(digest, stage, unit_id, audit_id, status, parser, qa) for digest in digests])
        return audit_id

    def lookup(self, stage, unit_id, record):
        digest = sha256(canonical_json(record).encode("utf-8"))
        with self.workspace.transaction() as conn:
            row = conn.execute("""SELECT status FROM audit_current c JOIN audit_attempts a USING(audit_id)
                                  WHERE c.stage=? AND c.unit_id=? AND a.record_digest=?
                                  AND a.parser_version=? AND a.qa_version=?""",
                               (stage, unit_id, digest, self.workspace.manifest["parser_version"],
                                self.workspace.manifest["audit_contract_version"])).fetchone()
        return row["status"] if row else None

    def current(self, stage, *, pending_limit=None):
        query = """SELECT a.* FROM audit_current c JOIN audit_attempts a USING(audit_id)
                   WHERE c.stage=?"""
        order = " ORDER BY a.checked_at,a.unit_id"
        params = (stage,)
        if pending_limit is not None:
            query += " AND a.batch_id IS NULL AND a.status!='ACCESS_STOP'"
            order += " LIMIT ?"
            params += (pending_limit,)
        with self.workspace.transaction() as conn:
            rows = conn.execute(query + order, params).fetchall()
        return [{**row, "fields": json.loads(row["fields"]), "anomalies": json.loads(row["anomalies"])}
                for row in rows]

    def assign_batch(self, ids, batch_id):
        with self.workspace.transaction() as conn:
            for audit_id in ids:
                changed = conn.execute("""UPDATE audit_attempts SET batch_id=?
                                          WHERE audit_id=? AND (batch_id IS NULL OR batch_id=?)""",
                                       (batch_id, audit_id, batch_id)).rowcount
                if not changed:
                    raise ValueError("AUDIT_BATCH_CHANGED:" + audit_id)
