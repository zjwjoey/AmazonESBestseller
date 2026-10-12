"""Offline verification of self-parent family evidence; never infer a family."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from ..models import normalize_asin


def confirmed_self_parent(detail: Mapping, asin: str, cache_root: Path | None = None) -> bool:
    evidence = detail.get("variation_evidence")
    if not isinstance(evidence, Mapping) or str(evidence.get("producer") or "").casefold() != "parser":
        return False
    family = {normalize_asin(value) for value in evidence.get("family_asins") or ()}
    if (normalize_asin(evidence.get("current_asin")) != asin
            or normalize_asin(evidence.get("parent_asin")) != asin
            or asin not in family or not any(value and value != asin for value in family)):
        return False
    raw_path = str(evidence.get("cache_html_path") or "")
    expected = str(evidence.get("cache_html_sha256") or "").casefold()
    if not raw_path or len(expected) != 64:
        return False
    path = Path(raw_path)
    if not path.is_absolute():
        if cache_root is None:
            return False
        path = cache_root / path
    try:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            return False
        # A hash alone does not prove the caller's claimed family is in HTML.
        from ..collection.detail_v2 import _variation_evidence
        parsed = _variation_evidence(content.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    return (parsed.get("current_asin") == asin and parsed.get("parent_asin") == asin
            and set(parsed.get("family_asins") or ()) == family)
