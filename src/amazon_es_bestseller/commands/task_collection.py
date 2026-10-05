"""Command handler for the reviewed task-collection entry point.

The scheduler, plan validation, workers, and checkpoints remain owned by
``collection.task``. This adapter owns only CLI-specific JSON loading and the
explicitly documented runtime overrides, so the argparse module does not grow
another business workflow.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def run_task_collection(args: Any, parser: Any, *, project_root: Path) -> dict[str, Any]:
    """Load a reviewed plan and dispatch it to the existing task scheduler."""
    if args.offline:
        parser.error("task-collect 需要联网，不能与 --offline 同用")
    plan_path = Path(args.plan)
    if not plan_path.exists():
        parser.error("找不到任务计划: %s" % args.plan)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        parser.error("任务计划不是有效 JSON: %s" % exc)

    # Runtime access values are deliberately not written back to the reviewed
    # plan. This keeps source-plan evidence immutable across resumes.
    plan = dict(plan)
    if args.postal_code:
        plan["postal_code"] = args.postal_code
    if args.challenge_wait_seconds is not None:
        plan["challenge_wait_seconds"] = args.challenge_wait_seconds
    if args.manual_assist:
        plan["manual_assist"] = True

    from ..collection.task import run_task

    try:
        report = run_task(plan, args.out_dir, mode=args.mode,
                          headful=args.headful, profile_dir=args.profile_dir,
                          plan_path=plan_path, project_root=project_root)
    except ValueError as exc:
        parser.error(str(exc))
    return report


__all__ = ["run_task_collection"]
