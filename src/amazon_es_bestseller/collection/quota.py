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


def validate_category_config(config) -> list[dict]:
    """Validate reviewed Amazon.es category URL/quota config before a run."""
    rows = config.get("categories", []) if isinstance(config, Mapping) else config
    if not isinstance(rows, list) or not rows:
        raise QuotaError("类目配置必须是非空数组")
    normalized = []
    groups = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise QuotaError("类目配置第 %d 项必须是对象" % index)
        group = normalize_group(row.get("category_group") or row.get("group"))
        if not group:
            raise QuotaError("类目配置第 %d 项缺少 group" % index)
        url = str(row.get("url") or row.get("ranking_url") or "").strip()
        if url:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc.lower() not in {"amazon.es", "www.amazon.es"}:
                raise QuotaError("类目配置第 %d 项 URL 必须是 Amazon.es HTTPS 地址" % index)
        try:
            quota = int(row.get("quota"))
        except (TypeError, ValueError):
            raise QuotaError("类目配置第 %d 项 quota 必须是整数" % index)
        if quota < 1:
            raise QuotaError("类目配置第 %d 项 quota 必须为正数" % index)
        if group in groups:
            raise QuotaError("类目 group 重复：%s" % group)
        groups.add(group)
        normalized.append({**dict(row), "group": group, "url": url, "quota": quota})
    target = config.get("target_unique") if isinstance(config, Mapping) else None
    if target is not None:
        try:
            target = int(target)
        except (TypeError, ValueError):
            raise QuotaError("target_unique 必须是整数")
        total = sum(row["quota"] for row in normalized)
        if target != total:
            raise QuotaError("target_unique=%d 与 quota 总和=%d 不一致" % (target, total))
    return normalized


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
            # Automotive pages must not absorb baby/other department records;
            # keep the evidence but mark the source mismatch so quota selection
            # cannot silently count it.
            l1 = str(item.get("category_l1") or "").strip().casefold()
            if group == "car" and l1 and l1 not in {"coche y moto", "coche y motocicleta"}:
                item["category_group_source_mismatch"] = True
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
        if (group not in selected or record.get("category_group_source_mismatch")
                or not asin or asin in seen_global
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
