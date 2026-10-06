# -*- coding: utf-8 -*-
"""Compatibility facade for the reviewed category-scale task runner.

The task implementation now lives under :mod:`amazon_es_bestseller.orchestration`:
plan validation, versioned task state, one-category workers, bounded scheduling,
and derived manifests have separate owners.  This module deliberately preserves
the historical public imports and monkeypatch seam used by the CLI and offline
tests; it must not acquire new business logic.
"""
from __future__ import annotations

# Existing tests patch these module attributes while exercising the shared
# atomic writer. Keep the names in this facade as a compatibility boundary.
import os
from pathlib import Path
import time
from typing import Callable, Mapping

from ..orchestration.checkpoint import TaskCheckpointRepository, read_json as _read_json
from ..orchestration.checkpoint import write_json_atomic as _write_json_atomic
from ..orchestration.manifest import merge_records as _merge_records
from ..orchestration.manifest import write_summary as _write_summary
from ..orchestration.plan import category_rank_filter as _category_rank_filter
from ..orchestration.plan import cooldown_seconds as _cooldown_seconds
from ..orchestration.plan import default_project_root as _default_project_root
from ..orchestration.plan import needs_reserve_sources as _needs_reserve_sources
from ..orchestration.plan import normalize_url as _normalize_url
from ..orchestration.plan import resolve_task_path
from ..orchestration.plan import source_urls as _source_urls
from ..orchestration.plan import validate_task_plan
from ..orchestration.scheduler import run_reviewed_task
from ..orchestration.worker import run_category_live


def _category_store(category_dir: Path, name: str):
    return TaskCheckpointRepository(category_dir.parent).category_store(category_dir, name)


def _load_category_mapping(category_dir: Path, name: str, legacy_name: str) -> dict:
    return TaskCheckpointRepository(category_dir.parent).load_category_mapping(category_dir, name, legacy_name)


def _load_category_records(category_dir: Path, name: str, legacy_name: str) -> list[dict]:
    return TaskCheckpointRepository(category_dir.parent).load_category_records(category_dir, name, legacy_name)


def _run_category_live(category: Mapping, plan: Mapping, output: Path,
                       worker_id: int, headful: bool, profile_dir: str,
                       claim_asins: Callable[[list[str]], list[str]],
                       release_asins: Callable[[list[str]], None],
                       completed_urls: set[str],
                       stop_event=None, challenge_pause=None) -> dict:
    """Legacy worker seam delegating to the extracted single-category worker."""
    return run_category_live(category, plan, output, worker_id, headful, profile_dir,
                             claim_asins, release_asins, completed_urls, stop_event,
                             challenge_pause)


def run_task(plan: Mapping, out_dir: str, mode: str | None = None,
             headful: bool = False, profile_dir: str = "",
             plan_path: str | Path | None = None,
             project_root: str | Path | None = None, phase: str = "all",
             resume: bool = False, runtime_overrides: Mapping | None = None,
             previous_details: str | Path | None = None) -> dict:
    """Run through the extracted scheduler while retaining the old patch point."""
    return run_reviewed_task(plan, out_dir, mode=mode, headful=headful,
                             profile_dir=profile_dir, plan_path=plan_path,
                             project_root=project_root, worker=_run_category_live,
                             phase=phase, resume=resume,
                             runtime_overrides=runtime_overrides,
                             previous_details=previous_details)


__all__ = ["_category_rank_filter", "_category_store", "_cooldown_seconds",
           "_default_project_root", "_load_category_mapping", "_load_category_records",
           "_merge_records", "_needs_reserve_sources", "_normalize_url", "_read_json",
           "_run_category_live", "_source_urls", "_write_json_atomic", "_write_summary",
           "resolve_task_path", "run_task", "validate_task_plan"]
