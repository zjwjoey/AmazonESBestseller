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
import time
from pathlib import Path
from typing import List, Mapping, Optional

#: 默认数据目录（仓库相对，避免硬编码绝对路径）
OUTPUTS = Path("outputs")

#: 证据输入默认路径。export 与 enrich/qa 共用同一组默认值，保证字段闭环
#: 门禁在默认调用下也会运行（缺省时曾静默跳过，见 QA_RULES §31）。
DEFAULT_DETAILS = str(OUTPUTS / "details.json")
DEFAULT_RANKINGS = str(OUTPUTS / "rankings.json")


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


def _load_evidence_json(path: Optional[str], default_path: str):
    """闭环门禁的证据输入：显式指定但缺失 → 报错；默认路径缺失 → 视为不可用。

    默认路径可以合法地不存在（例如只跑离线子链），此时由调用方显式声明门禁
    降级；显式传入的路径缺失仍必须失败，避免打错路径被当成"没有证据"。
    """
    if not path:
        return None
    if not Path(path).exists():
        if str(path) != str(default_path):
            raise SystemExit("找不到输入文件: %s" % path)
        return None
    return _load_json(path)


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
    from .access.location import ensure_spain_delivery
    from .collection.detail import (CURRENT_DETAIL_SCHEMA_VERSION,
                                    collect_details, reparse_saved_details)
    from .collection.planning import DetailState, build_plan, collect_asins
    from .collection.checkpoints import read_checkpoint
    from .collection.ranking import collect_rankings

    out_dir = str(Path(args.out_dir).resolve())
    with BrowserSession(headless=not args.headful,
                        profile_dir=args.profile_dir or None) as session:
        session.challenge_wait_seconds = args.challenge_wait_seconds
        session.manual_assist = args.manual_assist
        location = ensure_spain_delivery(session, args.postal_code)
        if location is not None:
            _safe_print("配送地点已确认：%s" % (location.text or "西班牙"))
        if args.rankings_file:
            rankings = _load_json(args.rankings_file)
        elif args.pages_per_url != 1:
            rankings = collect_rankings(args.urls, session, out_dir,
                                        pages_per_url=args.pages_per_url)
        else:
            rankings = collect_rankings(args.urls, session, out_dir)
        # Quarantine affects detail planning only; raw ranking evidence remains
        # unchanged for audit/export.
        quarantine_dir = Path(out_dir) / "quarantine"
        quarantined = {p.stem.upper() for p in quarantine_dir.rglob("*.html")}
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
        planning_rankings = [r for r in rankings
                             if str(r.get("asin") or "").strip().upper() not in quarantined]
        skipped = len(rankings) - len(planning_rankings)
        if skipped:
            print("已跳过隔离 ASIN %d 条详情计划，原始榜单证据保留" % skipped)
        state = DetailState(Path(out_dir) / "state" / "details_state.json")
        # Promote completed per-ASIN checkpoints before building the next plan;
        # this is what makes Ctrl-C/resume useful even when the batch summary
        # was never written.
        checkpoint_records = []
        for asin in {str(r.get("asin") or "").strip().upper() for r in rankings}:
            checkpoint = read_checkpoint(Path(out_dir) / "checkpoints", asin)
            if checkpoint and checkpoint.get("status") == "success" and checkpoint.get("record"):
                checkpoint_records.append(checkpoint["record"])
        if checkpoint_records:
            state.update(checkpoint_records)
            state.save()
        # Upgrade old cached records from local HTML before planning.  This is
        # deliberately offline and avoids re-requesting pages after a parser
        # schema bump.
        stale_asins = [r.get("asin") for r in state.records()
                       if int(r.get("detail_schema_version", 0) or 0)
                       < CURRENT_DETAIL_SCHEMA_VERSION]
        reparsed = (reparse_saved_details(Path(out_dir) / "html", state,
                                           asins=stale_asins)
                    if stale_asins else [])
        if reparsed:
            state.save()
        plan = build_plan(planning_rankings, state)
        planned_asins = collect_asins(plan)
        if args.progress:
            def _write_progress(event):
                _save_json({"planned": len(planned_asins), **event}, args.progress)
            details = collect_details(planned_asins, session, out_dir,
                                      on_progress=_write_progress)
        else:
            details = collect_details(planned_asins, session, out_dir)
        state.update(details)
        state.save()
        # details.json 用 state 全量重建：resume 场景下 collect_details 只产出
        # 本次增量（新增/重采），直接覆盖会丢已缓存详情；state 是跨 run 权威
        # 持久缓存，含全部 ASIN 的最新详情。
        _save_json(state.records(), str(Path(out_dir) / "details.json"))

    # 本次采集的榜单产物复制到 out_dir 根，供 enrich/qa/export 读取。
    # details.json 已由 state 全量重建，绝不用 run 目录副本覆盖；复用
    # --rankings-file 时本次没有新 run 目录，跳过复制，避免旧 run 的榜单
    # 覆盖调用方显式提供的输入（会让下游 enrich 与本次详情不同源）。
    if not args.rankings_file:
        runs = sorted(Path(out_dir).glob("runs/*"), reverse=True)
        if runs:
            src = runs[0] / "rankings.json"
            if src.exists():
                shutil.copy(src, Path(out_dir) / "rankings.json")
    print("collect 完成：榜单 %d 条、详情 %d 条、离线重解析 %d 条、计划收集 %d 条"
          % (len(rankings), len(details), len(reparsed), len(plan["collect"])))


def _batch_countdown(seconds: int, category_name: str) -> None:
    """Keep the process alive during inter-category cooldown with a live timer."""
    remaining = max(0, int(seconds))
    while remaining > 0:
        hours, rem = divmod(remaining, 3600)
        minutes, secs = divmod(rem, 60)
        print("\r[冷却倒计时] %s：%02d:%02d:%02d" %
              (category_name, hours, minutes, secs), end="", flush=True)
        time.sleep(1)
        remaining -= 1
    if seconds > 0:
        print("\r[冷却完成] %s：开始下一类目                    " % category_name,
              flush=True)


def _batch_countdown_until(deadline: float, category_name: str) -> None:
    """Resume an already persisted cooldown without resetting its deadline."""
    while True:
        remaining = max(0, int(deadline - time.time() + 0.999))
        if remaining <= 0:
            break
        hours, rem = divmod(remaining, 3600)
        minutes, secs = divmod(rem, 60)
        print("\r[冷却倒计时] %s：%02d:%02d:%02d" %
              (category_name, hours, minutes, secs), end="", flush=True)
        time.sleep(min(1, remaining))
    print("\r[冷却完成] %s：开始下一类目                    " % category_name,
          flush=True)


def _load_json_array_or_empty(path: Path) -> list:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("批处理证据文件损坏，已停止（不会静默清空）: %s (%s)" %
                         (path, exc))
    if not isinstance(value, list):
        raise SystemExit("批处理证据文件顶层必须是数组: %s" % path)
    return value


def cmd_batch_collect(args, parser: argparse.ArgumentParser) -> None:
    """Run the corrected source plan category-by-category with resumable cooldown."""
    if args.offline:
        parser.error("batch-collect 需要联网，不能与 --offline 同用")
    plan_path = Path(args.plan)
    if not plan_path.exists():
        parser.error("找不到提取计划: %s" % args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    sources = plan.get("sources", []) if isinstance(plan, dict) else []
    if not isinstance(sources, list) or not sources:
        parser.error("提取计划没有 sources")
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "batch_state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit("批处理状态损坏，已停止（请人工检查后恢复）: %s (%s)" %
                             (state_path, exc))
        if not isinstance(state, dict):
            raise SystemExit("批处理状态顶层必须是对象: %s" % state_path)
    done_urls = set(str(u) for u in state.get("completed_source_urls", [])
                    if str(u).strip())
    done_categories = set(str(g) for g in state.get("completed_categories", [])
                          if str(g).strip())
    detail_asins = set(str(a).upper() for a in state.get("detail_asins", [])
                       if str(a).strip())
    pending_detail_asins = set(str(a).upper() for a in state.get("pending_detail_asins", [])
                               if str(a).strip())
    shortfall_sources = dict(state.get("shortfall_sources", {}) or {})
    all_rankings = _load_json_array_or_empty(out_dir / "rankings.json")
    all_details = _load_json_array_or_empty(out_dir / "details.json")
    # Seed the batch with validated records from the preceding 4,500-SKU run.
    for path_text in (getattr(args, "seed_rankings", ""),):
        if path_text:
            for row in _load_json_array_or_empty(Path(path_text)):
                if not isinstance(row, dict):
                    continue
                try:
                    rank = int(row.get("bestseller_rank") or 0)
                except (TypeError, ValueError):
                    rank = 0
                if not 31 <= rank <= 50:
                    continue
                key = (row.get("ranking_source_url"), rank,
                       str(row.get("asin") or "").upper())
                if key not in {(r.get("ranking_source_url"), r.get("bestseller_rank"),
                                str(r.get("asin") or "").upper())
                               for r in all_rankings if isinstance(r, dict)}:
                    all_rankings.append(row)
    existing_products_path = getattr(args, "existing_products", "")
    if existing_products_path:
        for row in _load_json_array_or_empty(Path(existing_products_path)):
            if isinstance(row, dict) and row.get("asin"):
                detail_asins.add(str(row["asin"]).upper())
    existing_details_path = getattr(args, "existing_details", "")
    if existing_details_path:
        for row in _load_json_array_or_empty(Path(existing_details_path)):
            if isinstance(row, dict) and row.get("asin"):
                all_details.append(row)
    detail_map = {str(r.get("asin") or "").upper(): r for r in all_details
                  if isinstance(r, dict) and r.get("asin")}
    detail_asins.update(detail_map)
    ranking_keys = {(r.get("ranking_source_url"), r.get("bestseller_rank"),
                     str(r.get("asin") or "").upper())
                    for r in all_rankings if isinstance(r, dict)}
    cooldown = (int(args.cooldown_seconds) if args.cooldown_seconds is not None
                else int(plan.get("cooldown_between_categories_seconds", 1800)))
    if cooldown < 0:
        parser.error("--cooldown-seconds 不能为负数")

    # Plan status is evidence: completed/source_missing sources are not
    # requested again, even when the process was first started with empty state.
    for source in sources:
        if isinstance(source, dict) and source.get("status") in {"completed", "source_missing"}:
            url = str(source.get("source_url") or "").strip()
            if url:
                done_urls.add(url)

    grouped = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        group = str(source.get("category_group") or "unknown")
        grouped.setdefault(group, []).append(source)
    ordered_groups = sorted(grouped, key=lambda g: (
        min(int(s.get("category_sequence") or 999) for s in grouped[g]), g))
    pending_groups = []
    for group in ordered_groups:
        pending = [s for s in grouped[group]
                   if s.get("status") == "pending" and
                   str(s.get("source_url") or "") not in done_urls]
        if pending:
            pending_groups.append((group, pending))
    if not pending_groups and (args.rankings_only or not pending_detail_asins):
        print("batch-collect：计划中的待提取来源和详情已全部完成")
        return
    from .access.browser import BrowserSession
    from .access.location import ensure_spain_delivery
    from .collection.detail import collect_details
    from .collection.ranking import collect_rankings

    def save_state(active_group=None):
        state_path.write_text(json.dumps({
            "plan": str(plan_path),
            "completed_source_urls": sorted(done_urls),
            "completed_categories": sorted(done_categories),
            "detail_asins": sorted(detail_asins),
            "pending_detail_asins": sorted(pending_detail_asins),
            "shortfall_sources": shortfall_sources,
            "cooldown_until": state.get("cooldown_until"),
            "cooldown_category": state.get("cooldown_category"),
            "active_category": active_group,
            "completed_category_count": len(done_categories),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    with BrowserSession(headless=not args.headful,
                        profile_dir=args.profile_dir or None) as session:
        session.challenge_wait_seconds = args.challenge_wait_seconds
        session.manual_assist = args.manual_assist
        location = ensure_spain_delivery(session, args.postal_code)
        if location is not None:
            _safe_print("配送地点已确认：%s" % (location.text or "西班牙"))

        # A restart during the inter-category wait resumes the original
        # deadline; it never silently skips the configured cooling interval.
        persisted_until = state.get("cooldown_until")
        if persisted_until:
            try:
                deadline = float(persisted_until)
            except (TypeError, ValueError):
                raise SystemExit("批处理冷却状态无效: cooldown_until")
            if deadline > time.time():
                _batch_countdown_until(deadline, str(state.get("cooldown_category") or "上一类目"))
            state["cooldown_until"] = None
            state["cooldown_category"] = None
            save_state(None)

        # Retry detail failures before moving on to a new category.  A failed
        # ASIN remains pending and therefore survives a process restart.
        if pending_detail_asins and not args.rankings_only:
            retry = sorted(pending_detail_asins - detail_asins)
            if retry:
                retry_details = collect_details(retry, session, str(out_dir / "detail_cache"))
                success = {str(r.get("asin") or "").upper() for r in retry_details if r.get("asin")}
                for record in retry_details:
                    asin = str(record.get("asin") or "").upper()
                    if asin:
                        detail_map[asin] = record
                        detail_asins.add(asin)
                pending_detail_asins.difference_update(success)
                all_details = list(detail_map.values())
                (out_dir / "details.json").write_text(json.dumps(all_details, ensure_ascii=False, indent=2), encoding="utf-8")
                save_state(None)

        for group_index, (group, group_sources) in enumerate(pending_groups):
            category_name = str(group_sources[0].get("category_name_zh") or group)
            category_dir = out_dir / "categories" / group
            category_dir.mkdir(parents=True, exist_ok=True)
            save_state(group)
            urls = [str(s["source_url"]) for s in group_sources]
            print("\n[批处理] %s：%d 个来源页，目标排名31–50" %
                  (category_name, len(urls)))
            rankings = collect_rankings(urls, session, str(category_dir), pages_per_url=1)
            target = [r for r in rankings
                      if 31 <= int(r.get("bestseller_rank") or 0) <= 50]
            for r in target:
                r["batch_target_rank_range"] = "31-50"
                r["batch_category_group"] = group
                key = (r.get("ranking_source_url"), r.get("bestseller_rank"),
                       str(r.get("asin") or "").upper())
                if key not in ranking_keys:
                    all_rankings.append(r)
                    ranking_keys.add(key)
            (category_dir / "rankings_31_50.json").write_text(
                json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")
            # Validate each source independently.  A short page is retryable;
            # it must not be hidden by marking the whole category complete.
            complete_urls = set()
            for url in urls:
                count = sum(1 for r in target if str(r.get("ranking_source_url") or "") == url)
                if count == 20:
                    complete_urls.add(url)
                else:
                    shortfall_sources[url] = {"expected": 20, "observed": count,
                                              "status": "shortfall"}
            unique_targets = []
            seen_category = set()
            for r in target:
                asin = str(r.get("asin") or "").upper()
                if asin and asin not in seen_category:
                    seen_category.add(asin)
                    unique_targets.append(r)
            manifest = {"category_group": group, "records": unique_targets,
                        "unique_asins": len(unique_targets), "rank_range": [31, 50]}
            (category_dir / "manifest_31_50.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            new_asins = [str(r["asin"]).upper() for r in unique_targets
                         if str(r["asin"]).upper() not in detail_asins]
            # Persist ranking evidence and the detail work queue before the
            # first detail request. An access stop during detail collection
            # therefore leaves a resumable checkpoint instead of losing the
            # just-collected source results.
            if not args.rankings_only:
                pending_detail_asins.update(new_asins)
            (out_dir / "rankings.json").write_text(
                json.dumps(all_rankings, ensure_ascii=False, indent=2), encoding="utf-8")
            save_state(group)
            details = [] if args.rankings_only else (
                collect_details(new_asins, session, str(out_dir / "detail_cache"))
                if new_asins else [])
            for record in details:
                asin = str(record.get("asin") or "").upper()
                if asin:
                    detail_map[asin] = record
                    detail_asins.add(asin)
            success_asins = {str(r.get("asin") or "").upper() for r in details if r.get("asin")}
            pending_detail_asins.difference_update(success_asins)
            all_details = list(detail_map.values())
            (out_dir / "rankings.json").write_text(
                json.dumps(all_rankings, ensure_ascii=False, indent=2), encoding="utf-8")
            (out_dir / "details.json").write_text(
                json.dumps(all_details, ensure_ascii=False, indent=2), encoding="utf-8")
            done_urls.update(complete_urls)
            if len(complete_urls) != len(urls):
                print("[批处理] %s 来源短缺 %d/%d；未完成来源会在下次运行重试" %
                      (category_name, len(urls) - len(complete_urls), len(urls)))
            done_categories.add(group)
            save_state(None)
            print("[批处理] %s 完成：31–50 榜单 %d 条，新增详情 %d 条" %
                  (category_name, len(target), len(details)))
            if group_index < len(pending_groups) - 1:
                state["cooldown_until"] = time.time() + cooldown
                state["cooldown_category"] = category_name
                save_state(group)
                _batch_countdown_until(float(state["cooldown_until"]), category_name)
                state["cooldown_until"] = None
                state["cooldown_category"] = None
                save_state(None)
    if not pending_groups and not pending_detail_asins:
        print("batch-collect：计划中的待提取来源和详情已全部完成")
    print("batch-collect 完成：榜单 %d 条，详情 %d 条；状态文件 %s" %
          (len(all_rankings), len(all_details), state_path))


# ---------- select-quota（离线） ----------

def cmd_select_quota(args) -> None:
    """根据已采集榜单和审核过的 URL 配置生成 150/50 manifest。"""
    from .collection.quota import annotate_groups, normalize_group, select_quota, validate_category_config

    rankings = _load_json(args.rankings)
    config = _load_json(args.config)
    try:
        rows = validate_category_config(config)
    except ValueError as exc:
        raise SystemExit("%s: %s" % (args.config, exc))
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


def cmd_download_images(args) -> None:
    """按 ASIN 下载缺失原图；串行、可恢复，不调用 DS。"""
    from .collection.images import download_images
    records = _load_json(args.products)
    if not isinstance(records, list):
        raise SystemExit("products JSON 顶层必须是数组: %s" % args.products)
    result = download_images(records, args.out_dir, delay_seconds=args.delay)
    _save_json(result, args.report)
    print("download-images 完成：下载 %d、缓存 %d、失败 %d → %s" %
          (sum(v.get("status") == "downloaded" for v in result.values()),
           sum(v.get("status") == "cached" for v in result.values()),
          sum(v.get("status") == "failed" for v in result.values()), args.report))


def cmd_reconcile_task(args) -> None:
    from .qa.reconcile import reconcile_task
    task = _load_json(args.task)
    items = _load_json(args.items)
    products = _load_json(args.products)
    translations = _load_json(args.translations) if args.translations else []
    report = reconcile_task(task, items, products, translations=translations)
    _save_json(report, args.out)
    print("reconcile-task：%s，目标 %d → %s" %
          (report["status"], report["target_count"], args.out))


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
        if args.repair_partial:
            result = translator.translate_record(product, repair_partial=True)
        else:
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


def cmd_audit_detail_cache(args) -> None:
    """离线：审计保存详情 HTML，识别验证页并生成隔离清单。"""
    from .collection.detail import audit_saved_detail_cache
    from .collection.planning import DetailState
    if args.move and not args.quarantine_dir:
        raise SystemExit("--move 需要同时指定 --quarantine-dir：证据只移动，绝不删除")
    state = DetailState(args.state) if args.state else None
    report = audit_saved_detail_cache(args.html_dir, asins=args.asins or None,
                                      quarantine_dir=args.quarantine_dir or None,
                                      state=state, move=args.move)
    if state:
        state.save()
    _save_json(report, args.out)
    s = report["summary"]
    print("detail-cache-audit：有效 %d、挑战 %d、无效/空 %d → %s" %
          (s["VALID_PRODUCT_PAGE"], s["CHALLENGE"], s["INVALID_OR_EMPTY"], args.out))
    if args.move:
        print("已移出活动缓存 %d 个文件 → %s（原件保留在隔离目录，续采可恢复）"
              % (s.get("removed_from_cache", 0), args.quarantine_dir))


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
    # Field closure may inspect large saved HTML pages and therefore take a few
    # minutes.  Emit an immediate, flushed status line so a long-running audit
    # is distinguishable from a hung process; the final summary remains the
    # authoritative result.
    print("开始字段闭环审查：%d SKU；HTML=%s" %
          (len(products), "已启用" if args.html_dir else "未启用"), flush=True)
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
    details = _load_evidence_json(getattr(args, "details", ""), DEFAULT_DETAILS)
    rankings = _load_evidence_json(getattr(args, "rankings", ""), DEFAULT_RANKINGS)
    closure_enabled = bool(args.translations or details or rankings or
                            getattr(args, "html_dir", None) or getattr(args, "run_dir", ""))
    closure = audit_field_closure(products, details=details, rankings=rankings,
                                  html_dir=getattr(args, "html_dir", None) or None,
                                  run_dir=getattr(args, "run_dir", "") or None,
                                  translations=translations) if closure_enabled else {"records": []}
    if not closure_enabled:
        # 门禁降级必须可见：静默跳过会让导出看起来通过了实际未执行的审计。
        print("警告：未找到 details/rankings/translations 证据，字段闭环门禁未运行；"
              "本次仅执行 QA 门禁")
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
                         prev_workbook=prev_workbook, out_path=args.out,
                         profile=getattr(args, "profile", "research"))
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
    c.add_argument("--profile-dir", default="",
                   help="可选：复用本机 Chrome 用户配置目录（例如 Chrome User Data）")
    c.add_argument("--postal-code", default="28001",
                   help="配送地点检查使用的西班牙邮编（默认 28001，马德里）")
    c.add_argument("--challenge-wait-seconds", type=float, default=180.0,
                   help="遇到挑战页时等待自动恢复的秒数（默认 180；分段轮询）")
    c.add_argument("--manual-assist", action="store_true",
                   help="等待后仍是挑战页时，在 --headful 浏览器中暂停并等待人工接管")
    c.add_argument("--pages-per-url", type=int, default=1,
                   help="每个榜单 URL 依次访问的页数；默认 1，使用 ?pg=N 分页")
    c.add_argument("--rankings-only", action="store_true", help="只采集榜单页，不访问详情页")
    c.add_argument("--rankings-file", default="", help="复用已保存榜单 JSON，仅访问 manifest 中详情")
    c.add_argument("--manifest", default="", help="详情采集 ASIN manifest JSON（与 --rankings-file 配合）")
    c.add_argument("--progress", default="", help="可选：逐 ASIN 写入运行进度 JSON")
    c.set_defaults(func=lambda a, p=c: cmd_collect(a, p))

    bc = sub.add_parser("batch-collect", help="联网：按计划分批采集，类目间保持倒计时冷却并自动续跑")
    bc.add_argument("--plan", required=True, help="来源页提取计划 JSON")
    bc.add_argument("--out-dir", required=True, help="批处理输出目录")
    bc.add_argument("--headful", action="store_true", help="有头浏览器")
    bc.add_argument("--profile-dir", default="", help="可选：复用本机浏览器配置目录")
    bc.add_argument("--postal-code", default="28001", help="配送地点检查使用的西班牙邮编")
    bc.add_argument("--challenge-wait-seconds", type=float, default=180.0,
                    help="挑战页等待人工接管的秒数")
    bc.add_argument("--manual-assist", action="store_true", help="挑战页等待人工处理")
    bc.add_argument("--cooldown-seconds", type=int, default=None,
                    help="类目间冷却秒数；省略时读取计划，默认1800")
    bc.add_argument("--existing-products", default="",
                    help="已有规范化商品 JSON；其中 ASIN 不再重复请求详情")
    bc.add_argument("--existing-details", default="",
                    help="已有详情 JSON；合并写入批处理 details.json")
    bc.add_argument("--seed-rankings", default="",
                    help="已有31–50榜单 JSON；作为已完成来源的证据种子")
    bc.add_argument("--rankings-only", action="store_true", help="只提取榜单，不访问详情页")
    bc.set_defaults(func=lambda a, p=bc: cmd_batch_collect(a, p))

    s = sub.add_parser("select-quota", help="离线：按审核类目配置选择 150/50 唯一 ASIN")
    s.add_argument("--rankings", required=True, help="榜单记录 JSON")
    s.add_argument("--config", required=True, help="类目配置 JSON")
    s.add_argument("--out", required=True, help="配额 manifest JSON")
    s.set_defaults(func=cmd_select_quota)

    im = sub.add_parser("download-images", help="联网：按 ASIN 串行下载缺失原图")
    im.add_argument("--products", required=True, help="商品 JSON 数组")
    im.add_argument("--out-dir", required=True, help="图片缓存目录")
    im.add_argument("--report", required=True, help="下载结果 JSON")
    im.add_argument("--delay", type=float, default=1.0, help="图片请求间隔秒数")
    im.set_defaults(func=cmd_download_images)

    rc = sub.add_parser("reconcile-task", help="离线：对账任务目标与各阶段 ASIN 集合")
    rc.add_argument("--task", required=True)
    rc.add_argument("--items", required=True)
    rc.add_argument("--products", required=True)
    rc.add_argument("--translations", default="")
    rc.add_argument("--out", required=True)
    rc.set_defaults(func=cmd_reconcile_task)

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

    ca = sub.add_parser("audit-detail-cache", help="离线：审计详情 HTML 缓存，不访问 Amazon")
    ca.add_argument("--html-dir", nargs="+", required=True)
    ca.add_argument("--asins", nargs="*", default=[])
    ca.add_argument("--quarantine-dir", default="")
    ca.add_argument("--move", action="store_true",
                    help="把挑战/无效页移出活动缓存（移动不删除，续采才能恢复）")
    ca.add_argument("--state", default="")
    ca.add_argument("--out", required=True)
    ca.set_defaults(func=cmd_audit_detail_cache)

    t = sub.add_parser("translate-ds", help="联网：调用 DeepSeek API 翻译中文显示字段")
    t.add_argument("--products", required=True, help="规范化商品 JSON 数组")
    t.add_argument("--cache", default="", help="翻译缓存 JSON（默认写入 --out）")
    t.add_argument("--out", required=True, help="ASIN → 翻译结果 JSON")
    t.add_argument("--endpoint", default="", help="完整 API endpoint（默认 DeepSeek chat/completions）")
    t.add_argument("--model", default="", help="模型名（默认 deepseek-chat）")
    t.add_argument("--max-retries", type=int, default=2)
    t.add_argument("--backoff-seconds", type=float, default=1.0)
    t.add_argument("--timeout", type=float, default=60.0)
    t.add_argument("--repair-partial", action="store_true",
                   help="已确认调用 API 时，绕过同源 partial 缓存并补翻缺失字段")
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
    x.add_argument("--details", default=DEFAULT_DETAILS,
                   help="详情 raw JSON（默认 outputs/details.json，用于字段闭环门禁）")
    x.add_argument("--rankings", default=DEFAULT_RANKINGS,
                   help="榜单 raw JSON（默认 outputs/rankings.json，用于字段闭环门禁）")
    x.add_argument("--html-dir", nargs="+", default=[], help="保存的详情 HTML 目录（可选）")
    x.add_argument("--run-dir", default="", help="采集 run 根目录（可选）")
    x.add_argument("--prev-workbook", default="", help="前版工作簿（按 ASIN 保留备注）")
    x.add_argument("--images-dir", default="", help="本地图片目录（<ASIN>.png/.jpg/.jpeg）")
    x.add_argument("--category-planning", default="", help="类目规划 JSON（字典行数组或二维数组）")
    x.add_argument("--out", default=str(OUTPUTS / "选品清单.xlsx"))
    x.add_argument("--force", action="store_true",
                   help="跳过 QA 硬门禁（存在 P0/P1 也导出，保留上游证据）")
    x.add_argument("--profile", choices=("research", "business"), default="research",
                   help="research=类目规划+双语三表；business=仅西语/中文两表")
    x.set_defaults(func=cmd_export)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    from .access.detector import AccessStopError
    from .access.location import DeliveryLocationError
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (AccessStopError, DeliveryLocationError) as e:
        # 访问门禁或配送地点无法确认：停止采集，退出码 2
        parser.exit(2, "!! %s\n" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
