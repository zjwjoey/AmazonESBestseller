"""Protect technical tokens before translation and validate restoration."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

TOKEN_RE = re.compile(
    r"(?<!\w)(?:\d+(?:[.,]\d+)?\s*(?i:ml|cl|dl|l|g|kg|mg|mm|cm|m|w|kw|v|a|hz|ghz|mah|bar|psi|°c|%)"
    r"|\d+(?:[.,]\d+)?(?:[×x*]\d+(?:[.,]\d+)?)+(?:\s*(?i:mm|cm|m))?"
    r"|(?i:usb[- ]?c|usb[- ]?a|pd\s*\d+(?:\.\d+)?|ip\w+|e\d{2}|a\d|m\d+|[a-z]{1,8}-\d{1,4})"
    r"|\b[A-Z]{2,}[A-Z0-9]*(?:[-/]?[A-Z0-9]+)*\b|\b[A-Z0-9]{8,10}\b)(?!\w)")
PLACEHOLDER_RE = re.compile(r"__T(\d{4})__")


@dataclass
class ProtectedText:
    text: str
    tokens: Dict[str, str] = field(default_factory=dict)
    protected_values: List[str] = field(default_factory=list)


def protect(text: str, *, protected_values: Iterable[str] = ()) -> ProtectedText:
    source = str(text or "")
    values = [str(v) for v in protected_values if str(v)]
    tokens: Dict[str, str] = {}
    # Explicit brands/ASINs are protected first, longest first.
    explicit = sorted(set(values), key=len, reverse=True)
    for value in explicit:
        if not value or value in tokens.values():
            continue
        marker = "__T%04d__" % len(tokens)
        if value in source:
            source = source.replace(value, marker)
            tokens[marker] = value
    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        marker = "__T%04d__" % len(tokens)
        tokens[marker] = value
        return marker
    source = TOKEN_RE.sub(replace, source)
    return ProtectedText(source, tokens, explicit)


def restore(protected: ProtectedText, translated: str) -> tuple[str, List[dict]]:
    text = str(translated or "")
    issues: List[dict] = []
    expected = set(protected.tokens)
    found = set(PLACEHOLDER_RE.findall(text))
    found_markers = {"__T%s__" % n for n in found}
    for marker in sorted(expected - found_markers):
        issues.append({"code": "PROTECTED_TOKEN_MISSING", "token": protected.tokens[marker]})
    for marker in sorted(found_markers - expected):
        issues.append({"code": "PROTECTED_TOKEN_EXTRA", "token": marker})
    # A token must occur once; duplicated placeholders often indicate model drift.
    for marker in expected:
        if len(re.findall(re.escape(marker), text)) != 1:
            issues.append({"code": "PROTECTED_TOKEN_COUNT", "token": protected.tokens[marker]})
    for marker, value in protected.tokens.items():
        text = text.replace(marker, value)
    return text, issues
