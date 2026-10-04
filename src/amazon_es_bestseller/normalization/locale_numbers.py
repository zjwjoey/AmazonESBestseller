"""Locale-aware numeric parsing for Amazon.es evidence."""
from __future__ import annotations

import re


_NUMBER_RE = re.compile(r"[-+]?\d[\d\s\u00a0\u202f.,]*")


def parse_locale_number(value: object) -> float | None:
    if value is None:
        return None
    match = _NUMBER_RE.search(str(value))
    if not match:
        return None
    raw = match.group(0).replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = head.replace(",", "") + ("." + tail if len(tail) in (1, 2) else tail)
    elif raw.count(".") > 1 or (raw.count(".") == 1 and len(raw.rsplit(".", 1)[1]) == 3):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_price_es(value: object) -> float | None:
    return parse_locale_number(value)


def parse_rating_es(value: object) -> float | None:
    return parse_locale_number(value)


def parse_review_count_es(value: object) -> int | None:
    parsed = parse_locale_number(value)
    return int(parsed) if parsed is not None else None
