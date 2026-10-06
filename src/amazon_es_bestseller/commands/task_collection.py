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

    # Runtime access values are deliberately kept outside the reviewed plan.
    # Formal-plan fingerprint validation must always use the exact JSON that
    # was reviewed, before a local CLI default such as --postal-code is added.
    runtime_overrides: dict[str, Any] = {}
    if args.postal_code:
        runtime_overrides["postal_code"] = args.postal_code
    if args.challenge_wait_seconds is not None:
        runtime_overrides["challenge_wait_seconds"] = args.challenge_wait_seconds
    if args.manual_assist:
        runtime_overrides["manual_assist"] = True

    from ..collection.task import run_task

    try:
        report = run_task(plan, args.out_dir, mode=args.mode,
                          headful=args.headful, profile_dir=args.profile_dir,
                          plan_path=plan_path, project_root=project_root,
                          phase=args.phase, resume=bool(args.resume),
                          runtime_overrides=runtime_overrides,
                          previous_details=getattr(args, "previous_details", "") or None)
    except ValueError as exc:
        parser.error(str(exc))
    return report


__all__ = ["run_task_collection"]
