import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from test_link_first_runner import runner

ENTRY = Path(__file__).resolve().parents[1] / "tools/parallel_link_coverage.py"


def load_entry():
    spec = importlib.util.spec_from_file_location("parallel_link_coverage", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parallel_switch_preserves_cooldown_and_rejects_access_hold(tmp_path, monkeypatch):
    module = load_entry()
    origin = runner(tmp_path, monkeypatch)
    origin.save("COOLDOWN", cooldown_until=12345, last_category="a")
    new_plan = json.loads(origin.plan_path.read_text())
    new_plan["scheduler"].update(mode="parallel3", cooldown_after_category_seconds=1800)
    plan_path = tmp_path / "parallel_plan.json"
    plan_path.write_text(json.dumps(new_plan))
    result = module.freeze_origin(origin.output, tmp_path / "parallel", plan_path)
    assert result["origin_state"]["cooldown_until"] == 12345
    assert result["detail_results"] == {}
    assert result["translation"] == "disabled"
    origin.save("ACCESS_STOP", hold_token="human")
    with pytest.raises(ValueError, match="ORIGIN_INPUT_DRIFT"):
        module.freeze_origin(origin.output, tmp_path / "parallel", plan_path)
    with pytest.raises(ValueError, match="PARALLEL_SWITCH_REQUIRES_SAFE_PAUSE"):
        module.freeze_origin(origin.output, tmp_path / "another", plan_path)


def test_parallel_switch_does_not_allow_source_or_quota_change(tmp_path, monkeypatch):
    module = load_entry()
    origin = runner(tmp_path, monkeypatch)
    origin.save("COOLDOWN")
    new_plan = json.loads(origin.plan_path.read_text())
    new_plan["categories"][0]["target_unique"] += 1
    plan_path = tmp_path / "parallel_plan.json"
    plan_path.write_text(json.dumps(new_plan))
    with pytest.raises(ValueError, match="PARALLEL_SWITCH_SCOPE_DRIFT"):
        module.freeze_origin(origin.output, tmp_path / "parallel", plan_path)


def test_worker_cooldown_is_persisted_and_not_skipped(tmp_path, monkeypatch):
    module = load_entry()
    batch = module.ParallelCoverage.__new__(module.ParallelCoverage)
    batch.worker_states = {1: {"cooldown_until": 110}}
    batch.ready = asyncio.Event()
    batch.ready.set()
    batch.holds, batch.token = {}, ""
    batch.progress = lambda *args: None
    ticks = [100.0]
    batch.now = lambda: ticks[0]
    async def advance(seconds):
        ticks[0] += seconds
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    asyncio.run(batch.wait_cooldown(1))
    assert ticks[0] == 110
    assert batch.worker_states[1]["status"] == "READY"


def test_parallel_progress_uses_reviewed_target_not_previous_500(tmp_path):
    from types import SimpleNamespace
    module = load_entry()
    batch = module.ParallelCoverage.__new__(module.ParallelCoverage)
    batch.output = tmp_path
    batch.worker_states = {}
    batch.token, batch.holds = "", {}
    batch.runner = SimpleNamespace(
        state={"status": "PREPARED", "phase": "FULL", "detail_results": {}},
        plan={"target_unique": 377}, selected={}, records={}, links=[], save=lambda *args: None)
    batch.progress()
    assert json.loads((tmp_path / "parallel_progress.json").read_text())["target_unique"] == 377
