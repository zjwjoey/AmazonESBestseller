"""Incremental PAGE checks and ledger-only final coverage accounting."""
from __future__ import annotations

from collections import Counter
from time import perf_counter

from ..audit_ledger import AuditLedger, canonical_json
from ..evidence_store import sha256
from .v4_quality import audit_v4


def page_evidence_digests(page):
    derivatives = page.get("rendered_derivation") or {}
    refs = [
        page.get("document_html_reference") or {},
        page.get("rendered_html_reference") or {},
        *page.get("acp_captures", []),
        *derivatives.get("rendered_frames", []),
    ]
    return list(dict.fromkeys(filter(None, [
        page.get("evidence_sha256"), *(ref.get("evidence_sha256") or ref.get("sha256") for ref in refs)
    ])))


class SnapshotPageAudit:
    def __init__(self, workspace):
        self.workspace = workspace
        self.ledger = AuditLedger(workspace)

    def check(self, plan, page, observations, *, require_live=True):
        unit_id = plan["run_id"] + ":" + page["page_key"]
        parser = page.get("parser_version") or self.workspace.manifest["parser_version"]
        version = self.workspace.manifest["audit_contract_version"]
        digests = page_evidence_digests(page)
        # Journals are storage locations, not inputs to this isolated page replay.
        page = {key: value for key, value in page.items() if key != "journal_path"}
        observations = sorted(
            ({key: value for key, value in row.items() if key != "dedup_status"} for row in observations),
            key=lambda row: row["observation_id"])
        record = {"page": page, "observations": observations, "require_live": require_live}
        fingerprint = sha256(canonical_json(record).encode("utf-8"))
        with self.workspace.transaction() as conn:
            last = conn.execute("""SELECT a.status,a.record_digest,a.parser_version,a.qa_version
                                   FROM audit_current c JOIN audit_attempts a USING(audit_id)
                                   WHERE c.stage='snapshot' AND c.unit_id=?""", (unit_id,)).fetchone()
        if last and tuple(last)[1:] == (fingerprint, parser, version):
            self.last_audit_seconds = None
            return last["status"]

        started = perf_counter()
        result = audit_v4([page], observations, plan["sources"],
                          require_live=require_live, include_history=False)
        warnings = Counter()
        for row in observations:
            if not row.get("is_valid"):
                warnings["INVALID_LINK"] += 1
            if row.get("binding_doubts") or not row.get("asin_url_hint"):
                warnings["IDENTITY_REVIEW"] += 1
            if not row.get("product_title_es"):
                warnings["TITLE_MISSING"] += 1
            price = row.get("current_price")
            if price is not None and (not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0):
                warnings["PRICE_INVALID"] += 1
            rank = row.get("ranking_position")
            if rank is not None and (type(rank) is not int or rank < 1):
                warnings["RANK_INVALID"] += 1
        status = "FAIL" if result["errors"] else "WARN" if warnings else "PASS"
        self.ledger.record(
            stage="snapshot", unit_id=unit_id, digests=digests, record=record, status=status,
            fields={"observation_count": len(observations), "field_statuses": result["field_statuses"],
                    "card_warnings": dict(warnings), "source": page["source_type"],

                    "live_origin_required": require_live},
            anomalies=result["errors"], parser_version=parser, qa_version=version)
        for digest in digests:
            self.ledger.evidence.archive(digest)
        self.last_audit_seconds = perf_counter() - started
        return status

    def coverage(self, plan, pages):
        units = {plan["run_id"] + ":" + p["page_key"]: p for p in pages}
        version = self.workspace.manifest["audit_contract_version"]
        current = {a["unit_id"]: a for a in self.ledger.current("snapshot")
                   if a["unit_id"] in units
                   and a["qa_version"] == version
                   and a["parser_version"] == (units[a["unit_id"]].get("parser_version")
                                                or self.workspace.manifest["parser_version"])
                   and a["evidence_digest"] == units[a["unit_id"]].get("evidence_sha256")}
        counts = Counter(row["status"] for row in current.values())
        uncovered = sorted(units.keys() - current.keys())
        errors = [error for row in current.values() for error in row["anomalies"]]
        errors.extend({"code": "PAGE_AUDIT_MISSING", "unit_id": unit} for unit in uncovered)
        return {"central_full_dom_review_passed": bool(units) and not uncovered and not counts["FAIL"],
                "page_statuses": dict(counts), "reviewed_capture_frames": len(current),
                "reviewed_observations": sum(a["fields"]["observation_count"] for a in current.values()),
                "live_origin_required": bool(current) and all(a["fields"].get("live_origin_required")
                                                             for a in current.values()),
                "errors": errors, "review_status": "INCREMENTAL_LEDGER_RECONCILED"}
