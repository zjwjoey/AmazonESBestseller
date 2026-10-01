"""Provider boundary for Translation V2.

Providers translate one field at a time.  They do not own cache, record
merging, or QA, which keeps network-specific code replaceable and testable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ProviderResponse:
    text: str = ""
    provider: str = ""
    model: str = ""
    status: str = "success"
    error: Optional[str] = None
    attempts: int = 1
    raw: Dict[str, Any] = field(default_factory=dict)


class TranslationProvider(ABC):
    name = "provider"

    @abstractmethod
    def translate(self, text: str, *, asin: str, field: str,
                  context: Optional[Dict[str, Any]] = None) -> ProviderResponse:
        """Translate exactly one source field."""

    def translate_field(self, text: str, *, asin: str, field: str,
                        context: Optional[Dict[str, Any]] = None) -> ProviderResponse:
        """Readable alias used by integrations that call the operation a field translation."""
        return self.translate(text, asin=asin, field=field, context=context)

    @property
    def model(self) -> str:
        return ""
