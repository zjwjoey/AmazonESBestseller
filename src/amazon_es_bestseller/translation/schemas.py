"""Stable dataclasses used by Translation V2.

The schemas deliberately keep Spanish source evidence beside the Chinese result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

TRANSLATION_SCHEMA_VERSION = "translation-v2.1"


@dataclass
class TranslationFieldResult:
    asin: str
    field: str
    source_text: str = ""
    source_hash: str = ""
    translated_text: str = ""
    translation_status: str = "pending"
    qa_status: str = "pending"
    provider: str = ""
    model: str = ""
    schema_version: str = TRANSLATION_SCHEMA_VERSION
    prompt_version: str = "v1"
    attempt_count: int = 0
    last_error: Optional[str] = None
    qa_issues: List[Dict[str, Any]] = field(default_factory=list)
    translated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TranslationRecord:
    asin: str
    fields: Dict[str, TranslationFieldResult] = field(default_factory=dict)
    translation_status: str = "pending"
    source_record_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["fields"] = {k: v.to_dict() for k, v in self.fields.items()}
        return result


@dataclass
class TranslationQAResult:
    status: str
    issues: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TranslationBatchResult:
    records: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary: Dict[str, int] = field(default_factory=dict)
    qa_report: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
