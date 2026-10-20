"""Stable, lossless handoff for ranking candidates entering later stages.

This adapter describes one observed ranking link.  It deliberately does not
turn a URL, a parent ASIN, or an unverified ranking ASIN into a confirmed
buyable SKU.  Existing product identity resolution remains authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .models import is_valid_asin


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _observation_locator(record: Mapping[str, Any], raw_url: Any) -> dict[str, Any]:
    """Build the provenance key for one source-bound appearance."""
    return {
        "snapshot_id": str(record.get("snapshot_id") or ""),
        "source_url": str(_first(record, "ranking_source_url", "source_url") or ""),
        "page_number": _first(record, "ranking_page_number", "page_number"),
        "slot": _first(record, "ranking_index", "index", "card_index"),
        "rank": _first(record, "ranking_rank", "bestseller_rank", "rank"),
        "raw_product_url": str(raw_url or ""),
    }


def _digest_id(prefix: str, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return prefix + hashlib.sha256(payload).hexdigest()[:24]


def _candidate_id(record: Mapping[str, Any], raw_url: Any,
                  normalized_url: Any) -> str:
    """Build a URL-group key when normalized evidence exists; never use ASIN."""
    supplied = str(record.get("candidate_id") or "").strip()
    observed_asin = str(_first(record, "ranking_asin", "asin") or "").strip().upper()
    if supplied and supplied.upper() != observed_asin and not is_valid_asin(supplied):
        return supplied
    if normalized_url:
        return _digest_id("candidate_", {"normalized_product_url": str(normalized_url)})
    return _digest_id("candidate_", _observation_locator(record, raw_url))


@dataclass(frozen=True)
class CandidateObservation:
    """One source-bound candidate observation, not a product/SKU record."""

    candidate_id: str
    observation_id: str
    original_product_url: str
    normalized_product_url: str
    source_type: str
    source_root_url: str
    source_page_url: str
    snapshot_id: str
    observed_at: str
    ranking_position: Any
    ranking_position_raw: Any
    asin_observed: str
    identity_status: str
    evidence_file: str
    source_record: Mapping[str, Any]

    @classmethod
    def from_ranking_record(cls, record: Mapping[str, Any]) -> "CandidateObservation":
        """Adapt one mainline ranking row without discarding unknown evidence."""
        if not isinstance(record, Mapping):
            raise TypeError("ranking record must be a mapping")
        raw_url = _first(record, "ranking_product_url_raw", "raw_product_url",
                         "product_url_raw", "ranking_url_raw")
        normalized_url = _first(record, "ranking_product_url_normalized",
                                "normalized_product_url", "product_url_normalized")
        locator = _observation_locator(record, raw_url)
        return cls(
            candidate_id=_candidate_id(record, raw_url, normalized_url),
            observation_id=str(record.get("observation_id") or
                               _digest_id("ranking_observation_", locator)),
            original_product_url=str(raw_url or ""),
            normalized_product_url=str(normalized_url or ""),
            source_type=str(_first(record, "ranking_source_type", "source_type") or ""),
            source_root_url=str(_first(record, "ranking_source_url", "source_url") or ""),
            source_page_url=str(_first(record, "ranking_page_url", "source_page_url",
                                       "ranking_source_url", "source_url") or ""),
            snapshot_id=str(record.get("snapshot_id") or ""),
            observed_at=str(_first(record, "observed_at", "collected_at") or ""),
            ranking_position=_first(record, "ranking_rank", "bestseller_rank",
                                    "ranking_position", "rank"),
            ranking_position_raw=_first(record, "ranking_rank_raw",
                                         "bestseller_rank_raw", "ranking_position_raw", "rank_raw"),
            asin_observed=str(_first(record, "ranking_asin", "asin", "observed_asin",
                                     "asin_observed") or "").strip().upper(),
            identity_status=str(_first(record, "ranking_link_identity_status",
                                        "identity_status") or "UNCONFIRMED"),
            evidence_file=str(_first(record, "evidence_file", "html_file", "source_html",
                                     "evidence_path") or ""),
            source_record=dict(record),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the original row plus explicit candidate handoff fields."""
        return {
            **dict(self.source_record),
            "candidate_id": self.candidate_id,
            "candidate_observation_id": self.observation_id,
            "candidate_identity_status": "UNCONFIRMED",
            "candidate_original_product_url": self.original_product_url,
            "candidate_normalized_product_url": self.normalized_product_url,
            "candidate_source_type": self.source_type,
            "candidate_source_root_url": self.source_root_url,
            "candidate_source_page_url": self.source_page_url,
            "candidate_snapshot_id": self.snapshot_id,
            "candidate_observed_at": self.observed_at,
            "candidate_ranking_position": self.ranking_position,
            "candidate_ranking_position_raw": self.ranking_position_raw,
            "candidate_asin_observed": self.asin_observed,
            "candidate_link_identity_status": self.identity_status,
            "candidate_evidence_file": self.evidence_file,
        }


def adapt_ranking_candidates(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Adapt ranking observations to candidate handoffs, preserving row order."""
    return [CandidateObservation.from_ranking_record(row).to_dict() for row in records]


__all__ = ["CandidateObservation", "adapt_ranking_candidates"]
