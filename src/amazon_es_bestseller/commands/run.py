"""CLI handler for the real, evidence-driven Production V1 runner."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..orchestration.production_run import ProductionRun
from ..orchestration.task_config import TaskConfig, TaskConfigError
from ..orchestration.workflow import ProductionWorkflow, ProductionWorkflowError


def run_production(args: Any) -> dict[str, Any]:
    """Build concrete stage adapters; task JSON cannot provide stage payloads."""
    config_path = Path(args.config)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("TASK_CONFIG_INVALID:%s" % config_path) from exc
    try:
        task = TaskConfig.from_mapping(raw, base_dir=config_path.parent)
        workflow = ProductionWorkflow(task, args.run_dir, run_id=args.run_id)
        runner = ProductionRun(args.run_dir, run_id=args.run_id,
                               config=task.runner_config(), schema_version=args.schema_version,
                               offline=bool(args.offline))
        return runner.run(workflow.handlers(), from_stage=args.from_stage or None,
                          profile=args.profile)
    except (TaskConfigError, ProductionWorkflowError) as exc:
        raise SystemExit(str(exc)) from exc


__all__ = ["run_production"]
