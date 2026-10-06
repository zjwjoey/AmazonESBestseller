import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.orchestration.detail_code_migration import (
    DETAIL_RUNTIME_FILES, apply_approved_detail_code_migration, detail_runtime_sha256,
)
from amazon_es_bestseller.orchestration.state import TaskRuntimeState


OLD = "a" * 40
NEW = "b" * 40
PLAN = "c" * 64
CANDIDATE = "d" * 64
TASK = "migration-task"


def _candidates():
    return [{"asin": "A%09d" % index} for index in range(5500)]


def _runtime(*, old=OLD, plan=PLAN, candidate=CANDIDATE):
    saved = []
    return SimpleNamespace(
        raw={"run_status": "DETAIL_INCOMPLETE", "active_workers": []},
        category_states={},
        phase_fingerprints={"detail": {
            "phase": "detail", "code_head": old, "plan_sha256": plan,
            "candidate_manifest_sha256": candidate, "schema_version": 1,
        }},
        save=lambda status, workers: saved.append((status, workers)),
        saved=saved,
    )


def _registry(root: Path, *, old=OLD, new=NEW, plan=PLAN, candidate=CANDIDATE):
    for relative in DETAIL_RUNTIME_FILES:
        file = root / relative; file.parent.mkdir(parents=True, exist_ok=True); file.write_text("runtime\n", encoding="utf-8")
    runtime_sha = detail_runtime_sha256(root)
    path = root / "configs" / "tasks"
    path.mkdir(parents=True)
    (path / "detail_code_migrations.json").write_text(json.dumps({TASK: [{
        "phase": "detail", "from_code_head": old, "target_detail_runtime_sha256": runtime_sha,
        "reason": "TEST_UPGRADE", "plan_sha256": plan,
        "candidate_manifest_sha256": candidate,
    }]}), encoding="utf-8")


def _call(tmp_path, runtime, *, current=NEW, allow=True, candidates=None,
          plan=PLAN, candidate=CANDIDATE, ranking="COMPLETE", snapshot="AUTHORITATIVE"):
    return apply_approved_detail_code_migration(
        output=tmp_path / "run", runtime=runtime,
        plan={"task_id": TASK, "canonical_plan_sha256": plan},
        candidates=_candidates() if candidates is None else candidates,
        candidate_hash=candidate, ranking_report={"status": ranking},
        snapshot_manifest={"snapshot_status": snapshot}, current_code_head=current,
        project_root=tmp_path, allow_approved_code_migration=allow,
    )


def test_default_code_mismatch_remains_blocked(tmp_path):
    _registry(tmp_path)
    runtime = _runtime()
    assert _call(tmp_path, runtime, allow=False) is False
    assert runtime.phase_fingerprints["detail"]["code_head"] == OLD
    # bind_phase retains the production default failure once migration did not
    # explicitly happen.
    state = object.__new__(TaskRuntimeState)
    state.phase_fingerprints = runtime.phase_fingerprints
    with pytest.raises(ValueError, match="CODE_FINGERPRINT_MISMATCH"):
        state.bind_phase("detail", plan_sha256=PLAN,
                         candidate_manifest_sha256=CANDIDATE, code_head=NEW)


def test_approved_detail_migration_writes_artifact_and_is_idempotent(tmp_path):
    _registry(tmp_path)
    runtime = _runtime()
    assert _call(tmp_path, runtime) is True
    artifact = json.loads((tmp_path / "run" / "detail_code_migration.json").read_text(encoding="utf-8"))
    assert artifact["from_code_head"] == OLD
    assert artifact["observed_to_code_head"] == NEW
    assert artifact["observed_detail_runtime_sha256"] == detail_runtime_sha256(tmp_path)
    assert artifact["candidate_count"] == 5500
    assert artifact["candidate_manifest_sha256"] == CANDIDATE
    fingerprint = runtime.phase_fingerprints["detail"]
    assert fingerprint["code_head"] == NEW
    assert fingerprint["previous_code_head"] == OLD
    assert fingerprint["migration_count"] == 1
    assert runtime.saved == [("DETAIL_INCOMPLETE", [])]
    state = object.__new__(TaskRuntimeState)
    state.phase_fingerprints = runtime.phase_fingerprints
    state.bind_phase("detail", plan_sha256=PLAN,
                     candidate_manifest_sha256=CANDIDATE, code_head=NEW)
    assert state.phase_fingerprints["detail"]["detail_runtime_sha256"] == artifact[
        "observed_detail_runtime_sha256"]
    assert _call(tmp_path, runtime) is False
    assert runtime.saved == [("DETAIL_INCOMPLETE", [])]


def test_wrong_old_head_fails_closed_and_observed_head_is_not_an_approval_target(tmp_path):
    _registry(tmp_path)
    with pytest.raises(ValueError, match="CODE_FINGERPRINT_MISMATCH"):
        _call(tmp_path, _runtime(old="e" * 40))
    runtime = _runtime()
    assert _call(tmp_path, runtime, current="f" * 40) is True
    assert runtime.phase_fingerprints["detail"]["code_head"] == "f" * 40


def test_wrong_runtime_fingerprint_fails_closed(tmp_path):
    _registry(tmp_path)
    runtime_file = tmp_path / "src/amazon_es_bestseller/orchestration/worker.py"
    runtime_file.write_text("runtime changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DETAIL_RUNTIME_FINGERPRINT_MISMATCH"):
        _call(tmp_path, _runtime())


@pytest.mark.parametrize(("runtime_plan", "registry_plan", "candidate", "error"), [
    ("e" * 64, PLAN, CANDIDATE, "PLAN_FINGERPRINT_MISMATCH"),
    (PLAN, "e" * 64, CANDIDATE, "PLAN_FINGERPRINT_MISMATCH"),
    (PLAN, PLAN, "e" * 64, "CANDIDATE_FINGERPRINT_MISMATCH"),
])
def test_migration_rejects_plan_or_candidate_drift(tmp_path, runtime_plan, registry_plan,
                                                    candidate, error):
    _registry(tmp_path, plan=registry_plan)
    runtime = _runtime(plan=runtime_plan)
    with pytest.raises(ValueError, match=error):
        _call(tmp_path, runtime, candidate=candidate)


def test_migration_requires_frozen_quota_ranking_and_snapshot(tmp_path):
    _registry(tmp_path)
    with pytest.raises(ValueError, match="CANDIDATE_QUOTA_NOT_MET"):
        _call(tmp_path, _runtime(), candidates=_candidates()[:-1])
    with pytest.raises(ValueError, match="DETAIL_REQUIRES_COMPLETE_RANKING"):
        _call(tmp_path, _runtime(), ranking="DETAIL_INCOMPLETE")
    with pytest.raises(ValueError, match="DETAIL_REQUIRES_AUTHORITATIVE_SNAPSHOT"):
        _call(tmp_path, _runtime(), snapshot="INCOMPLETE")


def test_migration_rejects_conflicting_existing_artifact(tmp_path):
    _registry(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    (run / "detail_code_migration.json").write_text(json.dumps({"from_code_head": "wrong"}), encoding="utf-8")
    with pytest.raises(ValueError, match="DETAIL_CODE_MIGRATION_CONFLICT"):
        _call(tmp_path, _runtime())


def test_non_detail_phase_has_no_migration_escape_hatch(tmp_path):
    _registry(tmp_path)
    state = object.__new__(TaskRuntimeState)
    state.phase_fingerprints = {"ranking": {
        "phase": "ranking", "code_head": OLD, "plan_sha256": PLAN,
        "candidate_manifest_sha256": "", "schema_version": 1,
    }}
    with pytest.raises(ValueError, match="CODE_FINGERPRINT_MISMATCH"):
        state.bind_phase("ranking", plan_sha256=PLAN, code_head=NEW)


def test_runtime_fingerprint_is_path_newline_and_nonruntime_stable(tmp_path):
    other = tmp_path / "other"
    for root, newline in ((tmp_path, "\n"), (other, "\r\n")):
        for relative in DETAIL_RUNTIME_FILES:
            file = root / relative; file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(("value = 1" + newline).encode())
        (root / "docs").mkdir(exist_ok=True)
        (root / "docs" / "note.md").write_text("different", encoding="utf-8")
    assert detail_runtime_sha256(tmp_path) == detail_runtime_sha256(other)
    baseline = detail_runtime_sha256(tmp_path)
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_migration.py").write_text("changed", encoding="utf-8")
    registry = tmp_path / "configs" / "tasks"
    registry.mkdir(parents=True)
    (registry / "detail_code_migrations.json").write_text("{}", encoding="utf-8")
    assert detail_runtime_sha256(tmp_path) == baseline
    target = tmp_path / DETAIL_RUNTIME_FILES[0]
    target.write_text("value = 2\n", encoding="utf-8")
    assert detail_runtime_sha256(tmp_path) != detail_runtime_sha256(other)
