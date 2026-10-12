"""Repair the closed 888-SKU batch offline; leave all original evidence intact."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from amazon_es_bestseller.production.bounded_spanish_repair import repair_record  # noqa: E402
from amazon_es_bestseller.orchestration.checkpoint import read_json, write_json_atomic  # noqa: E402
from amazon_es_bestseller.orchestration.link_first import file_hash, immutable_json  # noqa: E402
from amazon_es_bestseller.quality.source_fields import audit_source_fields, source_gate  # noqa: E402


def snapshot(root):
    return {str(p.relative_to(root)): file_hash(p) for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix.lower() in {".html", ".json"}}


def scoped_path(root, relative):
    path = (root / str(relative).replace("\\", "/")).resolve()
    path.relative_to(root.resolve())
    return path


def key(issue):
    evidence = issue.get("evidence") or {}
    return (issue.get("asin"), issue.get("field"), issue.get("issue_code"),
            evidence.get("label"), evidence.get("value"), evidence.get("match_kind"))


def run(origin, output):
    origin, output = origin.resolve(), output.resolve()
    if origin == output or output.is_relative_to(origin):
        raise ValueError("Independent output required")
    if output.exists():
        raise ValueError("New output directory required; never overwrite a prior repair")
    output.mkdir(parents=True)
    hashes = snapshot(origin)
    immutable_json(output / "original_evidence_hashes.json", {"origin":str(origin),"files":hashes})
    rows = read_json(origin / "spanish_candidate.json", None)
    report = read_json(origin / "batch_report.json", None)
    inputs = read_json(origin / "batch_inputs.json", None)
    baseline = read_json(origin / "source_audit.json", None)
    scope = {r["asin"] for r in rows}
    if len(rows) != 888 or len(scope) != 888 or report["link_count"] != 980 or report["status"] != "BOUNDED_DETAIL_COMPLETE":
        raise ValueError("Unexpected scope or incomplete collection")
    state = read_json(origin / "checkpoints/link_first_state.latest.json", None)
    successes = {}
    for binding in state["detail_results"].values():
        path = scoped_path(origin,binding["result_file"])
        if file_hash(path) != binding["sha256"]:
            raise ValueError("RESULT_BINDING_DRIFT")
        result = read_json(path,None)
        if result.get("html_file") and file_hash(scoped_path(origin,result["html_file"])) != result["html_sha256"]:
            raise ValueError("RESULT_HTML_DRIFT")
        if result["status"] == "SUCCESS":
            if any(i["severity"]=="BLOCK" for i in result["audit"]["issues"]):
                raise ValueError("SUCCESS_HAS_BLOCKING_BINDING_FINDING")
            successes[(result["record"]["asin"],result["record"]["html_file"])] = result
    fixed, changes, checks = [], [], []
    for number, row in enumerate(rows,1):
        source = successes.get((row["asin"],row["html_file"]))
        if source is None:
            raise ValueError("CANDIDATE_HAS_NO_SUCCESSFUL_CAPTURE")
        for field in ("attributes","feature_bullets_raw","detail_bullets_raw","title_es_raw","detail_identity"):
            if source["record"].get(field) != row.get(field):
                raise ValueError("CANDIDATE_RAW_BINDING_DRIFT")
        data = scoped_path(origin,row["html_file"]).read_bytes()
        candidate, log = repair_record(row,data)
        fixed.append(candidate)
        changes.extend(log)
        checks.append({"asin":row["asin"],"html_sha256":row["html_sha256"],"binding_status":"PASS",
                       "attribute_count":len(row.get("attributes") or []),"feature_bullet_count":len(row.get("feature_bullets_raw") or []),
                       "raw_fields_unchanged":True,"category_html_verified":True,
                       "collection_audit":source["audit"]["status"]})
        if number % 50 == 0:
            write_json_atomic(output / "repair_progress.json",{"status":"REPAIRING","processed":number,"total":888})
            print("REPAIR",number,flush=True)
    # Gap authority is the full 980-context snapshot, including quarantined
    # identities. Do not manufacture ranking gaps from the successful subset.
    audit = audit_source_fields(fixed, ranking_matrix=inputs["links"],progress=lambda event,**values:print(event,values,flush=True))
    passed, gate = source_gate(audit)
    after_by = {key(i):i for i in audit["issues"]}
    comparisons = []
    for issue in baseline["issues"]:
        status = "STILL_REVIEW_REQUIRED" if key(issue) in after_by else "CONFIRMED_FIXED"
        if issue["issue_code"] in {"CATEGORY_COPIED","RANK_GAP"} and status == "CONFIRMED_FIXED":
            status = "AUDIT_EVIDENCE_SCOPE_CORRECTED"
        comparisons.append({**issue,"resolution_status":status})
    remaining = list(audit["issues"])
    for row in rows:
        if row.get("original_price") is not None and any(c["asin"]==row["asin"] and c["field"]=="original_price" for c in changes):
            remaining.append({"asin":row["asin"],"field":"original_price_raw","issue_code":"SOURCE_PRICE_RELATION_REVIEW",
                              "severity":"P2","message":"Original list price does not exceed current price; raw source retained, canonical original/discount unavailable",
                              "evidence":{"current_price_raw":row.get("current_price_raw"),"original_price_raw":row.get("original_price_raw")}})
    counts = Counter(i["issue_code"] for i in audit["issues"])
    blanks = {field:sum(row.get(field) in (None,"") for row in fixed) for field in (
        "asin","category_l1","category_l2","category_l3","leaf_category","specification_es","product_details_es","feature_bullets_es")}
    if {r["asin"] for r in fixed} != scope or len(fixed)!=888:
        raise ValueError("REPAIR_SCOPE_CHANGED")
    if snapshot(origin) != hashes:
        raise ValueError("ORIGINAL_EVIDENCE_CHANGED")
    summary={"status":"OFFLINE_REPAIR_COMPLETE","observed_at":datetime.now(timezone.utc).isoformat(),
             "sku_count":888,"original_files_unchanged":True,"verified_original_file_count":len(hashes),
             "change_counts":dict(Counter(c["field"] for c in changes)),
             "comparison_counts":dict(Counter(i["resolution_status"] for i in comparisons)),
             "remaining_issue_counts":dict(Counter(i["issue_code"] for i in remaining)),
             "remaining_issue_skus":len({i['asin'] for i in remaining if i.get('asin')}),
             "source_issue_counts":dict(counts),"source_gate":gate,"source_gate_passed":passed,
             "empty_fields":blanks,"formal_release":False,"translation":"disabled","network_requests":0}
    for name,data in (("spanish_candidate_repaired.json",fixed),("repair_log.json",changes),
                      ("binding_verification.json",checks),("source_audit_repaired.json",audit),
                      ("historical_issue_comparison.json",comparisons),("remaining_issues.json",remaining),("repair_report.json",summary)):
        immutable_json(output/name,data)
    with (output / "remaining_issues.csv").open("w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=["asin","field","issue_code","severity","message","evidence"])
        writer.writeheader()
        for issue in remaining:
            writer.writerow({**{k:issue.get(k,"") for k in writer.fieldnames},"evidence":json.dumps(issue.get("evidence",{}),ensure_ascii=False)})
    summary_text="\n".join(["# 888 SKU 西语离线修复审查", "", "保留全部888个ASIN，原始HTML/JSON/Checkpoint均未改变。",
        "", "Source Gate: "+gate,"正式发布: 否；仅供候选研究审阅。", "", "修复统计：",json.dumps(summary["change_counts"],ensure_ascii=False),
        "", "剩余问题统计：",json.dumps(summary["remaining_issue_counts"],ensure_ascii=False),
        "", "源值含混、损坏或冲突继续保留，未删除字段、未猜测替换。原始价格关系异常不用于计算折扣。",
        "类目leaf=L3仅在绑定的真实三级路径时认可。完整980榜单上下文保留，3条身份隔离不加入888个候选。"])
    (output / "repair_report.md").write_text(summary_text,encoding="utf-8")
    write_json_atomic(output / "repair_progress.json",summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    args=parser.parse_args()
    run(args.origin,args.out_dir)
