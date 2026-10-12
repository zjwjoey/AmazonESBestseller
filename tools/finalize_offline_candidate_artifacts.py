"""Finish only review/export artifacts from durable completed audit checkpoints.

Does not replay, re-audit, change source data, or overwrite existing deliverables.
"""
from __future__ import annotations

import argparse
from collections import Counter

from offline_regression_review import (
    HEAD_ES, HEAD_ZH, WIDTH_ES, WIDTH_ZH, _es_values, canonical, gate_summary, read, write,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    from pathlib import Path
    output = Path(args.output_dir).resolve()
    target = output / "final_review"
    summary = read(target / "summary.json")
    comparisons = read(target / "historical_issue_comparison.json")
    unresolved = read(target / "unresolved.json")
    fidelity = read(target / "sku_processing_results.json")
    register = read(target / "register_summary_comparison.json")
    metadata = read(output / "input_metadata.json")
    records = sorted(read(output / "spanish_candidate/spanish_master_5480.json")["records"], key=lambda r: r["asin"])
    assert len(fidelity) == len(records) == 5480
    assert {r["asin"] for r in fidelity} == {r["asin"] for r in records}
    rows, zh_rows = [], []
    for index, row in enumerate(records, 1):
        values = _es_values(row, index)
        rows.append(values)
        zh = [None] * 26
        for position, value in ((1, index), (2, row["asin"]), (3, row.get("parent_asin")), (5, row.get("brand")),
            (6, values[5]), (7, values[6]), (8, values[7]), (9, values[8]), (10, values[9]), (11, values[10]),
            (16, values[15]), (21, values[20]), (22, values[21]), (23, values[22]), (24, values[23]), (25, values[24])):
            zh[position] = value
        zh_rows.append(zh)
    planning = [["5480 SKU 西语候选 — 非正式版", "值 / 说明"],
        ["处理范围", 5480], ["唯一ASIN", len({r["asin"] for r in records})],
        ["ASIN集合核验", summary["exact_5480_asin_scope"]], ["原始输入哈希未变化", summary["raw_inputs_unchanged"]],
        ["Source Gate", canonical(gate_summary(summary["source_gate"]))],
        ["Closure Gate", canonical(gate_summary(summary["closure_gate"]))], ["交付状态", summary["final_review_status"]],
        ["历史对照数（两层记录，不当作去重错误数）", len(comparisons)],
        ["当前候选问题记录数", summary["current_issue_count"]], ["当前候选问题SKU数", summary["current_affected_skus"]],
        ["完整性阻断SKU数（与候选问题可能重叠）", summary["integrity_unresolved_count"]],
        *[[key, value] for key, value in summary["comparison_resolution_counts"].items()],
        ["完整Gate证据", str(target / "summary.json")],
        ["中文页说明", "冻结26列和5480行身份对照；中文翻译字段未生成。使用西班牙语选品清单审阅。"],
        ["使用限制", "候选可审阅，不代表SOURCE_READY；源数据疑点和缺失不猜测、不删除。"],
        ["单元格长文本", "完整内容保留；Excel原生行高上限可能无法同时展开所有行，可通过编辑栏查看或手动调整。"],
        ["源证据", metadata["inputs"]["details"]], ["冻结候选", metadata["inputs"]["candidate_manifest"]],
        ["历史对照JSON", str(target / "historical_issue_comparison.json")],
        ["未解决完整证据", str(target / "unresolved.json")]]
    planning.extend([[f"登记表实际复核：{k}", canonical(v)] for k, v in summary["register_fact_checks"].items()])
    planning.extend([[f"研究类目：{k}", v] for k, v in sorted(Counter(
        (r.get("metadata") or {}).get("research_category", "UNKNOWN") for r in records).items())])
    comparison_columns = ["historical_id", "layer", "asin", "field", "label", "issue_code", "severity", "resolution", "reason", "current_matching_count"]
    unresolved_columns = ["asin", "origin", "field", "issue_code", "classification", "severity", "status", "message", "reason", "evidence"]
    sheets = [
        {"name": "类目规划", "headers": planning[0], "rows": planning[1:], "widths": [48, 110]},
        {"name": "西班牙语选品清单", "headers": HEAD_ES, "rows": rows, "widths": WIDTH_ES},
        {"name": "中文选品清单", "headers": HEAD_ZH, "rows": zh_rows, "widths": WIDTH_ZH},
        {"name": "历史问题对照", "headers": comparison_columns, "rows": [[r.get(k) for k in comparison_columns] for r in comparisons],
         "widths": [38, 30, 16, 26, 35, 38, 12, 27, 100, 15]},
        {"name": "剩余问题", "headers": unresolved_columns, "rows": [[canonical(r.get(k)) if isinstance(r.get(k), (dict, list)) else r.get(k) for k in unresolved_columns] for r in unresolved],
         "widths": [16, 29, 30, 40, 28, 12, 15, 80, 85, 110]},
        {"name": "SKU核验", "headers": ["ASIN", "处理结果", "HTML哈希", "属性绑定", "卖点绑定", "排名类目绑定", "属性事实丢失", "卖点丢失", "DOM未捕获属性", "卖点DOM顺序", "详情内冲突ASIN"],
         "rows": [[r["asin"], r["result"], r["html_hash_verified"], r["attributes_exact_candidate_binding"],
            r["bullets_exact_candidate_binding"], r["ranking_category_binding"], r["attributes_lost"], r["bullets_lost"],
            len(r["missing_html_attribute_pairs"]), r["dom_bullet_order_binding"], canonical(r["detail_attribute_asin_conflicts"])] for r in fidelity],
         "widths": [16, 40, 18, 18, 18, 20, 20, 18, 24, 20, 35]},
        {"name": "登记表声明对照", "headers": ["历史编号", "来源", "判定", "说明", "原始声明"],
         "rows": [[r["historical_id"], r["section"], r["resolution"], r["reason"], canonical(r["historical_entry"])] for r in register],
         "widths": [46, 30, 28, 110, 130]},
    ]
    oversized = [(s["name"], i + 2, j + 1, len(v)) for s in sheets for i, r in enumerate(s["rows"])
                 for j, v in enumerate(r) if isinstance(v, str) and len(v) > 32767]
    if oversized:
        raise RuntimeError(f"Refusing silent truncation: {oversized[:5]}")
    write(target / "workbook_payload.json", {"sheets": sheets})
    report = ("# 5480 SKU 离线全量回归交付\n\n"
        f"全部5480唯一ASIN已处理：重解析{summary['replayed']}，未验证{summary['replay_unverified']}。\n\n"
        f"原始输入哈希未变化：{summary['raw_inputs_unchanged']}；HTML哈希通过{summary['used_html_hash_verified_count']}/5480；"
        f"属性事实丢失{summary['attribute_facts_lost_count']}；卖点丢失{summary['feature_bullets_lost_count']}。\n\n"
        f"Source Gate：{canonical(gate_summary(summary['source_gate']))}\n\n"
        f"Closure Gate：{canonical(gate_summary(summary['closure_gate']))}\n\n"
        f"完整性Gate：{summary['final_integrity_gate']}。当前候选问题{summary['current_issue_count']}条，影响{summary['current_affected_skus']} SKU。\n\n"
        f"另有{summary['integrity_unresolved_count']}个SKU完整性阻断（详情内ASIN冲突）；与当前候选问题可能重叠，不能相加作去重SKU数。冲突原值及证据保留在剩余问题和SKU核验页。\n\n"
        f"问题类型：{canonical(summary['current_issue_types'])}。\n\n"
        f"历史逐项对照（源审查与r3分别保留，非去重错误数）：{canonical(summary['comparison_by_layer'])}。\n\n"
        f"登记表价格/身份实际复核：{canonical(summary['register_fact_checks'])}。\n\n"
        "不再触发问题不等于事实修复。更早源审查的完整阶段行未能匹配其记录哈希，对应结论明确列为证据不足；r3记录哈希逐行验证。\n\n"
        "候选JSON/CSV保留完整原始属性、详情补充字段、卖点、来源及疑点。没有访问Amazon、没有调用翻译Provider、没有提交Git，没有覆写旧表或冻结manifest，没有缩小为5478。\n\n"
        "中文页只作5480身份对照，翻译字段为空，不是中文研究版。西语候选尚不能宣称正式SOURCE_READY。\n\n"
        "## 文件\n\n"
        f"- 完整候选数据：{summary['candidate_json_path']}\n"
        f"- 字段级历史对照：{target / 'historical_issue_comparison.csv'}\n"
        f"- 全量处理结果：{target / 'sku_processing_results.csv'}\n"
        f"- 剩余问题完整证据：{target / 'unresolved.json'}\n"
        f"- 原始输入与使用证据哈希：{target / 'input_and_evidence_hashes.json'}\n")
    with (target / "交付说明.md").open("x", encoding="utf-8") as stream:
        stream.write(report)
    print(canonical({"payload_ready": True, "sheets": [{"name": s["name"], "rows": len(s["rows"])} for s in sheets]}))


if __name__ == "__main__":
    main()
