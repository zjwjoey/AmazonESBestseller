# -*- coding: utf-8 -*-
"""统一 CLI 入口（ARCHITECTURE §59-60）：Amazon.es bestseller research pipeline。

主链按需要组合联网采集与离线处理命令。
  - collect：联网（榜单+详情，串行 + 显式延迟，无并发）；缺省输出
    ``outputs/rankings.json`` + ``outputs/details.json``。
  - enrich / qa / export：全离线（不联网）。
  - translate-ds：联网且在首个请求前要求人工确认。
  - ``--offline``：全局标记；collect/translate-ds 拒绝离线。

示例：
  amazon-es collect --urls "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs/1293659031"
  amazon-es enrich --legacy product_details.json        # 30 条遗留真实数据
  amazon-es --offline enrich
  amazon-es --offline qa
  amazon-es audit-fields --products outputs/products.json --out outputs/field_closure.json
  amazon-es --offline export
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Mapping, Optional

#: 默认数据目录（仓库相对，避免硬编码绝对路径）
OUTPUTS = Path("outputs")


def _safe_print(*parts) -> None:
    """Print diagnostics without letting a narrow Windows code page abort QA."""
    text = " ".join(str(part) for part in parts)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")


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
    if not args.urls and not args.rankings_file:
        parser.error("collect 需要 --urls 或 --rankings-file")
    from .access.browser import BrowserSession
    from .collection.detail import collect_details, reparse_saved_details
    from .collection.planning import DetailState, build_plan, collect_asins
    from .collection.ranking import collect_rankings

    out_dir = str(Path(args.out_dir).resolve())
    with BrowserSession(headless=not args.headful) as session:
        rankings = (_load_json(args.rankings_file) if args.rankings_file
                    else collect_rankings(args.urls, session, out_dir))
        if args.rankings_only:
            _save_json(rankings, str(Path(out_dir) / "rankings.json"))
            print("collect rankings-only 完成：榜单 %d 条 → %s" %
                  (len(rankings), Path(out_dir) / "rankings.json"))
            return
        if args.manifest:
            manifest = _load_json(args.manifest)
            manifest_records = manifest.get("records", []) if isinstance(manifest, dict) else manifest
            allowed = {str(r.get("asin") or "").strip().upper() for r in manifest_records if isinstance(r, dict)}
            rankings = [r for r in rankings if str(r.get("asin") or "").strip().upper() in allowed]
            if not rankings:
                raise SystemExit("manifest 与榜单记录没有可匹配的 ASIN")
        state = DetailState(Path(out_dir) / "state" / "details_state.json")
        # Upgrade old cached records from local HTML before planning.  This is
        # deliberately offline and avoids re-requesting pages after a parser
        # schema bump.
        reparsed = reparse_saved_details(Path(out_dir) / "html", state)
        if reparsed:
            state.save()
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
    print("collect 完成：榜单 %d 条、详情 %d 条、离线重解析 %d 条、计划收集 %d 条"
          % (len(rankings), len(details), len(reparsed), len(plan["collect"])))


# ---------- select-quota（离线） ----------

def cmd_select_quota(args) -> None:
    """根据已采集榜单和审核过的 URL 配置生成 150/50 manifest。"""
    from .collection.quota import annotate_groups, normalize_group, select_quota

    rankings = _load_json(args.rankings)
    config = _load_json(args.config)
    rows = config.get("categories", []) if isinstance(config, dict) else config
    if not isinstance(rows, list) or not rows:
        raise SystemExit("类目配置必须是非空数组: %s" % args.config)
    quotas: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        group = normalize_group(row.get("category_group") or row.get("group"))
        if not group:
            raise SystemExit("类目配置缺少 group: %r" % row)
        try:
            quota = int(row.get("quota"))
        except (TypeError, ValueError):
            raise SystemExit("类目配置 quota 必须是整数: %r" % row)
        quotas[group] = quotas.get(group, 0) + quota
    tagged = annotate_groups(rankings, rows)
    try:
        selected = select_quota(tagged, quotas)
    except ValueError as exc:
        raise SystemExit(str(exc))
    records = [item for group in quotas for item in selected[group]]
    summary = {group: len(selected[group]) for group in quotas}
    summary["total"] = len(records)
    _save_json({"summary": summary, "records": records}, args.out)
    print("select-quota 完成：家居 %d、DIY %d、总计 %d → %s"
          % (summary.get("hogar", 0), summary.get("diy", 0), len(records), args.out))


# ---------- translate-ds（联网 API） ----------

def cmd_translate_ds(args) -> None:
    """按 ASIN 顺序调用 DS，输出 ASIN → 翻译结果映射。"""
    if args.offline:
        raise SystemExit("translate-ds 需要联网，不能与 --offline 同用")
    products = _load_json(args.products)
    if not isinstance(products, list):
        raise SystemExit("products JSON 顶层必须是数组: %s" % args.products)

    endpoint = args.endpoint or os.getenv("DEEPSEEK_ENDPOINT") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/chat/completions"
    model = args.model or os.getenv("DEEPSEEK_MODEL") or os.getenv("DS_MODEL") or "deepseek-chat"
    print("translate-ds 即将调用 DeepSeek API：%d 个 ASIN，endpoint=%s，model=%s"
          % (len(products), endpoint, model))
    try:
        confirmation = input("输入 YES 确认开始调用 API，其他输入将取消：")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("未确认，已取消 DS API 调用")
    if confirmation.strip().upper() != "YES":
        raise SystemExit("未确认，已取消 DS API 调用")

    from .translation.ds import DeepSeekTranslator

    translator = DeepSeekTranslator(
        endpoint=args.endpoint or None,
        model=args.model or None,
        cache_path=args.cache or args.out,
        max_retries=args.max_retries,
        backoff_seconds=args.backoff_seconds,
        timeout=args.timeout,
    )
    output: dict[str, dict] = {}
    for product in products:
        result = translator.translate_record(product)
        asin = str(result.get("asin") or product.get("asin") or "").strip().upper()
        if asin:
            output[asin] = result
        translator.save_cache()
    _save_json(output, args.out)
    success = sum(1 for r in output.values() if r.get("translation_status") == "success")
    partial = sum(1 for r in output.values() if r.get("translation_status") == "partial")
    failed = sum(1 for r in output.values() if r.get("translation_status") == "failed")
    print("translate-ds 完成：成功 %d、部分 %d、失败 %d、总计 %d → %s"
          % (success, partial, failed, len(output), args.out))


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


def cmd_repair_cache(args) -> None:
    """离线：用已保存详情 HTML 补齐 canonical 商品字段。"""
    from .collection.repair import repair_cached_products

    products = _load_json(args.products)
    repaired, report = repair_cached_products(products, args.html_dir)
    _save_json(repaired, args.out)
    print("repair-cache 完成：匹配 %d 页、忽略 %d 页、修改 %d 个商品、%d 个字段 → %s"
          % (report["matched_pages"], report["ignored_pages"],
             report["changed_products"], report["changed_fields"], args.out))


def cmd_reparse_details(args) -> None:
    """离线：用保存 HTML 升级详情 schema，不发起 Amazon 请求。"""
    from .collection.detail import reparse_saved_details
    from .collection.planning import DetailState
    state = DetailState(args.state)
    records = reparse_saved_details(args.html_dir, state)
    state.save()
    _save_json(state.records(), args.out)
    print("reparse-details 完成：重解析 %d 条、缓存总计 %d 条 → %s"
          % (len(records), len(state), args.out))


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
        _safe_print("!! P0/P1 问题 %d 条：" % len(p0p1))
        for asin, code, msg in p0p1[:20]:
            _safe_print("   %s %s: %s" % (asin, code, msg))
    else:
        print("0 P0 / 0 P1 OK")   # 不用 ✓（U+2713）：GBK 控制台无法编码


# ---------- field closure audit（离线） ----------

def cmd_audit_fields(args) -> None:
    """Audit Source → Raw → Canonical → Derived → Excel without mutation."""
    from .qa.field_closure import audit_field_closure, write_report

    products = _load_json(args.products)
    details = _load_json(args.details) if args.details else []
    rankings = _load_json(args.rankings) if args.rankings else []
    translations = _load_json(args.translations) if args.translations else None
    report = audit_field_closure(products, details=details, rankings=rankings,
                                 html_dir=args.html_dir or None, run_dir=args.run_dir or None,
                                 workbook_path=args.workbook or None, translations=translations)
    write_report(report, args.out, args.md_out or None)
    s = report["summary"]
    print("Field Closure Audit：%d SKU、%d 字段；PASS %d / SOURCE_MISSING %d / PARSER_MISSED %d / MAPPING_MISSED %d / DERIVED_MISSING %d / EXPORT_MISMATCH %d / IMAGE_MISSING %d"
          % (s["total_skus"], s["fields_checked"], s["pass"], s["SOURCE_MISSING"],
             s["PARSER_MISSED"], s["MAPPING_MISSED"], s["DERIVED_MISSING"],
             s.get("EXPORT_VALUE_MISMATCH", 0), s.get("IMAGE_MISSING", 0)))
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

    translations = _load_json(args.translations) if args.translations else None
    closure_findings = []
    from .qa.field_closure import audit_field_closure
    details = _load_json(getattr(args, "details", "")) if getattr(args, "details", "") else None
    rankings = _load_json(getattr(args, "rankings", "")) if getattr(args, "rankings", "") else None
    closure_enabled = bool(args.translations or details or rankings or
                            getattr(args, "html_dir", None) or getattr(args, "run_dir", ""))
    closure = audit_field_closure(products, details=details, rankings=rankings,
                                  html_dir=getattr(args, "html_dir", None) or None,
                                  run_dir=getattr(args, "run_dir", "") or None,
                                  translations=translations) if closure_enabled else {"records": []}
    blocked_closure = [r for r in closure.get("records", [])
                       if r.get("severity") == "P1" and r.get("classification") in
                       {"PARSER_MISSED", "MAPPING_MISSED", "DERIVED_MISSING",
                        "TRANSLATION_INCOMPLETE"}]
    closure_findings = [(r.get("asin"), r.get("classification"), r.get("message"))
                        for r in blocked_closure]
    blocked = blocked + closure_findings
    if blocked and not args.force:
        lines = ["QA/字段闭环门禁未通过：%d 条 P0/P1 问题，拒绝导出（--force 强制）"
                 % len(blocked)]
        for asin, code, msg in blocked[:10]:
            lines.append("   %s %s: %s" % (asin, code, msg))
        raise SystemExit("\n".join(lines))
    if blocked and args.force:
        print("警告：--force 忽略 %d 条 QA/字段闭环 P0/P1 问题" % len(blocked))
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
        description="Amazon.es bestseller research pipeline")
    parser.add_argument("--offline", action="store_true",
                        help="离线标记：collect/translate-ds 拒绝；其余处理命令不联网")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="联网采集榜单+详情（串行）")
    c.add_argument("--urls", nargs="+", default=[],
                   help="榜单页 URL（/zgbs/<NODE>）")
    c.add_argument("--out-dir", default=str(OUTPUTS), help="输出目录（默认 outputs/）")
    c.add_argument("--headful", action="store_true", help="有头浏览器（默认 headless）")
    c.add_argument("--rankings-only", action="store_true", help="只采集榜单页，不访问详情页")
    c.add_argument("--rankings-file", default="", help="复用已保存榜单 JSON，仅访问 manifest 中详情")
    c.add_argument("--manifest", default="", help="详情采集 ASIN manifest JSON（与 --rankings-file 配合）")
    c.set_defaults(func=lambda a, p=c: cmd_collect(a, p))

    s = sub.add_parser("select-quota", help="离线：按审核类目配置选择 150/50 唯一 ASIN")
    s.add_argument("--rankings", required=True, help="榜单记录 JSON")
    s.add_argument("--config", required=True, help="类目配置 JSON")
    s.add_argument("--out", required=True, help="配额 manifest JSON")
    s.set_defaults(func=cmd_select_quota)

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

    r = sub.add_parser("repair-cache", help="离线：用保存 HTML 修复已有商品的 canonical/display 字段")
    r.add_argument("--products", required=True, help="规范化商品 JSON 数组")
    r.add_argument("--html-dir", required=True, help="保存的详情 HTML 目录")
    r.add_argument("--out", required=True, help="修复后的商品 JSON")
    r.set_defaults(func=cmd_repair_cache)

    rp = sub.add_parser("reparse-details", help="离线：按当前详情 schema 重建 raw details（重复 ASIN 取首个有效目录）")
    rp.add_argument("--html-dir", nargs="+", required=True)
    rp.add_argument("--state", required=True, help="DetailState JSON")
    rp.add_argument("--out", required=True, help="重建后的 details JSON")
    rp.set_defaults(func=cmd_reparse_details)

    t = sub.add_parser("translate-ds", help="联网：调用 DeepSeek API 翻译中文显示字段")
    t.add_argument("--products", required=True, help="规范化商品 JSON 数组")
    t.add_argument("--cache", default="", help="翻译缓存 JSON（默认写入 --out）")
    t.add_argument("--out", required=True, help="ASIN → 翻译结果 JSON")
    t.add_argument("--endpoint", default="", help="完整 API endpoint（默认 DeepSeek chat/completions）")
    t.add_argument("--model", default="", help="模型名（默认 deepseek-chat）")
    t.add_argument("--max-retries", type=int, default=2)
    t.add_argument("--backoff-seconds", type=float, default=1.0)
    t.add_argument("--timeout", type=float, default=60.0)
    t.set_defaults(func=cmd_translate_ds)

    q = sub.add_parser("qa", help="离线：商品表 → QA 结果")
    q.add_argument("--products", default=str(OUTPUTS / "products.json"))
    q.add_argument("--out", default=str(OUTPUTS / "qa.json"))
    q.set_defaults(func=cmd_qa)

    a = sub.add_parser("audit-fields", help="离线：Source→Raw→Canonical→Derived→Excel 字段闭环审计")
    a.add_argument("--products", default=str(OUTPUTS / "products.json"), help="规范化商品表 JSON")
    a.add_argument("--details", default=str(OUTPUTS / "details.json"), help="详情 raw JSON（可选）")
    a.add_argument("--rankings", default=str(OUTPUTS / "rankings.json"), help="榜单 raw JSON（可选）")
    a.add_argument("--html-dir", nargs="+", default=[],
                   help="保存的详情 HTML 目录（可选，可传多个，用于识别 PARSER_MISSED）")
    a.add_argument("--run-dir", default="", help="采集 run 根目录（可选，自动读取 ranking_*.html 作为类目来源）")
    a.add_argument("--workbook", default="", help="导出的 Excel 工作簿（可选，逐 ASIN 核验展示层）")
    a.add_argument("--translations", default="", help="翻译映射 JSON（可选，用于中文表对账）")
    a.add_argument("--out", default=str(OUTPUTS / "field_closure.json"))
    a.add_argument("--md-out", default="", help="Markdown 输出路径（默认与 JSON 同名 .md）")
    a.set_defaults(func=cmd_audit_fields)

    x = sub.add_parser("export", help="离线：商品表 → Excel")
    x.add_argument("--products", default=str(OUTPUTS / "products.json"))
    x.add_argument("--translations", default="")
    x.add_argument("--details", default="", help="详情 raw JSON（用于字段闭环门禁）")
    x.add_argument("--rankings", default="", help="榜单 raw JSON（用于字段闭环门禁）")
    x.add_argument("--html-dir", nargs="+", default=[], help="保存的详情 HTML 目录（可选）")
    x.add_argument("--run-dir", default="", help="采集 run 根目录（可选）")
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
