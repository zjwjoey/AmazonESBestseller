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
            if isinstance(old, Mapping) and old.get("status") == "READY" and old.get("fingerprint") == fingerprint:
                continue
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
            artifact = {"stage": stage, "status": "READY", "completed_at": _now(),
                        "fingerprint": fingerprint, "artifact_hash": artifact_hash(payload), "payload": payload}
            completed[stage] = artifact
            manifest["updated_at"] = _now(); self._write(self.manifest_path, manifest)
            progress = {"run_id": self.run_id, "current_stage": stage, "completed": list(completed),
                        "counts": payload.get("counts") or {}, "updated_at": _now()}
            self._write(self.progress_path, progress)
        status = "DRAFT_SOURCE_ONLY" if profile == "source-only" else "READY"
        summary = {"run_id": self.run_id, "status": status, "formal_release": profile == "full",
                   "stage_count": len(completed), "counts": {stage: (row.get("payload", {}).get("counts") or {})
                   for stage, row in completed.items()}, "manifest_hash": artifact_hash(manifest)}
        self._write(self.summary_path, summary)
        return summary
