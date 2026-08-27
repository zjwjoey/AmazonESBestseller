"""Pure helpers for assigning and selecting the 200-SKU category quota.

The collector keeps ranking records as source evidence.  This module only adds
an explicit group tag from the reviewed URL manifest and selects a deterministic
set of unique ASINs; it never invents a category or ranking value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


class QuotaError(ValueError):
    """Raised when a configured group cannot satisfy its requested quota."""

    code = "QUOTA_UNIQUE_SHORTFALL"


def normalize_group(value: object) -> str:
    value = str(value or "").strip().casefold()
    if value in {"hogar", "hogar y cocina", "home", "kitchen"}:
        return "hogar"
    if value in {
        "diy",
        "bricolaje",
        "bricolaje y herramientas",
        "diy y herramientas",
        "tools",
    }:
        return "diy"
    return value


def normalize_source_url(value: object) -> str:
    """Normalize only URL noise used by Amazon's ranking links.

    Query strings/fragments and a trailing slash do not identify a different
    configured ranking page, while the path remains case-sensitive enough for
    our exact manifest lookup.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, "", ""))


def annotate_groups(records: Iterable[Mapping], category_config: Sequence[Mapping]) -> list[dict]:
    """Attach ``category_group`` from an exact normalized ranking URL match.

    Existing explicit group tags win.  A record with neither an explicit tag
    nor a configured URL match is retained but remains ungrouped, allowing the
    selector to report an honest shortfall instead of guessing.
    """

    url_to_group: dict[str, str] = {}
    for row in category_config:
        url = normalize_source_url(row.get("url") or row.get("ranking_source_url"))
        group = normalize_group(row.get("category_group") or row.get("group"))
        if url and group:
            url_to_group[url] = group

    out: list[dict] = []
    for record in records:
        item = dict(record)
        group = normalize_group(item.get("category_group") or item.get("group"))
        if not group:
            group = url_to_group.get(normalize_source_url(item.get("ranking_source_url")), "")
        if group:
            item["category_group"] = group
        out.append(item)
    return out


def select_quota(records: Iterable[Mapping], quotas: Mapping[str, int]) -> dict[str, list[dict]]:
    """Select the first unique ASINs in source order for each requested group."""

    normalized_quotas: dict[str, int] = {}
    for raw_group, raw_quota in quotas.items():
        group = normalize_group(raw_group)
        quota = int(raw_quota)
        if not group or quota < 1:
            raise QuotaError("配额必须是正整数: %r=%r" % (raw_group, raw_quota))
        normalized_quotas[group] = quota

    selected = {group: [] for group in normalized_quotas}
    seen = {group: set() for group in normalized_quotas}
    seen_global: set[str] = set()
    for record in records:
        group = normalize_group(record.get("category_group") or record.get("group") or record.get("category_l1"))
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        if (group not in selected or not asin or asin in seen_global
                or asin in seen[group] or len(selected[group]) >= normalized_quotas[group]):
            continue
        seen[group].add(asin)
        seen_global.add(asin)
        selected[group].append(dict(record, asin=asin, category_group=group))

    for group, quota in normalized_quotas.items():
        actual = len(selected[group])
        if actual < quota:
            raise QuotaError("QUOTA_UNIQUE_SHORTFALL: %s组需要 %d 个全局唯一 ASIN，只有 %d 个可用" %
                             (group, quota, actual))
    return selected
