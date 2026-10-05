import pytest
import json
from amazon_es_bestseller.cli import main
from amazon_es_bestseller.orchestration.production_run import ProductionRun, ProductionRunError, STAGES


def _handlers(calls):
    def handler(context):
        calls.append(context["stage"])
        return {"status": "READY", "counts": {"records": 1}, "stage": context["stage"]}
    return {stage: handler for stage in STAGES}


def test_offline_run_persists_hash_bound_stages_and_resumes(tmp_path):
    calls = []; run = ProductionRun(tmp_path, run_id="r1", config={"source": "fixture"}, offline=True)
    summary = run.run(_handlers(calls), profile="source-only")
    source_stage_count = STAGES.index("spanish-master") + 1
    assert summary["status"] == "DRAFT_SOURCE_ONLY" and len(calls) == source_stage_count
    assert run.run(_handlers(calls), profile="source-only")["status"] == "DRAFT_SOURCE_ONLY" and len(calls) == source_stage_count
    assert (tmp_path / "runmanifest.json").exists() and (tmp_path / "progress.json").exists()


def test_resume_rejects_changed_predecessor_and_source_only_is_nonformal(tmp_path):
    calls = []; run = ProductionRun(tmp_path, run_id="r1", config={"source": "fixture"})
    assert run.run(_handlers(calls), profile="source-only")["status"] == "DRAFT_SOURCE_ONLY"
    changed = ProductionRun(tmp_path, run_id="r1", config={"source": "changed"})
    with pytest.raises(ProductionRunError, match="RESUME_FINGERPRINT_MISMATCH"):
        changed.run(_handlers([]), from_stage="source-audit")


def test_empty_or_missing_stage_cannot_be_ready(tmp_path):
    run = ProductionRun(tmp_path, run_id="r1", config={})
    with pytest.raises(ProductionRunError, match="STAGE_HANDLER_MISSING:preflight"):
        run.run({})
    with pytest.raises(ProductionRunError, match="STAGE_NOT_READY:preflight"):
        run.run({"preflight": lambda _ctx: {}})
    with pytest.raises(ProductionRunError, match="RELEASE_ARTIFACTS_MISSING"):
        ProductionRun(tmp_path / "full", run_id="r2", config={}).run(_handlers([]))


def test_cli_runs_fixture_only_and_registers_resume_controls(tmp_path):
    config = tmp_path / "fixture.json"; run_dir = tmp_path / "run"
    config.write_text(json.dumps({"stages": {stage: {"status": "READY", "counts": {"fixture": 1}}
                                             for stage in STAGES}}), encoding="utf-8")
    assert main(["--offline", "production-run", "--run-dir", str(run_dir), "--run-id", "fixture",
                 "--config", str(config), "--resume", "--profile", "source-only"]) == 0
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["status"] == "DRAFT_SOURCE_ONLY"
