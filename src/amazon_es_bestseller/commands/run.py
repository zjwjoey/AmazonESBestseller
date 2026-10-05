"""CLI handler for the real, evidence-driven Production V1 runner."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..orchestration.live_runtime import (LiveRuntimeError, ReviewedV1Transport,
                                          build_qwen_provider, load_live_transport_scope)
from ..orchestration.production_run import STAGES, ProductionRun
from ..orchestration.task_config import TaskConfig, TaskConfigError
from ..orchestration.translation_batch import (TranslationBatchError, create_selection_manifest,
                                               write_selection_manifest)
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
        run_manifest = Path(args.run_dir) / "runmanifest.json"
        if args.from_stage and not bool(args.resume):
            raise TaskConfigError("FROM_STAGE_REQUIRES_RESUME")
        if run_manifest.exists() and not bool(args.resume):
            raise TaskConfigError("RUN_ALREADY_EXISTS_USE_RESUME")
        if bool(args.resume) and not run_manifest.exists():
            raise TaskConfigError("RUN_RESUME_MANIFEST_MISSING")
        if task.network_mode == "live":
            if bool(args.offline):
                raise LiveRuntimeError("LIVE_TRANSPORT_FORBIDDEN_OFFLINE")
            if not bool(getattr(args, "allow_live_transport", False)):
                raise LiveRuntimeError("LIVE_TRANSPORT_NOT_AUTHORIZED")
            scope = load_live_transport_scope(task, config_dir=config_path.parent)
            runtime_config = {**task.runner_config(), "live_transport_scope": {
                "reviewed_plan_hash": scope.reviewed_plan_hash,
                "target_unique": scope.target_unique,
                "category_count": scope.category_count,
                "source_counts": dict(scope.source_counts),
                "max_ranking_pages": scope.max_ranking_pages,
                "max_detail_requests": scope.max_detail_requests,
            }}
            provider = (build_qwen_provider(
                task, run_dir=args.run_dir,
                allow_qwen_translation=bool(getattr(args, "allow_qwen_translation", False)),
                offline=False,
            ) if args.profile == "full" else None)
            # A hash-verified continuation from offline-reparse onward must
            # not reopen Amazon merely to reach translation/QA/release.  The
            # runner independently verifies every skipped V1 producer stage.
            transport_required = (not args.from_stage
                                  or STAGES.index(args.from_stage) <= STAGES.index("detail-evidence"))
            if transport_required:
                with ReviewedV1Transport(task, scope) as transport:
                    workflow = ProductionWorkflow(
                        task, args.run_dir, run_id=args.run_id,
                        snapshot_collector=transport.snapshot_collector,
                        detail_collector=transport.detail_collector,
                        detail_session=transport.session,
                        translation_provider=provider,
                    )
                    runner = ProductionRun(args.run_dir, run_id=args.run_id,
                                           config=runtime_config, schema_version=args.schema_version,
                                           offline=False)
                    return runner.run(workflow.handlers(), from_stage=args.from_stage or None,
                                      profile=args.profile)
            else:
                workflow = ProductionWorkflow(
                    task, args.run_dir, run_id=args.run_id,
                    translation_provider=provider,
                )
                runner = ProductionRun(args.run_dir, run_id=args.run_id,
                                       config=runtime_config, schema_version=args.schema_version,
                                       offline=False)
                return runner.run(workflow.handlers(), from_stage=args.from_stage or None,
                                  profile=args.profile)
        workflow = ProductionWorkflow(task, args.run_dir, run_id=args.run_id)
        runner = ProductionRun(args.run_dir, run_id=args.run_id,
                               config=task.runner_config(), schema_version=args.schema_version,
                               offline=bool(args.offline))
        return runner.run(workflow.handlers(), from_stage=args.from_stage or None,
                          profile=args.profile)
    except (TaskConfigError, ProductionWorkflowError, LiveRuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


def create_translation_selection(args: Any) -> dict[str, Any]:
    """Create the reviewed <=1500-ASIN link between a source run and Qwen."""
    raw = str(args.asins or "").strip()
    source = Path(raw)
    try:
        if source.is_file():
            value = json.loads(source.read_text(encoding="utf-8"))
            asins = value.get("asins") if isinstance(value, dict) else value
        else:
            asins = [item.strip() for item in raw.replace("\n", ",").split(",")]
        if not isinstance(asins, list):
            raise TranslationBatchError("TRANSLATION_SELECTION_ASINS_LIST_REQUIRED")
        manifest = create_selection_manifest(master_artifact_path=args.master_artifact,
                                              selected_asins=asins,
                                              selection_id=str(args.selection_id or ""))
        path = write_selection_manifest(args.out, manifest)
    except TranslationBatchError as exc:
        raise SystemExit(str(exc)) from exc
    return {**manifest, "path": str(path)}


__all__ = ["create_translation_selection", "run_production"]
