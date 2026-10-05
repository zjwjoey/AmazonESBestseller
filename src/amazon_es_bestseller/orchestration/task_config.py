"""Typed, fail-closed task configuration for the Production V1 runner."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class TaskConfigError(ValueError):
    pass


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class TaskConfig:
    task_id: str
    mode: str
    network_mode: str
    ranking_evidence: Path
    detail_html_dirs: tuple[Path, ...]
    history_dir: Path | None
    profile: str
    translation: Mapping[str, Any]
    refresh_due_asins: frozenset[str]
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, base_dir: str | Path = ".") -> "TaskConfig":
        if not isinstance(value, Mapping):
            raise TaskConfigError("TASK_CONFIG_OBJECT_REQUIRED")
        if "stages" in value:
            raise TaskConfigError("TASK_CONFIG_STAGE_PAYLOADS_FORBIDDEN")
        root = Path(base_dir)
        task_id = str(value.get("task_id") or value.get("run_id") or "").strip()
        if not task_id:
            raise TaskConfigError("TASK_CONFIG_TASK_ID_REQUIRED")
        mode = str(value.get("mode") or "initial").strip().lower()
        if mode not in {"initial", "incremental"}:
            raise TaskConfigError("TASK_CONFIG_MODE_INVALID")
        network_mode = str(value.get("network_mode") or "offline").strip().lower()
        if network_mode not in {"offline", "live"}:
            raise TaskConfigError("TASK_CONFIG_NETWORK_MODE_INVALID")
        source = value.get("source") if isinstance(value.get("source"), Mapping) else value
        evidence = str(source.get("ranking_evidence") or value.get("ranking_evidence") or "").strip()
        if not evidence:
            raise TaskConfigError("TASK_CONFIG_RANKING_EVIDENCE_REQUIRED")
        ranking_evidence = (root / evidence).resolve() if not Path(evidence).is_absolute() else Path(evidence)
        html_values = source.get("detail_html_dirs") or value.get("detail_html_dirs") or []
        if isinstance(html_values, (str, Path)):
            html_values = [html_values]
        if not isinstance(html_values, (list, tuple)):
            raise TaskConfigError("TASK_CONFIG_DETAIL_HTML_DIRS_INVALID")
        dirs = tuple((root / str(path)).resolve() if not Path(path).is_absolute() else Path(path)
                     for path in html_values)
        history_value = str(value.get("history_dir") or "").strip()
        history_dir = ((root / history_value).resolve() if history_value and not Path(history_value).is_absolute()
                       else Path(history_value) if history_value else None)
        translation = value.get("translation") or {}
        if not isinstance(translation, Mapping):
            raise TaskConfigError("TASK_CONFIG_TRANSLATION_INVALID")
        profile = str(value.get("profile") or "production-research")
        if "v2-canary" in profile.casefold():
            raise TaskConfigError("TASK_CONFIG_CANARY_PROFILE_FORBIDDEN")
        detail = value.get("detail") if isinstance(value.get("detail"), Mapping) else {}
        due = detail.get("refresh_due_asins") or value.get("refresh_due_asins") or []
        if isinstance(due, str):
            due = [value for value in due.split(",") if value.strip()]
        if not isinstance(due, (list, tuple)):
            raise TaskConfigError("TASK_CONFIG_REFRESH_DUE_INVALID")
        refresh_due_asins = frozenset(str(asin).strip().upper() for asin in due if str(asin).strip())
        return cls(task_id, mode, network_mode, ranking_evidence, dirs, history_dir,
                   profile, dict(translation), refresh_due_asins, dict(value))

    def evidence_fingerprints(self) -> dict[str, str]:
        if not self.ranking_evidence.is_file():
            raise TaskConfigError("RANKING_EVIDENCE_MISSING:%s" % self.ranking_evidence)
        result = {"ranking_evidence": _file_hash(self.ranking_evidence)}
        for index, root in enumerate(self.detail_html_dirs):
            if not root.is_dir():
                raise TaskConfigError("DETAIL_HTML_DIR_MISSING:%s" % root)
            digest = hashlib.sha256()
            for path in sorted(root.glob("*.html")):
                digest.update(path.name.encode("utf-8")); digest.update(path.read_bytes())
            result["detail_html_dir_%d" % index] = digest.hexdigest()
        return result

    def runner_config(self) -> dict[str, Any]:
        """The run fingerprint includes evidence bytes, not merely their paths."""
        return {"task_config": self.raw, "task_id": self.task_id, "mode": self.mode,
                "network_mode": self.network_mode, "profile": self.profile,
                "evidence_fingerprints": self.evidence_fingerprints(),
                "refresh_due_asins": sorted(self.refresh_due_asins),
                "parser": {"ranking": "V1", "detail": "V1"},
                "translation": dict(self.translation)}

    def load_ranking_evidence(self) -> dict[str, Any]:
        try:
            value = json.loads(self.ranking_evidence.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TaskConfigError("RANKING_EVIDENCE_INVALID:%s" % self.ranking_evidence) from exc
        if isinstance(value, list):
            value = {"records": value}
        if not isinstance(value, dict) or not isinstance(value.get("records"), list):
            raise TaskConfigError("RANKING_EVIDENCE_RECORDS_REQUIRED")
        return value


__all__ = ["TaskConfig", "TaskConfigError"]
