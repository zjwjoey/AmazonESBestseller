"""Small, JSON-friendly contracts for Ranking Identity Snapshot V1."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

IDENTITY_PARSER_VERSION = "ranking_identity_v1"
IDENTITY_SCHEMA_VERSION = 1


@dataclass
class IdentityCandidate:
    """One raw identity observation from a card or supplemental payload."""

    asin: str = ""
    asin_source: str = ""
    card_asin: str = ""
    href_asin: str = ""
    raw_href: str | None = None
    rank: int | None = None
    rank_raw: str | None = None
    page_number: int | None = None
    card_index: int | None = None
    source_url: str = ""
    evidence_source: str = ""
    evidence_file: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value = {
            "asin": self.asin,
            "asin_source": self.asin_source,
            "card_asin": self.card_asin,
            "href_asin": self.href_asin,
            "raw_href": self.raw_href,
            "rank": self.rank,
            "rank_raw": self.rank_raw,
            "page_number": self.page_number,
            "card_index": self.card_index,
            "source_url": self.source_url,
            "evidence_source": self.evidence_source,
            "evidence_file": self.evidence_file,
        }
        value.update(self.raw)
        return value
