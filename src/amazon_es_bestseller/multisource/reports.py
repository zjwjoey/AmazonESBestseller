"""Versioned JSON/text handoffs derived from immutable snapshot evidence."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
import uuid

from . import SCHEMA_VERSION
from .comparison import SnapshotComparator
from .quality import audit_snapshot, coverage_matrix, confirmed
from .sampling import SamplingPlanner
from .storage import canonical_bytes, immutable_bytes, immutable_json, read_sealed, digest
from .sources import normalize_source_url


def _live_attempts(manifest):
    """Count saved, checked transport attempts; a LIVE label is insufficient."""
    if manifest.get("origin") != "LIVE_CAPTURE":
        return 0, []
    source_ids = {source.get("source_id") for source in manifest.get("sources", [])}
    attempts, errors = set(), []
    for proof in manifest.get("live_request_receipts", []):
        try:
            path = Path(proof["path"])
            if digest(path.read_bytes()) != proof.get("sha256"):
                raise ValueError("RECEIPT_HASH_MISMATCH")
            receipt = read_sealed(path)
            request = receipt.get("transport_evidence", {}).get("request", {})
            response = receipt.get("transport_evidence", {}).get("response", {})
            if (receipt.get("origin") != "LIVE_CAPTURE" or receipt.get("kind") not in {"PAGE", "ACP"}
                    or not receipt.get("request_id") or not receipt.get("approval_id")
                    or not receipt.get("source_id") or (source_ids and receipt["source_id"] not in source_ids)
                    or not normalize_source_url(request.get("url") or "")
                    or not isinstance(response.get("body"), str)):
                raise ValueError("RECEIPT_SCOPE_OR_TRANSPORT_INVALID")
            attempts.add(receipt["request_id"])
        except (KeyError, TypeError, ValueError, OSError) as exc:
            errors.append({"code": "LIVE_REQUEST_RECEIPT_INVALID", "severity": "ERROR", "reason": str(exc)})
    declared = manifest.get("known_saved_live_request_attempts")
    if declared is not None and (not isinstance(declared, int) or isinstance(declared, bool) or declared != len(attempts)):
        errors.append({"code": "LIVE_REQUEST_ATTEMPT_COUNT_UNPROVEN", "severity": "ERROR"})
    return len(attempts), errors


class SnapshotReportExporter:
    def build(self, snapshot: dict, *, history=None, prior_product_ids=None,
              comparison=None, test_evidence=None, prior_scope=None) -> dict:
        manifest = snapshot.get("manifest", {})
        rows = list(snapshot.get("observations", []))
        pages = list(snapshot.get("pages", []))
        quality = audit_snapshot(manifest, pages, rows)
        coverage = coverage_matrix(manifest.get("source_coverage", manifest.get("sources")))
        sampling = SamplingPlanner().feasibility(rows, coverage, prior_product_ids)
        sampling["candidate_scope"] = "CURRENT_SNAPSHOT"
        sampling["prior_scope"] = prior_scope or ("SUPPLIED_CONFIRMED_ENTITY_SET" if prior_product_ids is not None else "UNKNOWN_NO_PRIOR_SET")
        snapshots = list(history) if history is not None else [snapshot]
        pooled, excluded_pools = {}, []
        for item in snapshots:
            origin = item.get("manifest", {}).get("origin")
            item_qa = audit_snapshot(item.get("manifest", {}), item.get("pages", []), item.get("observations", []))
            if item_qa["status"] != "COMPLETE" or not item_qa["gate_passed"]:
                excluded_pools.append({"snapshot_id": item.get("manifest", {}).get("snapshot_id"),
                                       "origin": origin, "reason": "SNAPSHOT_QA_NOT_COMPLETE"})
                continue
            group = pooled.setdefault(origin, {"observations": {}, "coverage": [], "snapshot_ids": set()})
            group["snapshot_ids"].add(item.get("manifest", {}).get("snapshot_id"))
            group["coverage"].extend(item_qa["source_coverage"])
            for row in item.get("observations", []):
                identifier = row["observation_id"]
                existing = group["observations"].get(identifier)
                if existing and (existing.get("product_id"), existing.get("source_type"), existing.get("research_category")) != (row.get("product_id"), row.get("source_type"), row.get("research_category")):
                    # Ambiguous parse revisions of one raw slot cannot create
                    # multiple confident entities in the pooled estimate.
                    group["observations"][identifier] = {**row, "product_id": None, "identity_status": "CONFLICT",
                        "identity_conflict_reason": ["HISTORICAL_OBSERVATION_MAPPING_CONFLICT"]}
                elif identifier not in group["observations"]:
                    group["observations"][identifier] = row
        pooled_reports = {}
        for origin, group in pooled.items():
            prior = prior_product_ids if origin == manifest.get("origin") else None
            result = SamplingPlanner().feasibility(group["observations"].values(), group["coverage"], prior)
            result.update(candidate_scope="VALIDATED_HISTORY_WITHIN_ONE_ORIGIN", origin=origin,
                          snapshot_ids=sorted(group["snapshot_ids"]), observation_count=len(group["observations"]),
                          prior_scope=sampling["prior_scope"] if prior is not None else "UNKNOWN_NO_PRIOR_SET")
            pooled_reports[origin] = result
        quota_pool = {"schema_version": SCHEMA_VERSION, "current_origin": manifest.get("origin"),
                      "candidate_scope": "VALIDATED_HISTORY_SEPARATED_BY_ORIGIN", "origins": pooled_reports,
                      "excluded_snapshots": excluded_pools,
                      "notes": ["Each origin is a separate candidate pool; fixture/replay/live counts are never summed.",
                                "Only complete QA snapshots are pooled; confirmed internal entities remain globally unique within each pool.",
                                "Prior IDs refer to the explicitly supplied scope, not an implicitly imported production catalogue."]}
        comparator = SnapshotComparator()
        histories = comparator.history(snapshots)
        conflicts = [row for row in rows if row.get("identity_conflict_reason") or
                     row.get("identity_status") in {"CONFLICT", "IDENTITY_CONFLICT", "QUARANTINED"}]
        unresolved = [row for row in rows if not confirmed(row)]
        exceptions = [{"kind": "QUALITY", **finding} for finding in quality["findings"]]
        attempted_requests, attempt_errors = _live_attempts(manifest)
        exceptions.extend({"kind": "PROVENANCE", **error} for error in attempt_errors)
        if attempt_errors:
            quality["findings"].extend(attempt_errors)
            quality.update(status="FAILED", gate_passed=False, gate="FAIL")
            quality["gate_block_reasons"] = sorted(set(quality["gate_block_reasons"]) | {error["code"] for error in attempt_errors})
        normal_pages = sum(page.get("origin") == "LIVE_CAPTURE" and page.get("access_status") == "NORMAL"
                           and page_qa["live_provenance_verified"] for page, page_qa in zip(pages, quality["pages"]))
        # Earlier snapshots without the new receipt inventory still retain
        # independently checked page capture receipts in their page records.
        if not manifest.get("live_request_receipts") and manifest.get("known_saved_live_request_attempts") is None:
            attempted_requests = quality["counts"]["proven_live_page_snapshots"]
        exceptions.extend({"kind": "IDENTITY", "observation_id": row.get("observation_id"),
                           "identity_status": row.get("identity_status"),
                           "identity_conflict_reason": row.get("identity_conflict_reason", []),
                           "evidence_path": row.get("evidence_path"),
                           "raw_product_url": row.get("raw_product_url", row.get("product_url_raw")),
                           "review_status": row.get("review_status", "PENDING")}
                          for row in unresolved)
        origins = histories.get("origins", {})
        live_days = origins.get("LIVE_CAPTURE", {}).get("real_live_days", 0)
        live_snapshots = [item for item in snapshots if item.get("manifest", {}).get("origin") == "LIVE_CAPTURE"
                          and item.get("manifest", {}).get("request_count", 0) > 0
                          and audit_snapshot(item["manifest"], item.get("pages", []), item.get("observations", []))["gate_passed"]]
        live_dates = sorted({item["manifest"].get("local_date") for item in live_snapshots
                             if item["manifest"].get("local_date")})
        completed_series = False
        for index in range(max(0, len(live_dates) - 13)):
            dates = live_dates[index:index + 14]
            if date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0]) != timedelta(days=13):
                continue
            comparison_gate = comparator.compare_periods(live_snapshots, dates[0], dates[6], dates[7], dates[13])
            if comparison_gate["status"] == "COMPARABLE":
                completed_series = True
                break
        return {"schema_version": SCHEMA_VERSION, "report_id": "report_" + uuid.uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "manifest": manifest, "origin": manifest.get("origin"),
                "quality_report": quality, "source_coverage": coverage,
                "sampling_report": sampling, "quota_feasibility": sampling,
                "historical_candidate_pool_feasibility": quota_pool,
                "comparison_report": comparison if comparison is not None else histories,
                "history_report": histories, "identity_conflicts": conflicts,
                "identity_links": [{key: row.get(key) for key in (
                    "observation_id", "product_id", "identity_status", "identity_conflict_reason",
                    "review_status", "observed_at", "asin_observed", "parent_asin_observed")}
                                   for row in rows],
                "exceptions": exceptions, "test_evidence": test_evidence,
                "real_amazon_live_test": {"executed": manifest.get("origin") == "LIVE_CAPTURE"
                    and attempted_requests > 0,
                    "attempted_requests": attempted_requests, "verified_normal_pages": normal_pages,
                    "execution_definition": "CHECKED_SAVED_AUTHORIZED_TRANSPORT_ATTEMPT_NOT_COLLECTION_SUCCESS",
                    "real_observation_days": live_days, "fourteen_day_series_completed": completed_series,
                    "note": "Offline fixture/replay success is not a real Amazon collection result."},
                "notes": ["Production ASIN tables, translation caches and Excel contracts are unchanged.",
                          "Unresolved identities are excluded from confirmed unique candidates and formal quotas.",
                          "Visible list positions and rank changes are not market sales or sales growth.",
                          "Every export is a new immutable report revision; raw snapshots are never overwritten."]}

    generate = build

    @staticmethod
    def text(report: dict) -> str:
        qa, quota = report["quality_report"], report["quota_feasibility"]
        counts = qa["counts"]
        lines = ["多榜单快照核查报告", "报告编号：" + report["report_id"],
                 "运行：" + str(report["manifest"].get("run_id")),
                 "证据来源：" + str(report["origin"]),
                 "快照质量：" + qa["status"] + "；阶段门禁：" + qa["gate"],
                 "请求次数：" + str(counts["requests"]),
                 "页面快照：" + str(counts["page_snapshots"]),
                 "榜单观察：" + str(counts["observations"]),
                 "已确认唯一商品：" + str(counts["confirmed_unique_products"]),
                 "身份冲突观察：" + str(counts["identity_conflicts"]),
                 "身份待确认观察：" + str(counts["unconfirmed_observations"]),
                 "当前快照5,500唯一商品池已可分配：" + str(quota["allocated_unique_products"]),
                 "当前快照5,500配额真实缺口：" + str(quota["global_shortfall"]),
                 "历史候选池按证据来源分别统计，见 quota_pool_feasibility.json。",
                 "已核验的联网请求尝试：" + str(report["real_amazon_live_test"]["attempted_requests"]),
                 "已核验的正常响应页：" + str(report["real_amazon_live_test"]["verified_normal_pages"]),
                 "75逻辑组合状态：" + str(qa["coverage_status_counts"]),
                 "实际联网观察日期数：" + str(report["real_amazon_live_test"]["real_observation_days"]),
                 "离线测试和历史回放不能当作新增联网采集；榜单名次变化不能解释为销量增长。", "", "异常："]
        lines.extend(str(item.get("code", item.get("identity_status"))) + " " +
                     str(item.get("observation_id", item.get("page_url", "")))
                     for item in report["exceptions"])
        return "\n".join(lines) + "\n"

    def export(self, report: dict, destination: str | Path) -> dict:
        """Publish once to a fresh report-ID directory; refuse conflicting bytes."""
        if not isinstance(report.get("report_id"), str) or not re.fullmatch(r"report_[0-9a-f]{32}", report["report_id"]):
            raise ValueError("INVALID_REPORT_ID")
        directory = Path(destination) / report["report_id"]
        for filename, key in (("report.json", None), ("source_coverage.json", "source_coverage"),
                              ("quality_report.json", "quality_report"),
                              ("sampling_report.json", "sampling_report"),
                              ("quota_feasibility.json", "quota_feasibility"),
                              ("quota_pool_feasibility.json", "historical_candidate_pool_feasibility"),
                              ("comparison_report.json", "comparison_report"),
                              ("history_report.json", "history_report"),
                              ("identity_conflicts.json", "identity_conflicts"),
                              ("exceptions.json", "exceptions")):
            immutable_json(directory / filename, report if key is None else report[key])
        immutable_bytes(directory / "identity_links.jsonl",
                        b"".join(canonical_bytes(row) + b"\n" for row in report["identity_links"]))
        immutable_bytes(directory / "report.txt", self.text(report).encode("utf-8"))
        return {"directory": str(directory), "report_id": report["report_id"],
                "json": str(directory / "report.json"), "text": str(directory / "report.txt"),
                "exceptions": str(directory / "exceptions.json")}

    def export_exceptions(self, snapshot: dict, destination: str | Path) -> dict:
        return self.export(self.build(snapshot), destination)
