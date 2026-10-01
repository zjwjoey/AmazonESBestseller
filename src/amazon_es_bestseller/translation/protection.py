"""Protect technical tokens before translation and validate restoration."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

TOKEN_RE = re.compile(
    r"(?<!\w)(?:\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)+\b"
    r"|\d+(?:[.,]\d+)?\s*(?i:ml|cl|dl|l|g|kg|mg|mm|cm|m|w|kw|v|a|hz|ghz|mah|bar|psi|°c|%)"
    r"|\d+(?:[.,]\d+)?(?:[×x*]\d+(?:[.,]\d+)?)+(?:\s*(?i:mm|cm|m))?"
    r"|\b\d+(?:[.,]\d+)?\b(?!\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])"
    r"|(?i:usb[- ]?c|usb[- ]?a|pd\s*\d+(?:\.\d+)?|ip\w+|e\d{2}|a\d|m\d+|[a-z]{1,8}-\d{1,4})"
    r"|\b[A-Z]{2,}[A-Z0-9]*(?:[-/]?[A-Z0-9]+)*\b|\b[A-Z0-9]{8,10}\b)(?!\w)")
PLACEHOLDER_RE = re.compile(r"__T(\d{4})__")
# Values under these labels are identity evidence, not prose.  Protecting the
# value (rather than only an uppercase token such as ``OEM``) keeps model
# names like ``Cera Tec`` and numeric model IDs unchanged.
_IDENTITY_VALUE_RE = re.compile(
    r"(?im)(?:n[uú]mero\s+de\s+modelo|modelo|oem|part\s*number|"
    r"referencia(?:\s+oem)?|n[uú]mero\s+de\s+pieza(?:\s+del\s+fabricante)?)"
    r"\s*[:：]\s*([^;\n]+)"
)
# Preserve legal entity names in detail values, including the common Spanish
# suffixes.  This is deliberately narrow and does not attempt to translate
# arbitrary capitalized words.
_LEGAL_ENTITY_RE = re.compile(
    r"(?<!\w)((?:[\wÀ-ÿ&.'-]+\s+){1,7}"
    r"(?:S\.?\s*L\.?|S\.?\s*A\.?|GmbH|Ltd\.?|LLC|Inc\.?|SAS))\b",
    re.I,
)


@dataclass
class ProtectedText:
    text: str
    tokens: Dict[str, str] = field(default_factory=dict)
    protected_values: List[str] = field(default_factory=list)


def protect(text: str, *, protected_values: Iterable[str] = ()) -> ProtectedText:
    source = str(text or "")
    identity_values = []
    for match in _IDENTITY_VALUE_RE.finditer(source):
        value = match.group(1).strip()
        if value:
            identity_values.append(value)
    legal_values = [match.group(1).strip() for match in _LEGAL_ENTITY_RE.finditer(source)]
    values = [str(v) for v in (*identity_values, *legal_values, *protected_values) if str(v)]
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
        # A provider may echo a protected value literally instead of carrying
        # the placeholder through its response.  That is still preservation,
        # not loss: accept one exact literal occurrence and only report a
        # missing token when neither form is present.
        value = protected.tokens[marker]
        if value not in text:
            issues.append({"code": "PROTECTED_TOKEN_MISSING", "token": value})
    for marker in sorted(found_markers - expected):
        issues.append({"code": "PROTECTED_TOKEN_EXTRA", "token": marker})
    # A token must occur once; duplicated placeholders often indicate model drift.
    for marker in expected:
        if marker in found_markers and len(re.findall(re.escape(marker), text)) != 1:
            issues.append({"code": "PROTECTED_TOKEN_COUNT", "token": protected.tokens[marker]})
    for marker, value in protected.tokens.items():
        text = text.replace(marker, value)
    return text, issues
