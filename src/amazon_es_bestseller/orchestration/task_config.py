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
    ranking_evidence: Path | None
    source_urls: tuple[str, ...]
    pages_per_url: int
    reviewed_task_plan: Path | None
    reviewed_task_plan_hash: str | None
    detail_html_dirs: tuple[Path, ...]
    history_dir: Path | None
    profile: str
    translation: Mapping[str, Any]
    translation_selection_manifest: Path | None
    translation_selection_manifest_hash: str | None
    diagnostic_sample_detail_limit: int | None
    refresh_due_asins: frozenset[str]
    raw: Mapping[str, Any]
    source_candidate_manifest: Path | None = None
    source_candidate_manifest_hash: str | None = None

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
        candidate_value = str(source.get("source_candidate_manifest") or "").strip()
        candidate_manifest = ((root / candidate_value).resolve() if candidate_value else None)
        if candidate_manifest is not None and network_mode != "offline":
            raise TaskConfigError("SOURCE_CANDIDATE_OFFLINE_ONLY")
        if candidate_manifest is not None and not candidate_manifest.is_file():
            raise TaskConfigError("SOURCE_CANDIDATE_MANIFEST_MISSING:%s" % candidate_manifest)
        candidate_manifest_hash = _file_hash(candidate_manifest) if candidate_manifest is not None else None
        evidence = str(source.get("ranking_evidence") or value.get("ranking_evidence") or "").strip()
        ranking_evidence = ((root / evidence).resolve() if evidence and not Path(evidence).is_absolute()
                            else Path(evidence) if evidence else None)
        urls = source.get("source_urls") or source.get("urls") or value.get("source_urls") or []
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, (list, tuple)):
            raise TaskConfigError("TASK_CONFIG_SOURCE_URLS_INVALID")
        source_urls = tuple(str(url).strip() for url in urls if str(url).strip())
        try:
            pages_per_url = int(source.get("pages_per_url") or value.get("pages_per_url") or 1)
        except (TypeError, ValueError) as exc:
            raise TaskConfigError("TASK_CONFIG_PAGES_PER_URL_INVALID") from exc
        if pages_per_url < 1:
            raise TaskConfigError("TASK_CONFIG_PAGES_PER_URL_INVALID")
        if network_mode == "offline" and ranking_evidence is None:
            raise TaskConfigError("TASK_CONFIG_RANKING_EVIDENCE_REQUIRED")
        if network_mode == "live" and not source_urls:
            raise TaskConfigError("TASK_CONFIG_SOURCE_URLS_REQUIRED")
        plan_value = str(value.get("reviewed_task_plan") or "").strip()
        reviewed_task_plan = ((root / plan_value).resolve()
                              if plan_value and not Path(plan_value).is_absolute()
                              else Path(plan_value) if plan_value else None)
        if network_mode == "live" and reviewed_task_plan is None:
            raise TaskConfigError("TASK_CONFIG_REVIEWED_TASK_PLAN_REQUIRED")
        if reviewed_task_plan is not None and not reviewed_task_plan.is_file():
            raise TaskConfigError("REVIEWED_TASK_PLAN_MISSING:%s" % reviewed_task_plan)
        reviewed_task_plan_hash = (_file_hash(reviewed_task_plan)
                                   if reviewed_task_plan is not None else None)
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
        selection_value = str(translation.get("selection_manifest") or "").strip()
        selection_manifest = ((root / selection_value).resolve()
                              if selection_value and not Path(selection_value).is_absolute()
                              else Path(selection_value) if selection_value else None)
        if selection_manifest is not None and not selection_manifest.is_file():
            raise TaskConfigError("TRANSLATION_SELECTION_MANIFEST_MISSING:%s" % selection_manifest)
        selection_manifest_hash = (_file_hash(selection_manifest)
                                   if selection_manifest is not None else None)
        profile = str(value.get("profile") or "production-research")
        if "v2-canary" in profile.casefold():
            raise TaskConfigError("TASK_CONFIG_CANARY_PROFILE_FORBIDDEN")
        sample = value.get("sample")
        sample_limit = None
        if isinstance(sample, Mapping) and sample.get("diagnostic") is True:
            try:
                sample_limit = int(sample.get("max_detail_asins"))
            except (TypeError, ValueError) as exc:
                raise TaskConfigError("DIAGNOSTIC_SAMPLE_DETAIL_LIMIT_REQUIRED") from exc
            if sample_limit < 1:
                raise TaskConfigError("DIAGNOSTIC_SAMPLE_DETAIL_LIMIT_REQUIRED")
            live_transport = value.get("live_transport")
            budget = live_transport.get("request_budget") if isinstance(live_transport, Mapping) else None
            try:
                maximum = int(budget.get("max_detail_requests")) if isinstance(budget, Mapping) else 0
            except (TypeError, ValueError):
                maximum = 0
            if not maximum or sample_limit > maximum:
                raise TaskConfigError("DIAGNOSTIC_SAMPLE_DETAIL_LIMIT_EXCEEDS_TRANSPORT")
        detail = value.get("detail") if isinstance(value.get("detail"), Mapping) else {}
        due = detail.get("refresh_due_asins") or value.get("refresh_due_asins") or []
        if isinstance(due, str):
            due = [value for value in due.split(",") if value.strip()]
        if not isinstance(due, (list, tuple)):
            raise TaskConfigError("TASK_CONFIG_REFRESH_DUE_INVALID")
        refresh_due_asins = frozenset(str(asin).strip().upper() for asin in due if str(asin).strip())
        return cls(task_id, mode, network_mode, ranking_evidence, source_urls, pages_per_url,
                   reviewed_task_plan, reviewed_task_plan_hash, dirs, history_dir,
                   profile, dict(translation), selection_manifest, selection_manifest_hash,
                   sample_limit, refresh_due_asins, dict(value), candidate_manifest, candidate_manifest_hash)

    def evidence_fingerprints(self) -> dict[str, str]:
        if self.ranking_evidence is not None and not self.ranking_evidence.is_file():
            raise TaskConfigError("RANKING_EVIDENCE_MISSING:%s" % self.ranking_evidence)
        result = ({"ranking_evidence": _file_hash(self.ranking_evidence)}
                  if self.ranking_evidence is not None else
                  {"live_source_plan": hashlib.sha256(json.dumps({"urls": self.source_urls,
                      "pages_per_url": self.pages_per_url}, sort_keys=True).encode("utf-8")).hexdigest()})
        if self.reviewed_task_plan_hash:
            result["reviewed_task_plan"] = self.reviewed_task_plan_hash
        if self.source_candidate_manifest is not None:
            result["source_candidate_manifest"] = _file_hash(self.source_candidate_manifest)
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
                "source_urls": list(self.source_urls), "pages_per_url": self.pages_per_url,
                "reviewed_task_plan_hash": self.reviewed_task_plan_hash or "",
                "translation_selection_manifest_hash": self.translation_selection_manifest_hash or "",
                "diagnostic_sample_detail_limit": self.diagnostic_sample_detail_limit or 0,
                "refresh_due_asins": sorted(self.refresh_due_asins),
                "parser": {"ranking": "V1", "detail": "V1"},
                "translation": dict(self.translation)}

    def load_ranking_evidence(self) -> dict[str, Any]:
        if self.ranking_evidence is None:
            raise TaskConfigError("RANKING_EVIDENCE_UNAVAILABLE_IN_LIVE_MODE")
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
