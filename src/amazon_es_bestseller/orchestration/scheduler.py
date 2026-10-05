"""Reviewed serial and bounded-three-slot category scheduling."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
import threading
import time
from typing import Callable, Mapping

from ..collection.quota import QuotaError, select_research_quota
from .checkpoint import TaskCheckpointRepository, read_json, write_json_atomic
from .manifest import merge_records, write_summary
from .plan import cooldown_seconds, validate_task_plan
from .state import TaskRuntimeState
from .worker import run_category_live


def run_reviewed_task(plan: Mapping, out_dir: str, mode: str | None = None,
                      headful: bool = False, profile_dir: str = "",
                      plan_path: str | Path | None = None,
                      project_root: str | Path | None = None,
                      worker: Callable | None = None) -> dict:
    """Run one reviewed plan without widening its scheduling or access policy.

    ``worker`` is injectable only to retain the long-standing offline/CLI test
    seam. Production callers use the extracted serial category worker.
    """
    plan = validate_task_plan(plan, plan_path=plan_path, project_root=project_root)
    scheduler = plan["scheduler"]
    mode = mode or scheduler["mode"]
    if mode not in {"parallel3", "serial"}:
        raise ValueError("mode 必须是 parallel3 或 serial")
    if mode == "parallel3" and profile_dir:
        raise ValueError("parallel3 不接受共享浏览器 profile_dir；请使用独立会话或串行模式")
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    repository = TaskCheckpointRepository(output)
    categories = plan["categories"]
    category_map = {row["research_category"]: row for row in categories}
    slots = 3 if mode == "parallel3" else 1
    runtime = TaskRuntimeState.load(repository, plan, mode=mode, slots=slots)
    force_reserve_sources = runtime.raw.get("run_status") == "QUOTA_UNIQUE_SHORTFALL"
    plan = {**plan, "force_reserve_sources": force_reserve_sources}
    pending = [group for group in category_map
               if runtime.category_states.get(group, {}).get("status") != "COMPLETE"
               or (force_reserve_sources and runtime.has_unfinished_reserve(group, category_map[group]))]
    all_rankings = read_json(output / "rankings.json", [])
    all_details = read_json(output / "details.json", [])
    worker = worker or run_category_live
    runtime.save("RUNNING", [])
    stop_all = False
    stop_event = threading.Event()
    challenge_pause = threading.Event()
    last_cooldown_log = [0.0] * slots

    def run_one(group: str, slot: int):
        try:
            args = (category_map[group], plan, output, slot + 1, headful, profile_dir,
                    runtime.claim_asins, runtime.release_asins,
                    set(runtime.completed_source_urls), stop_event)
            if plan.get("manual_assist"):
                return worker(*args, challenge_pause=challenge_pause)
            # Test and legacy adapters predate the optional cooperative hook.
            return worker(*args)
        except Exception as exc:
            from ..access.detector import AccessStopError
            if isinstance(exc, AccessStopError):
                stop_event.set()
            raise

    with ThreadPoolExecutor(max_workers=slots, thread_name_prefix="amazon-es-category") as executor:
        futures: dict[Future, tuple[int, str]] = {}
        while pending or futures:
            now = time.time()
            for slot in range(slots):
                remaining = runtime.worker_ready_at[slot] - now
                if remaining > 0 and now - last_cooldown_log[slot] >= 10:
                    print("[任务][槽位%d] 冷却中，剩余 %.0f 秒" % (slot + 1, remaining))
                    last_cooldown_log[slot] = now
            for slot in range(slots):
                if stop_all or any(existing_slot == slot for existing_slot, _ in futures.values()):
                    continue
                if not pending or runtime.worker_ready_at[slot] > now:
                    continue
                group = pending.pop(0)
                previous = dict(runtime.category_states.get(group, {}))
                runtime.category_states[group] = {**previous, "status": "RUNNING", "worker": slot + 1,
                                                  "attempts": int(previous.get("attempts", 0)) + 1}
                future = executor.submit(run_one, group, slot)
                futures[future] = (slot, group)
            runtime.save("ACCESS_STOP" if stop_all else "RUNNING",
                         [group for _, group in futures.values()])
            if not futures:
                if pending:
                    delay = max(0.0, min(runtime.worker_ready_at) - time.time())
                    if delay:
                        print("[任务] 等待下一工作槽冷却 %.0f 秒" % delay)
                        time.sleep(min(delay, 1.0))
                    continue
                break
            done, _ = wait(list(futures), timeout=0.5, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                slot, group = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    from ..access.detector import AccessStopError
                    runtime.category_states[group] = {**runtime.category_states.get(group, {}), "status":
                                                      "ACCESS_STOP" if isinstance(exc, AccessStopError) else "FAILED",
                                                      "error": str(exc)}
                    if isinstance(exc, AccessStopError):
                        stop_all = True
                        stop_event.set()
                    elif runtime.category_states[group].get("attempts", 1) < 2:
                        runtime.category_states[group]["status"] = "PENDING"
                        pending.insert(0, group)
                    continue
                all_rankings.extend(result.get("rankings", []))
                all_details.extend(result.get("details", []))
                runtime.category_states[group] = {
                    **runtime.category_states.get(group, {}), "status": "COMPLETE",
                    "raw_ranking_records": result.get("raw_ranking_records", 0),
                    "unique_asins": result.get("unique_asins", 0),
                    "detail_records": result.get("detail_records", 0),
                    "pending_detail_asins": result.get("pending_detail_asins", []),
                    "source_status": result.get("source_status", {}),
                }
                runtime.completed_source_urls.extend(result.get("completed_source_urls", []))
                merge_records(output, all_rankings, all_details)
                runtime.worker_ready_at[slot] = time.time() + cooldown_seconds(mode, scheduler)
                if result.get("status") != "COMPLETE":
                    runtime.category_states[group]["status"] = result.get("status", "DETAIL_INCOMPLETE")
                    if runtime.category_states[group].get("attempts", 1) < 2:
                        runtime.category_states[group]["status"] = "PENDING"
                        pending.append(group)
                print("[%s][槽位%d] 完成，榜单%d条，详情%d条" %
                      (group, slot + 1, result.get("raw_ranking_records", 0),
                       result.get("detail_records", 0)))

    merge_records(output, all_rankings, all_details)
    all_rankings = read_json(output / "rankings.json", [])
    all_details = read_json(output / "details.json", [])
    selected = None
    selected_rows = []
    missing_detail_asins = []
    run_status = "ACCESS_STOP" if stop_all else "INCOMPLETE"
    try:
        selected = select_research_quota(all_rankings, categories, plan["target_unique"])
        selected_rows = [row for group in selected.values() for row in group]
        selected_asins = {str(row.get("asin") or "").upper() for row in selected_rows if row.get("asin")}
        detail_asins = {str(row.get("asin") or "").upper() for row in all_details if row.get("asin")}
        missing_detail_asins = sorted(selected_asins - detail_asins)
        incomplete_groups = [group for group in category_map
                             if runtime.category_states.get(group, {}).get("status") != "COMPLETE"]
        if stop_all:
            run_status = "ACCESS_STOP"
        elif missing_detail_asins:
            run_status = "DETAIL_INCOMPLETE"
            print("[任务] 详情仍缺失 %d 个 ASIN，未通过完成 Gate" % len(missing_detail_asins))
        elif incomplete_groups:
            run_status = "INCOMPLETE"
            print("[任务] 类目仍未完成：%s" % ", ".join(incomplete_groups))
        else:
            run_status = "COMPLETE"
        write_json_atomic(output / "candidate_manifest.json", selected_rows)
        write_json_atomic(output / "final_manifest.json", selected_rows if run_status == "COMPLETE" else [])
    except QuotaError as exc:
        write_json_atomic(output / "final_manifest.json", [])
        run_status = "QUOTA_UNIQUE_SHORTFALL" if not stop_all else run_status
        print("[任务] 尚未通过最终配额 Gate：%s" % exc)
    write_summary(output, categories, all_rankings, all_details, selected, runtime.category_states)
    report = {
        "schema_version": 1, "task_id": plan["task_id"], "mode": mode,
        "run_status": run_status, "target_unique": plan["target_unique"],
        "ranking_records": len(all_rankings),
        "unique_ranking_asins": len({str(row.get("asin") or "").upper() for row in all_rankings if row.get("asin")}),
        "detail_records": len({str(row.get("asin") or "").upper() for row in all_details if row.get("asin")}),
        "missing_detail_asins": missing_detail_asins,
        "final_unique_asins": len({str(row.get("asin") or "").upper()
                                    for row in (selected_rows if run_status == "COMPLETE" else [])
                                    if row.get("asin")}),
        "categories": runtime.category_states,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json_atomic(output / "run_report.json", report)
    runtime.save(run_status, [])
    return report


__all__ = ["run_reviewed_task"]
