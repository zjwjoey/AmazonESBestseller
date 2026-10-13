"""Reviewed scope and phase gates checked before opening a live browser."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import json

from .contracts import PHASE_TARGETS, timestamp
from .storage import read_sealed, digest


def amazon_url(url):
    parsed = urlsplit(str(url))
    return parsed.scheme == "https" and parsed.hostname == "www.amazon.es" and not parsed.username and not parsed.password and parsed.port in {None, 443}


def validate_authorization(plan, authorization, sources, gate, *, now=None):
    if not plan.get("live_enabled") or not authorization or authorization.get("approved") is not True:
        raise ValueError("LIVE_AUTHORIZATION_REQUIRED")
    phase = plan.get("phase")
    if phase not in PHASE_TARGETS or authorization.get("phase") != phase:
        raise ValueError("PHASE_AUTHORIZATION_MISMATCH")
    for field in ("approval_id", "approved_by", "reviewed_at", "expires_at", "stop_conditions"):
        if not authorization.get(field):
            raise ValueError("AUTHORIZATION_MISSING_" + field.upper())
    current = datetime.fromisoformat(timestamp(now)["observed_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(timestamp(authorization["expires_at"])["observed_at"].replace("Z", "+00:00"))
    reviewed = datetime.fromisoformat(timestamp(authorization["reviewed_at"])["observed_at"].replace("Z", "+00:00"))
    if reviewed > current or expires <= current:
        raise ValueError("AUTHORIZATION_EXPIRED_OR_NOT_YET_VALID")
    required_stops = {"CHALLENGE", "BLOCKED", "RATE_LIMITED", "UNKNOWN", "NETWORK_ERROR"}
    if not required_stops.issubset(set(authorization["stop_conditions"])):
        raise ValueError("INCOMPLETE_STOP_CONDITIONS")
    if gate.get("state", "NORMAL") != "NORMAL":
        if not authorization.get("clear_access_gate") or authorization["approval_id"] == gate.get("approval_id"):
            raise ValueError("ACCESS_GATE_REQUIRES_NEW_HUMAN_AUTHORIZATION")
    if authorization.get("access_gate_state") != "NORMAL":
        raise ValueError("ACCESS_GATE_NOT_REVIEWED")
    for key in ("max_requests", "max_pages", "max_requests_per_day", "max_observations"):
        value = plan.get(key)
        limit = authorization.get(key)
        if not isinstance(value, int) or value <= 0 or not isinstance(limit, int) or value > limit:
            raise ValueError("UNREVIEWED_OR_INVALID_BUDGET_" + key.upper())
    if plan["max_observations"] > PHASE_TARGETS[phase] or plan["max_observations"] > 30000:
        raise ValueError("PHASE_BUDGET_EXCEEDED")
    if plan.get("parallelism", 1) != 1 or float(plan.get("request_delay_seconds", 2)) < 2:
        raise ValueError("SERIAL_CONSERVATIVE_ACCESS_REQUIRED")
    cooldown = plan.get("cooldown_after_category_seconds", 1800)
    if (not isinstance(cooldown, (int, float)) or isinstance(cooldown, bool) or cooldown < 0
            or cooldown == 0 and authorization.get("cooldown_after_category_seconds") != 0):
        raise ValueError("CATEGORY_COOLDOWN_REQUIRES_EXPLICIT_REVIEW")
    reviewed_sources = authorization.get("sources", {})
    for source in sources:
        scope = reviewed_sources.get(source["source_id"])
        if not scope or scope.get("source_type") != source["source_type"]:
            raise ValueError("UNREVIEWED_SOURCE")
        if not scope.get("evidence") or scope.get("research_category") != source.get("research_category"):
            raise ValueError("UNREVIEWED_SOURCE_MAPPING")
        if isinstance(scope["evidence"], dict) and scope["evidence"].get("reviewed") is not True:
            raise ValueError("SOURCE_SCOPE_NOT_EXPLICITLY_REVIEWED")
        url = source.get("actual_source_url") or source.get("root_url")
        if not amazon_url(url) or url not in scope.get("page_urls", []):
            raise ValueError("UNREVIEWED_SOURCE_URL")
        if phase != "P0" and source.get("source_status") != "AVAILABLE":
            raise ValueError("VERIFIED_SOURCE_REQUIRED_AFTER_P0")
    if phase != "P0":
        path = authorization.get("previous_phase_gate_path")
        if not path:
            raise ValueError("PREVIOUS_PHASE_GATE_REQUIRED")
        previous = read_sealed(path)
        expected = "P" + str(int(phase[1]) - 1)
        if previous.get("phase") != expected or previous.get("origin") != "LIVE_CAPTURE" or previous.get("passed") is not True:
            raise ValueError("PREVIOUS_PHASE_GATE_FAILED")
        if digest(Path(path).read_bytes()) != authorization.get("previous_phase_gate_sha256"):
            raise ValueError("PREVIOUS_PHASE_GATE_EVIDENCE_MISMATCH")
        directory = Path(path).resolve().parent
        manifest_path = directory / "manifest.json"
        quality_path = directory / "quality_report.json"
        if (digest(manifest_path.read_bytes()) != previous.get("manifest_sha256")
                or digest(quality_path.read_bytes()) != previous.get("quality_report_sha256")):
            raise ValueError("PREVIOUS_PHASE_LINKED_EVIDENCE_CORRUPT")
        manifest = read_sealed(manifest_path)
        for name, checksum in manifest.get("artifacts", {}).items():
            if Path(name).name != name or digest((directory / name).read_bytes()) != checksum:
                raise ValueError("PREVIOUS_PHASE_ARTIFACT_CORRUPT")
        from .quality import audit_snapshot
        pages = json.loads((directory / "pages.json").read_text(encoding="utf-8"))
        observations = json.loads((directory / "identity_links.json").read_text(encoding="utf-8"))
        from .identity import ProductIdentityRegistry
        store = directory.parents[3]
        if not (store / ".multisource_store.json").is_file():
            raise ValueError("PREVIOUS_PHASE_GATE_OUTSIDE_SNAPSHOT_STORE")
        observations = ProductIdentityRegistry(store / "identity").materialize_views(observations, manifest["version"])
        if not audit_snapshot(manifest, pages, observations)["gate_passed"]:
            raise ValueError("PREVIOUS_PHASE_QA_NO_LONGER_PASSES")
    return authorization


def authorize_request(url, *, source, authorization, kind="PAGE"):
    if not amazon_url(url):
        raise ValueError("NON_AMAZON_ES_URL")
    scope = authorization["sources"][source["source_id"]]
    if kind == "PAGE" and url not in scope.get("page_urls", []):
        raise ValueError("UNREVIEWED_PAGE_URL")
    if kind == "ACP" and not scope.get("allow_acp"):
        raise ValueError("UNREVIEWED_ACP_CONTINUATION")
    if kind == "ACP" and not urlsplit(url).path.startswith("/acp/"):
        raise ValueError("INVALID_ACP_EVIDENCE_URL")
