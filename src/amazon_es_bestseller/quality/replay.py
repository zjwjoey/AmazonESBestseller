"""Deterministic offline replay of the existing V1 parsers."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..collection.detail import parse_detail_page
from ..collection.ranking import parse_bestsellers_page
from ..models import normalize_asin
from .models import QualityStatus, check_result, issue


_ASIN_RE = re.compile(r"[A-Z0-9]{10}", re.I)
_RANKING_FIELDS = (
    "asin", "bestseller_rank", "ranking_source_url", "ranking_page_number",
    "ranking_product_url_raw",
)
_DETAIL_FIELDS = (
    "asin", "title_es_raw", "current_price_raw", "original_price_raw", "brand_raw",
    "parent_asin", "attributes", "feature_bullets_raw", "product_description_raw",
    "detail_category_trail",
)


def _roots(value: str | Path | Iterable[str | Path] | None) -> list[Path]:
    if not value:
        return []
    values = [value] if isinstance(value, (str, Path)) else list(value)
    return [Path(item) for item in values]


def _status_rows(run_dir: str | Path | None) -> list[dict[str, Any]]:
    if not run_dir:
        return []
    root = Path(run_dir)
    for candidate in (root / "page_statuses.json", root / "pages" / "page_statuses.json"):
        if candidate.exists():
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def _normal(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normal(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normal(item) for item in value]
    if value is None:
        return None
    return str(value).strip()


def _detail_asin(path: Path, html: str) -> str:
    match = _ASIN_RE.fullmatch(path.stem.upper())
    if match:
        return match.group(0)
    match = re.search(r"(?:id|name)\s*=\s*[\"']ASIN[\"'][^>]*value\s*=\s*[\"']([A-Z0-9]{10})",
                      html, re.I)
    return match.group(1).upper() if match else ""


def _detail_files(roots: list[Path]) -> dict[str, tuple[Path, str]]:
    found: dict[str, tuple[Path, str]] = {}
    for root in roots:
        paths = sorted(root.rglob("*.html")) if root.is_dir() else [root]
        for path in paths:
            try:
                html = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            asin = _detail_asin(path, html)
            if asin and asin not in found:
                found[asin] = (path, html)
    return found


def audit_offline_replay(
    rankings: Iterable[Mapping], details: Iterable[Mapping], *,
    ranking_html_dirs: str | Path | Iterable[str | Path] | None = None,
    detail_html_dirs: str | Path | Iterable[str | Path] | None = None,
    run_dir: str | Path | None = None,
    asins: set[str] | None = None,
) -> object:
    ranking_rows = [dict(row) for row in (rankings or []) if isinstance(row, Mapping)]
    detail_rows = [dict(row) for row in (details or []) if isinstance(row, Mapping)]
    ranking_roots = _roots(ranking_html_dirs)
    if not ranking_roots and run_dir:
        root = Path(run_dir)
        if (root / "html").is_dir():
            ranking_roots = [root / "html"]
    detail_roots = _roots(detail_html_dirs)
    issues = []
    replayed_rankings = 0
    replayed_details = 0

    status_rows = _status_rows(run_dir)
    ranking_files: list[Path] = []
    for root in ranking_roots:
        if root.is_dir():
            ranking_files.extend(sorted(root.glob("ranking_*.html")))
        elif root.suffix.lower() in {".html", ".htm"}:
            ranking_files.append(root)
    expected_by_key: dict[tuple, dict] = {}
    expected_by_asin: dict[str, list[dict]] = {}
    for row in ranking_rows:
        asin = normalize_asin(row.get("asin") or row.get("ranking_asin"))
        if asins and asin not in asins:
            continue
        key = (str(row.get("ranking_source_url") or row.get("source_url") or ""),
               row.get("ranking_page_number", row.get("page_number")), asin)
        expected_by_key[key] = row
        expected_by_asin.setdefault(asin, []).append(row)
    for index, path in enumerate(ranking_files):
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            issues.append(issue(
                "offline_replay", QualityStatus.BLOCK, "P1", "REPLAY_EVIDENCE_UNREADABLE",
                source="ranking", source_file=path, message="榜单 HTML 无法读取。",
                evidence={"error": str(exc)}))
            continue
        status = status_rows[index] if index < len(status_rows) else {}
        page_url = str(status.get("page_url") or status.get("source_url") or "")
        if not page_url:
            source_urls = {str(row.get("ranking_source_url") or row.get("source_url") or "")
                           for row in ranking_rows if str(row.get("ranking_source_url") or row.get("source_url") or "")}
            if len(source_urls) == 1:
                page_url = next(iter(source_urls))
        page_number = status.get("page_number", 1)
        parsed = parse_bestsellers_page(html, page_url, "")
        replayed_rankings += 1
        for row in parsed:
            asin = normalize_asin(row.get("asin"))
            expected = expected_by_key.get((page_url, page_number, asin))
            if expected is None:
                candidates = expected_by_asin.get(asin, [])
                expected = candidates[0] if len(candidates) == 1 else None
            if expected is None:
                issues.append(issue(
                    "offline_replay", QualityStatus.BLOCK, "P1", "OFFLINE_REPLAY_MISMATCH",
                    asin=asin, source="ranking", source_file=path,
                    message="离线重放产生了在线记录中不存在的榜单记录。",
                    evidence={"replayed": {key: row.get(key) for key in _RANKING_FIELDS}}))
                continue
            mismatches = {}
            for field in _RANKING_FIELDS:
                left = _normal(row.get(field))
                right_key = field
                if field == "asin":
                    right_key = "asin"
                right = _normal(expected.get(right_key))
                if left != right and not (field == "ranking_page_number" and right is None):
                    mismatches[field] = {"online": right, "offline": left}
            if mismatches:
                issues.append(issue(
                    "offline_replay", QualityStatus.BLOCK, "P1", "OFFLINE_REPLAY_MISMATCH",
                    asin=asin, source="ranking", source_file=path,
                    message="榜单 V1 在线记录与同 HTML 的离线解析不一致。",
                    evidence={"mismatches": mismatches}))
        if not parsed and expected_by_key:
            issues.append(issue(
                "offline_replay", QualityStatus.BLOCK, "P1", "OFFLINE_REPLAY_MISMATCH",
                source="ranking", source_file=path, message="榜单 HTML 重放没有产生有效记录。"))

    detail_files = _detail_files(detail_roots)
    for row in detail_rows:
        asin = normalize_asin(row.get("asin") or row.get("requested_asin"))
        if asins and asin not in asins:
            continue
        found = detail_files.get(asin)
        if found is None:
            issues.append(issue(
                "offline_replay", QualityStatus.BLOCK, "P1", "REPLAY_EVIDENCE_MISSING",
                asin=asin, source="detail", message="找不到与详情记录对应的已保存 HTML。"))
            continue
        path, html = found
        parsed = parse_detail_page(html, asin)
        replayed_details += 1
        mismatches = {}
        for field in _DETAIL_FIELDS:
            online = _normal(row.get(field))
            offline = _normal(parsed.get(field))
            if online != offline:
                mismatches[field] = {"online": online, "offline": offline}
        if mismatches:
            issues.append(issue(
                "offline_replay", QualityStatus.BLOCK, "P1", "OFFLINE_REPLAY_CORE_MISMATCH",
                asin=asin, source="detail", source_file=path,
                message="详情 V1 在线记录与同 HTML 的离线解析核心字段不一致。",
                evidence={"mismatches": mismatches}))
    if ranking_rows and not ranking_files:
        issues.append(issue(
            "offline_replay", QualityStatus.BLOCK, "P1", "REPLAY_EVIDENCE_MISSING",
            source="ranking", message="存在榜单记录但没有保存的榜单 HTML。"))
    if detail_rows and not detail_files:
        issues.append(issue(
            "offline_replay", QualityStatus.BLOCK, "P1", "REPLAY_EVIDENCE_MISSING",
            source="detail", message="存在详情记录但没有保存的详情 HTML。"))
    if not ranking_rows and not detail_rows:
        issues.append(issue(
            "offline_replay", QualityStatus.REVIEW, "P2", "REPLAY_INPUT_EMPTY",
            source="replay", message="没有可重放的 V1 记录。"))
    return check_result(
        "offline_replay", issues,
        summary={"ranking_pages_replayed": replayed_rankings,
                 "detail_pages_replayed": replayed_details,
                 "network_requests": 0},
    )
