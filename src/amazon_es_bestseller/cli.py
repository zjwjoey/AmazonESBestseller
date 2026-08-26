# -*- coding: utf-8 -*-
"""统一 CLI 入口（ARCHITECTURE §59-60）：collect / enrich / qa / export。

主链：``collect → enrich → qa → export``。
  - collect：联网（榜单+详情，串行 + 显式延迟，无并发）；缺省输出
    ``outputs/rankings.json`` + ``outputs/details.json``。
  - enrich / qa / export：全离线（不联网）。
  - ``--offline``：全局标记；collect 拒绝离线（需联网采集）。

示例：
  amazon-es collect --urls "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs/1293659031"
  amazon-es enrich --legacy product_details.json        # 30 条遗留真实数据
  amazon-es enrich --offline
  amazon-es qa --offline
  amazon-es audit-fields --products outputs/products.json --out outputs/field_closure.json
  amazon-es export --offline
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
import shutil
import sys
from pathlib import Path
from typing import List, Mapping, Optional

#: 默认数据目录（仓库相对，避免硬编码绝对路径）
OUTPUTS = Path("outputs")


def _load_json(path: Optional[str]) -> list:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise SystemExit("找不到输入文件: %s" % path)
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def _save_json(data, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_category_planning(path: Optional[str]):
    if not path:
        return None
    data = _load_json(path)
    if not isinstance(data, list):
        raise SystemExit("类目规划 JSON 顶层必须是数组: %s" % path)
    return data


def _load_images_by_asin(directory: Optional[str], records: list) -> dict:
    if not directory:
        return {}
    root = Path(directory)
    if not root.is_dir():
        print("警告：图片目录不存在，跳过内嵌图片: %s" % directory)
        return {}
    out = {}
    for record in records:
        asin = str(record.get("asin") or "").strip().upper()
        if not asin:
            continue
        for suffix in (".png", ".jpg", ".jpeg"):
            path = root / (asin + suffix)
            if path.exists():
                try:
                    out[asin] = (BytesIO(path.read_bytes()), 70, 70)
                except OSError as exc:
                    print("警告：无法读取图片 %s：%s" % (path, exc))
                break
    return out


# ---------- collect（联网） ----------

def cmd_collect(args, parser: argparse.ArgumentParser) -> None:
    """榜单+详情串行采集；rankings.json/details.json 稳定输出到 out_dir 根。"""
    if args.offline:
        parser.error("collect 需要联网，不能与 --offline 同用")
    if not args.urls:
        parser.error("collect 需要 --urls 指定至少一个榜单页 URL")
    from .access.browser import BrowserSession
    from .collection.detail import collect_details
    from .collection.planning import DetailState, build_plan, collect_asins
    from .collection.ranking import collect_rankings

    out_dir = str(Path(args.out_dir).resolve())
    with BrowserSession(headless=not args.headful) as session:
        rankings = collect_rankings(args.urls, session, out_dir)
        state = DetailState(Path(out_dir) / "state" / "details_state.json")
        plan = build_plan(rankings, state)
        details = collect_details(collect_asins(plan), session, out_dir)
        state.update(details)
        state.save()
        # details.json 用 state 全量重建：resume 场景下 collect_details 只产出
        # 本次增量（新增/重采），直接覆盖会丢已缓存详情；state 是跨 run 权威
        # 持久缓存，含全部 ASIN 的最新详情。
        _save_json(state.records(), str(Path(out_dir) / "details.json"))

    # 最新一次 run 的产物稳定复制到 out_dir 根，供 enrich/qa/export 读取
    runs = sorted(Path(out_dir).glob("runs/*"), reverse=True)
    if runs:
        latest = runs[0]
        for name in ("rankings.json", "details.json"):
            src = latest / name
            if src.exists():
                shutil.copy(src, Path(out_dir) / name)
    print("collect 完成：榜单 %d 条、详情 %d 条、计划收集 %d 条"
          % (len(rankings), len(details), len(plan["collect"])))


# ---------- enrich（离线） ----------

def cmd_enrich(args) -> None:
    """榜单+详情 → 规范化+中文派生商品表（products.json）。"""
    from .pipeline import enrich_products, legacy_flat_to_detail, legacy_flat_to_ranking

    if args.legacy:
        data = _load_json(args.legacy)
        rankings = [legacy_flat_to_ranking(r) for r in data]
        details = [legacy_flat_to_detail(r) for r in data]
        print("legacy 导入：%d 条真实记录（构造型 BSR 列已丢弃）" % len(data))
    else:
        rankings = _load_json(args.rankings)
        details = _load_json(args.details)
        print("榜单 %d 条、详情 %d 条" % (len(rankings), len(details)))

    translations = _load_json(args.translations) if args.translations else None
    products = enrich_products(rankings, details, translations)
    _save_json(products, args.out)
    print("enrich 完成：%d 条商品 → %s" % (len(products), args.out))


# ---------- qa（离线） ----------

def cmd_qa(args) -> None:
    """商品表 → QA 结果（qa.json）+ 控制台汇总。"""
    from .qa.run import qa_summary, run_qa

    products = _load_json(args.products)
    results = []
    p0p1 = []
    for p in products:
        res = run_qa(p)
        rec = {"asin": p.get("asin"), "qa_status": res["qa_status"], "counts": res["counts"],
               "issues": [{"code": i.code, "severity": i.severity, "field": i.field,
                           "message": i.message} for i in res["qa_issues"]]}
        results.append(rec)
        for i in res["qa_issues"]:
            if i.severity in ("P0", "P1"):
                p0p1.append((p.get("asin"), i.code, i.message))
    summary = qa_summary(products)
    out = {"summary": summary, "records": results}
    _save_json(out, args.out)
    print("QA：%s" % summary)
    print("QA 结果 → %s" % args.out)
    if p0p1:
        print("!! P0/P1 问题 %d 条：" % len(p0p1))
        for asin, code, msg in p0p1[:20]:
            print("   %s %s: %s" % (asin, code, msg))
    else:
        print("0 P0 / 0 P1 OK")   # 不用 ✓（U+2713）：GBK 控制台无法编码


# ---------- field closure audit（离线） ----------

def cmd_audit_fields(args) -> None:
    """Audit Source → Raw → Canonical → Derived → Excel without mutation."""
    from .qa.field_closure import audit_field_closure, write_report

    products = _load_json(args.products)
    details = _load_json(args.details) if args.details else []
    rankings = _load_json(args.rankings) if args.rankings else []
    report = audit_field_closure(products, details=details, rankings=rankings,
                                 html_dir=args.html_dir or None)
    write_report(report, args.out, args.md_out or None)
    s = report["summary"]
    print("Field Closure Audit：%d SKU、%d 字段；PASS %d / SOURCE_MISSING %d / PARSER_MISSED %d / MAPPING_MISSED %d / DERIVED_MISSING %d"
          % (s["total_skus"], s["fields_checked"], s["pass"], s["SOURCE_MISSING"],
             s["PARSER_MISSED"], s["MAPPING_MISSED"], s["DERIVED_MISSING"]))
    print("审计 JSON → %s" % args.out)
    print("审计 Markdown → %s" % (args.md_out or str(Path(args.out).with_suffix(".md"))))


# ---------- export（离线） ----------

def cmd_export(args) -> None:
    """商品表 → Excel 工作簿（B3x 重写为新 3 表/26 列契约）。

    QA 硬门禁（QA_RULES §31）：导出前跑全量 QA，存在任何 P0/P1 即拒绝导出，
    除非显式 --force（保留上游错误证据，不静默修复，§25）。
    """
    from .export.excel import export_workbook
    from .qa.run import blocking_issues

    products = _load_json(args.products)
    blocked = blocking_issues(products)
    if blocked and not args.force:
        lines = ["QA 门禁未通过：%d 条 P0/P1 问题，拒绝导出（--force 强制）"
                 % len(blocked)]
        for asin, code, msg in blocked[:10]:
            lines.append("   %s %s: %s" % (asin, code, msg))
        raise SystemExit("\n".join(lines))

    translations = _load_json(args.translations) if args.translations else None
    images_by_asin = _load_images_by_asin(args.images_dir, products)
    category_planning = _load_category_planning(args.category_planning)
    prev_workbook = None
    if args.prev_workbook:
        import openpyxl
        prev_workbook = openpyxl.load_workbook(args.prev_workbook)
    wb = export_workbook(products, translations=translations,
                         images_by_asin=images_by_asin,
                         category_planning=category_planning,
                         prev_workbook=prev_workbook, out_path=args.out)
    print("export 完成：%s（%s 条商品，%d 张表）" % (args.out, len(products), len(wb.sheetnames)))


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amazon-es",
        description="Amazon.es 畅销品研究主链：collect → enrich → qa → export")
    parser.add_argument("--offline", action="store_true",
                        help="离线标记：collect 拒绝、enrich/qa/export 要求")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="联网采集榜单+详情（串行）")
    c.add_argument("--urls", nargs="+", default=[],
                   help="榜单页 URL（/zgbs/<NODE>）")
    c.add_argument("--out-dir", default=str(OUTPUTS), help="输出目录（默认 outputs/）")
    c.add_argument("--headful", action="store_true", help="有头浏览器（默认 headless）")
    c.set_defaults(func=lambda a, p=c: cmd_collect(a, p))

    e = sub.add_parser("enrich", help="离线：榜单+详情 → 规范化+中文派生商品表")
    e.add_argument("--rankings", default=str(OUTPUTS / "rankings.json"),
                   help="榜单记录 JSON")
    e.add_argument("--details", default=str(OUTPUTS / "details.json"),
                   help="详情记录 JSON")
    e.add_argument("--legacy", default="",
                   help="遗留扁平数据（product_details.json），导入时丢弃构造型 BSR")
    e.add_argument("--translations", default="", help="翻译表 JSON（ASIN → {title_zh}）")
    e.add_argument("--out", default=str(OUTPUTS / "products.json"), help="输出商品表 JSON")
    e.set_defaults(func=cmd_enrich)

    q = sub.add_parser("qa", help="离线：商品表 → QA 结果")
    q.add_argument("--products", default=str(OUTPUTS / "products.json"))
    q.add_argument("--out", default=str(OUTPUTS / "qa.json"))
    q.set_defaults(func=cmd_qa)

    a = sub.add_parser("audit-fields", help="离线：Source→Raw→Canonical→Derived→Excel 字段闭环审计")
    a.add_argument("--products", default=str(OUTPUTS / "products.json"), help="规范化商品表 JSON")
    a.add_argument("--details", default=str(OUTPUTS / "details.json"), help="详情 raw JSON（可选）")
    a.add_argument("--rankings", default=str(OUTPUTS / "rankings.json"), help="榜单 raw JSON（可选）")
    a.add_argument("--html-dir", default="", help="保存的详情 HTML 目录（可选，用于识别 PARSER_MISSED）")
    a.add_argument("--out", default=str(OUTPUTS / "field_closure.json"))
    a.add_argument("--md-out", default="", help="Markdown 输出路径（默认与 JSON 同名 .md）")
    a.set_defaults(func=cmd_audit_fields)

    x = sub.add_parser("export", help="离线：商品表 → Excel")
    x.add_argument("--products", default=str(OUTPUTS / "products.json"))
    x.add_argument("--translations", default="")
    x.add_argument("--prev-workbook", default="", help="前版工作簿（按 ASIN 保留备注）")
    x.add_argument("--images-dir", default="", help="本地图片目录（<ASIN>.png/.jpg/.jpeg）")
    x.add_argument("--category-planning", default="", help="类目规划 JSON（字典行数组或二维数组）")
    x.add_argument("--out", default=str(OUTPUTS / "选品清单.xlsx"))
    x.add_argument("--force", action="store_true",
                   help="跳过 QA 硬门禁（存在 P0/P1 也导出，保留上游证据）")
    x.set_defaults(func=cmd_export)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    from .access.detector import AccessStopError
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except AccessStopError as e:
        # 访问门禁（ARCHITECTURE §6）：非 NORMAL 停止采集，退出码 2
        parser.exit(2, "!! %s\n" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
