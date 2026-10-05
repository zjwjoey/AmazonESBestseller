"""Small fail-closed runner for the Production V1 artifact graph.

Business stages remain owned by their existing modules.  This layer only
persists the stage graph, fingerprints, progress and failures so a resumed run
cannot silently consume stale evidence.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..runtime_state import atomic_write_json
from ..run_manifest import create_manifest, finalize_manifest, load_manifest, update_manifest, write_manifest

STAGES = (
    "preflight", "ranking-authority", "detail-evidence", "offline-reparse",
    "normalize", "source-audit", "spanish-master", "translation-input",
    "preclean", "dictionary", "translation", "dictionary-rerender", "chinese-qa",
    "field-repair", "re-qa", "field-closure", "release", "excel",
)
SOURCE_ONLY_LAST = "spanish-master"


class ProductionRunError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def artifact_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class ProductionRun:
    def __init__(self, directory: str | Path, *, run_id: str, config: Mapping[str, Any],
                 schema_version: str = "production-v1", offline: bool = True) -> None:
        self.directory = Path(directory)
        self.run_id, self.config = str(run_id), dict(config)
        self.schema_version, self.offline = str(schema_version), bool(offline)
        self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path: return self.directory / "runmanifest.json"
    @property
    def progress_path(self) -> Path: return self.directory / "progress.json"
    @property
    def summary_path(self) -> Path: return self.directory / "summary.json"
    @property
    def errors_path(self) -> Path: return self.directory / "errors.jsonl"
    @property
    def metrics_path(self) -> Path: return self.directory / "run_manifest.json"

    def _load(self, path: Path, default: Any) -> Any:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def _write(self, path: Path, value: Any) -> None:
        atomic_write_json(path, value, sort_keys=True)

    def _manifest(self) -> dict[str, Any]:
        manifest = self._load(self.manifest_path, {})
        if manifest and (manifest.get("run_id") != self.run_id or manifest.get("schema_version") != self.schema_version):
            raise ProductionRunError("RUN_MANIFEST_ID_OR_SCHEMA_MISMATCH")
        return manifest or {"run_id": self.run_id, "schema_version": self.schema_version,
                            "created_at": _now(), "offline": self.offline,
                            "config_hash": artifact_hash(self.config), "stages": {}}

    def _fingerprint(self, stage: str, manifest: Mapping[str, Any]) -> str:
        prior = manifest.get("stages") or {}
        upstream = {name: row.get("artifact_hash") for name, row in prior.items()
                    if isinstance(row, Mapping) and STAGES.index(name) < STAGES.index(stage)}
        return artifact_hash({"stage": stage, "config": self.config, "schema": self.schema_version,
                              "offline": self.offline, "upstream": upstream})

    def _record_error(self, stage: str, code: str, detail: str = "") -> None:
        with self.errors_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": _now(), "stage": stage, "code": code, "detail": detail}, ensure_ascii=False) + "\n")

    def _update_metrics(self, payload: Mapping[str, Any]) -> None:
        """Publish stable operational counters from actual stage producers."""
        if self.metrics_path.exists():
            metrics = load_manifest(self.metrics_path)
        else:
            metrics = create_manifest(self.run_id, status="running",
                                      config_hash=artifact_hash(self.config))
        updates = dict(payload.get("manifest_counts") or {})
        if updates:
            metrics = update_manifest(metrics, **updates)
        write_manifest(metrics, self.metrics_path)

    def run(self, handlers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]], *,
            from_stage: str | None = None, profile: str = "full") -> dict[str, Any]:
        if profile not in {"full", "source-only"}: raise ProductionRunError("PROFILE_INVALID")
        if from_stage and from_stage not in STAGES: raise ProductionRunError("FROM_STAGE_INVALID")
        manifest = self._manifest(); completed = manifest["stages"]
        start = STAGES.index(from_stage) if from_stage else 0
        end = STAGES.index(SOURCE_ONLY_LAST) if profile == "source-only" else len(STAGES) - 1
        for index, stage in enumerate(STAGES[:end + 1]):
            fingerprint = self._fingerprint(stage, manifest)
            old = completed.get(stage)
            if index < start:
                if not isinstance(old, Mapping) or old.get("status") != "READY" or old.get("fingerprint") != fingerprint:
                    raise ProductionRunError("RESUME_FINGERPRINT_MISMATCH:%s" % stage)
                continue
            if isinstance(old, Mapping) and old.get("status") == "READY":
                if old.get("fingerprint") == fingerprint:
                    continue
                # A completed producer artifact is immutable.  Letting a
                # changed input execute over it would turn resume into a
                # silent partial rerun, so operators must choose a new run
                # directory/run id after inspecting the evidence change.
                self._record_error(stage, "RESUME_FINGERPRINT_MISMATCH")
                raise ProductionRunError("RESUME_FINGERPRINT_MISMATCH:%s" % stage)
            handler = handlers.get(stage)
            if handler is None:
                self._record_error(stage, "STAGE_HANDLER_MISSING")
                raise ProductionRunError("STAGE_HANDLER_MISSING:%s" % stage)
            try:
                payload = dict(handler({"run_id": self.run_id, "stage": stage, "offline": self.offline,
                                        "manifest": manifest, "config": self.config}) or {})
            except Exception as exc:
                self._record_error(stage, "STAGE_EXCEPTION", str(exc)); raise
            if not payload or payload.get("status") not in {None, "READY"}:
                self._record_error(stage, "STAGE_NOT_READY")
                raise ProductionRunError("STAGE_NOT_READY:%s" % stage)
            if stage == "release":
                # Formal release is owned by the single Production Release
                # Gate.  A runner/fixture cannot promote a self-declared
                # READY flag or a generic sealed blob into final evidence.
                from ..production.release import evaluate_release_gate
                artifacts = payload.get("artifacts")
                if not isinstance(artifacts, Mapping):
                    self._record_error(stage, "RELEASE_ARTIFACTS_MISSING")
                    raise ProductionRunError("RELEASE_ARTIFACTS_MISSING")
                decision = evaluate_release_gate(artifacts, formal=True)
                payload["release_decision"] = decision
                if not decision.get("ready"):
                    self._record_error(stage, "RELEASE_GATE_NOT_READY", str(decision.get("status")))
                    raise ProductionRunError("RELEASE_GATE_NOT_READY:%s" % decision.get("status"))
            if stage == "excel":
                release = completed.get("release", {}).get("payload", {}).get("release_decision", {})
                if not release.get("ready"):
                    self._record_error(stage, "RELEASE_GATE_NOT_READY")
                    raise ProductionRunError("RELEASE_GATE_NOT_READY")
            artifact = {"stage": stage, "status": "READY", "completed_at": _now(),
                        "fingerprint": fingerprint, "artifact_hash": artifact_hash(payload),
                        "input_artifact_hashes": dict(payload.get("input_artifact_hashes") or {}),
                        "payload": payload}
            completed[stage] = artifact
            manifest["updated_at"] = _now(); self._write(self.manifest_path, manifest)
            self._update_metrics(payload)
            progress = {"run_id": self.run_id, "current_stage": stage, "completed": list(completed),
                        "counts": payload.get("counts") or {}, "updated_at": _now()}
            self._write(self.progress_path, progress)
        status = "DRAFT_SOURCE_ONLY" if profile == "source-only" else "READY"
        summary = {"run_id": self.run_id, "status": status, "formal_release": profile == "full",
                   "stage_count": len(completed), "counts": {stage: (row.get("payload", {}).get("counts") or {})
                   for stage, row in completed.items()}, "manifest_hash": artifact_hash(manifest)}
        self._write(self.summary_path, summary)
        if self.metrics_path.exists():
            metrics = load_manifest(self.metrics_path)
            write_manifest(finalize_manifest(metrics, status="draft" if profile == "source-only" else "success"),
                           self.metrics_path)
        return summary
