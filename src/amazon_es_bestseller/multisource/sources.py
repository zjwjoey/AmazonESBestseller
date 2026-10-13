"""Evidence-backed source registration and saved-page discovery.

This module performs no network operations. Root URLs are candidates, not proof
that Amazon currently supports every research-category/source combination.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit
import uuid

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from .contracts import (ORIGINS, RESEARCH_CATEGORIES, SOURCE_ROOTS,
                        SOURCE_STATUSES, canonical_page_url, timestamp)
from .storage import atomic_json, digest, file_lock, read_sealed, sealed

REGISTRY_VERSION = "source_registry_v1.1"
_PATHS = {key: urlsplit(value).path for key, value in SOURCE_ROOTS.items()}


def _classification_path(value: str) -> str:
    # These are observed Amazon display locale prefixes. Preserve the original
    # URL everywhere else; a locale alias is not evidence of a different list.
    return re.sub(r"^/-/(?:en|es)(?=/)", "", urlsplit(value).path)


def source_type_value(value) -> str:
    value = str(getattr(value, "value", value))
    if value not in SOURCE_ROOTS:
        raise ValueError("UNKNOWN_SOURCE_TYPE: " + value)
    return value


def normalize_source_url(value: str, *, base_url: str = "https://www.amazon.es") -> str:
    """Remove known tracking only; preserve node, page and unknown parameters."""
    return canonical_page_url(value, base_url=base_url)


def source_type_from_url(value: str) -> str | None:
    normalized = normalize_source_url(value)
    if not normalized:
        return None
    path = _classification_path(normalized)
    for source_type, prefix in _PATHS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return source_type
    if path == "/zgbs" or path.startswith("/zgbs/"):
        return "BEST_SELLERS"
    return None


def browse_node_id(value: str) -> str | None:
    parts = urlsplit(normalize_source_url(value))
    for key, val in parse_qsl(parts.query):
        if key in {"node", "nodeId", "browseNodeId"} and val.isdigit():
            return val
    segments = parts.path.split("/")
    return next((part for part in reversed(segments) if part.isdigit()), None)


def same_source_category(page_url: str, source_url: str) -> bool:
    """Bind a paginated page to its registered source without inventing nodes.

    Explicit Browse Node identity is stronger than a display path slug. Where
    neither URL exposes a node, only the actual path and non-pagination query
    can prove the category context; two unknown nodes are not equal evidence.
    """
    page = normalize_source_url(page_url)
    source = normalize_source_url(source_url)
    kind = source_type_from_url(source)
    if not page or not source or kind is None or source_type_from_url(page) != kind:
        return False
    page_node, source_node = browse_node_id(page), browse_node_id(source)
    if page_node is not None or source_node is not None:
        return page_node is not None and page_node == source_node
    page_parts, source_parts = urlsplit(page), urlsplit(source)
    def context_query(parts):
        return sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                      if key != "pg")
    return (_classification_path(page) == _classification_path(source)
            and context_query(page_parts) == context_query(source_parts))


def _validate_live_receipt(proof: dict, *, row_id: str, source_url: str) -> None:
    """Verify a saved transport receipt, never a caller's LIVE label alone.

    Receipts are written by the authorized SnapshotManager before page commit.
    The registry performs no request and cannot upgrade fixture verification.
    """
    receipt_path = Path(proof.get("capture_receipt_path") or "")
    if not receipt_path.is_file():
        raise ValueError("LIVE_SOURCE_CAPTURE_RECEIPT_REQUIRED")
    receipt = read_sealed(receipt_path)
    transport = receipt.get("transport_evidence")
    if not isinstance(transport, dict) or not isinstance(transport.get("request"), dict) or not isinstance(transport.get("response"), dict):
        raise ValueError("LIVE_SOURCE_CAPTURE_RECEIPT_INVALID")
    request, response = transport["request"], transport["response"]
    body = response.get("body")
    request_url = normalize_source_url(request.get("url") or "")
    receipt_url = normalize_source_url(receipt.get("page_url") or "")
    proof_url = normalize_source_url(proof.get("page_url") or "")
    response_url = normalize_source_url(response.get("url") or "")
    def page_number(url):
        values = [value for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True) if key == "pg"]
        if not values:
            return 1
        if len(values) != 1 or not values[0].isdigit() or int(values[0]) < 1:
            return None
        return int(values[0])
    try:
        captured = timestamp(receipt.get("observed_at"))["observed_at"]
    except (TypeError, ValueError):
        raise ValueError("LIVE_SOURCE_CAPTURE_RECEIPT_INVALID") from None
    status_code = response.get("status_code")
    if (not isinstance(body, str) or (status_code is not None and type(status_code) is not int)
            or detect_access_status(status_code, body).value != receipt.get("access_status")):
        raise ValueError("LIVE_SOURCE_CAPTURE_RECEIPT_INVALID")
    if (receipt.get("origin") != "LIVE_CAPTURE" or receipt.get("kind") != "PAGE"
            or receipt.get("source_id") != row_id or receipt.get("access_status") != proof.get("access_status")
            or not receipt.get("approval_id") or not isinstance(body, str)
            or digest(body.encode("utf-8")) != proof.get("evidence_sha256")
            or captured != proof.get("observed_at") or not receipt.get("observed_at")
            or not request_url or request_url != receipt_url or request_url != proof_url
            or not same_source_category(request_url, source_url)
            or not same_source_category(response_url, source_url)
            or page_number(request_url) is None or page_number(response_url) != page_number(request_url)
            or (proof.get("capture_receipt_sha256") and
                digest(receipt_path.read_bytes()) != proof["capture_receipt_sha256"])):
        raise ValueError("LIVE_SOURCE_CAPTURE_RECEIPT_INVALID")


def source_id(source_type: str, url: str, research_category: str | None = None) -> str:
    source_type = source_type_value(source_type)
    canonical = normalize_source_url(url)
    if source_type_from_url(canonical) != source_type:
        raise ValueError("SOURCE_URL_TYPE_MISMATCH")
    return "src_" + uuid.uuid5(uuid.NAMESPACE_URL,
        "|".join((source_type, canonical, research_category or ""))).hex


def _mapping(url: str, mapping: dict | None) -> tuple[str | None, dict | None]:
    value = (mapping or {}).get(url) or (mapping or {}).get(browse_node_id(url))
    if value is None:
        return None, None
    if not isinstance(value, dict) or value.get("research_category") not in RESEARCH_CATEGORIES:
        raise ValueError("EXPLICIT_RESEARCH_MAPPING_REQUIRED")
    if not value.get("evidence"):
        raise ValueError("RESEARCH_MAPPING_EVIDENCE_REQUIRED")
    return value["research_category"], deepcopy(value)


def discover_sources(html: str, source_type: str, page_url: str, *,
                     research_mapping: dict | None = None,
                     evidence_path: str | None = None,
                     observed_at: str | None = None,
                     origin: str = "OFFLINE_FIXTURE") -> list[dict]:
    """Discover actual navigation links; never manufacture sibling list URLs."""
    source_type = source_type_value(source_type)
    if origin not in ORIGINS:
        raise ValueError("UNKNOWN_ORIGIN")
    if source_type_from_url(page_url) != source_type:
        raise ValueError("SOURCE_URL_TYPE_MISMATCH")
    soup = BeautifulSoup(html or "", "lxml")
    navigation = soup.select("#zg_browseRoot, .zg-browse-root, .zg-browse-group, nav")
    # The existing saved Best Sellers pages expose zg_browseRoot. A document
    # without navigation may still expose explicitly marked category links.
    anchors = [anchor for nav in navigation for anchor in nav.select("a[href]")]
    anchors.extend(soup.select("a[data-category-link='true'][href]"))
    breadcrumbs = [node.get_text(" ", strip=True) for node in soup.select(
        "[aria-label='breadcrumb'] a, [aria-label='Breadcrumb'] a, .a-breadcrumb a")]
    result, seen = [], set()
    for anchor in anchors:
        raw_href = str(anchor.get("href") or "")
        url = normalize_source_url(raw_href, base_url=page_url)
        label = anchor.get_text(" ", strip=True)
        if source_type_from_url(url) != source_type or not label or url in seen:
            continue
        # Pagination is separate; its visible number does not name a category.
        if label.isdigit() or anchor.find_parent(class_=re.compile("pagination")):
            continue
        if any(key == "pg" for key, _ in parse_qsl(urlsplit(url).query)):
            continue
        seen.add(url)
        research, mapping_evidence = _mapping(url, research_mapping)
        path = list(dict.fromkeys(breadcrumbs + [label]))
        result.append({"source_id": source_id(source_type, url, research),
            "source_type": source_type, "root_url": SOURCE_ROOTS[source_type],
            "actual_source_url": url, "research_category": research,
            "amazon_category": label, "browse_node_id": browse_node_id(url),
            "category_path": path, "source_status": "UNVERIFIED", "enabled": False,
            "discovery_evidence": [{"kind": "NAVIGATION_LINK", "origin": origin,
                "page_url": page_url, "raw_href": raw_href, "label_raw": label,
                "evidence_path": evidence_path, "evidence_sha256": digest(html.encode("utf-8")),
                "observed_at": timestamp(observed_at)["observed_at"]}],
            "mapping_evidence": mapping_evidence, "last_verified_at": None,
            "adapter_version": None, "access_profile": "SERIAL_CONSERVATIVE",
            "tested_page_count": 0, "tested_record_count": 0})
    return result


# Friendly API alias for callers following the task's suggested naming.
discovery = discover_sources


class SourceRegistry:
    """Small atomic registry, with reviewed status evidence and revision history."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists() and self.path.is_dir():
            self.path = self.path / "sources.json"

    def _read(self) -> dict:
        if self.path.exists():
            return read_sealed(self.path)
        return {"registry_version": REGISTRY_VERSION, "revision": 0,
                "sources": [], "history": []}

    def list(self, *, enabled: bool | None = None, source_type: str | None = None) -> list[dict]:
        rows = deepcopy(self._read()["sources"])
        if source_type is not None:
            wanted = source_type_value(source_type)
            rows = [row for row in rows if row["source_type"] == wanted]
        if enabled is not None:
            rows = [row for row in rows if bool(row.get("enabled")) == enabled]
        return sorted(rows, key=lambda row: row["source_id"])

    def seed_roots(self) -> list[dict]:
        existing = {row["source_id"] for row in self.list()}
        for kind, url in SOURCE_ROOTS.items():
            if source_id(kind, url) in existing:
                continue
            self.upsert({"source_type": kind, "actual_source_url": url,
                "discovery_evidence": [{"kind": "TASK_CANDIDATE_ROOT",
                    "origin": "USER_CONFIGURATION", "url": url}],
                "source_status": "UNVERIFIED", "enabled": False})
        return self.list()

    def upsert(self, source: dict) -> dict:
        row = deepcopy(source)
        kind = source_type_value(row["source_type"])
        url = normalize_source_url(row.get("actual_source_url") or row.get("root_url") or "")
        if source_type_from_url(url) != kind:
            raise ValueError("SOURCE_URL_TYPE_MISMATCH")
        research = row.get("research_category")
        if research is not None and research not in RESEARCH_CATEGORIES:
            raise ValueError("UNKNOWN_RESEARCH_CATEGORY")
        row_id = source_id(kind, url, research)
        if row.get("source_id") not in {None, row_id}:
            raise ValueError("SOURCE_ID_MISMATCH")
        status = row.get("source_status", "UNVERIFIED")
        if status not in SOURCE_STATUSES:
            raise ValueError("UNKNOWN_SOURCE_STATUS")
        if research and not row.get("mapping_evidence"):
            raise ValueError("RESEARCH_MAPPING_EVIDENCE_REQUIRED")
        if status == "AVAILABLE":
            proof = row.get("verification_evidence") or {}
            if not (proof.get("origin") == "LIVE_CAPTURE" and proof.get("access_status") == "NORMAL"
                    and proof.get("recognized_page_type") is True and proof.get("evidence_sha256")
                    and proof.get("actual_count", 0) > 0 and row.get("last_verified_at")
                    and proof.get("source_context_valid") is True
                    and proof.get("category_navigation_valid") is not False):
                raise ValueError("LIVE_SOURCE_VERIFICATION_REQUIRED")
            proof_path = Path(proof.get("evidence_path") or "")
            if not proof_path.is_file() or digest(proof_path.read_bytes()) != proof["evidence_sha256"]:
                raise ValueError("LIVE_SOURCE_VERIFICATION_EVIDENCE_INVALID")
            _validate_live_receipt(proof, row_id=row_id, source_url=url)
        if status == "UNSUPPORTED":
            proof = deepcopy(row.get("status_evidence") or row.get("unsupported_evidence") or {})
            if not (proof.get("reviewed_by") and proof.get("reason") and proof.get("evidence_path")
                    and proof.get("conclusion") == "UNSUPPORTED"):
                raise ValueError("REVIEWED_UNSUPPORTED_EVIDENCE_REQUIRED")
            proof_path = Path(proof["evidence_path"])
            if (not proof_path.is_file() or not proof_path.stat().st_size
                    or (proof.get("evidence_sha256") and
                        digest(proof_path.read_bytes()) != proof["evidence_sha256"])):
                raise ValueError("UNSUPPORTED_EVIDENCE_INVALID")
            proof["evidence_sha256"] = digest(proof_path.read_bytes())
            # Keep the original public status field and the QA/report field in
            # sync; downstream consumers must not lose the reviewed evidence.
            row["status_evidence"] = proof
            row["unsupported_evidence"] = deepcopy(proof)
        defaults = {"source_id": row_id, "source_type": kind, "root_url": SOURCE_ROOTS[kind],
            "actual_source_url": url, "research_category": research,
            "amazon_category": None, "browse_node_id": browse_node_id(url),
            "category_path": [], "source_status": status, "enabled": False,
            "discovery_evidence": [], "mapping_evidence": None, "last_verified_at": None,
            "adapter_version": None, "access_profile": "SERIAL_CONSERVATIVE",
            "tested_page_count": 0, "tested_record_count": 0}
        defaults.update(row)
        defaults.update(source_id=row_id, source_type=kind, actual_source_url=url,
                        root_url=SOURCE_ROOTS[kind])
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            state = self._read()
            previous = next((item for item in state["sources"] if item["source_id"] == row_id), None)
            if previous == defaults:
                return deepcopy(defaults)
            state["sources"] = [item for item in state["sources"] if item["source_id"] != row_id] + [defaults]
            state["revision"] += 1
            state["history"].append({"revision": state["revision"], "source_id": row_id,
                "previous": previous, "new": deepcopy(defaults),
                "changed_at": timestamp()["observed_at"]})
            atomic_json(self.path, sealed(state), backup=True)
        return deepcopy(defaults)

    def verify(self, source_id: str, parsed_result: dict, *, origin: str,
               observed_at: str, evidence_path: str,
               capture_receipt_path: str | None = None) -> dict:
        if origin not in ORIGINS:
            raise ValueError("UNKNOWN_ORIGIN")
        row = next((item for item in self.list() if item["source_id"] == source_id), None)
        if row is None:
            raise KeyError(source_id)
        page = parsed_result["page"]
        if page.get("origin") != origin:
            raise ValueError("SOURCE_VERIFICATION_ORIGIN_MISMATCH")
        evidence = Path(evidence_path)
        if not evidence.is_file() or digest(evidence.read_bytes()) != page.get("evidence_sha256"):
            raise ValueError("SOURCE_VERIFICATION_EVIDENCE_INVALID")
        if page.get("source_type") != row["source_type"] or page.get("source_id") != source_id:
            raise ValueError("SOURCE_VERIFICATION_CONTEXT_MISMATCH")
        proof = {"origin": origin, "observed_at": timestamp(observed_at)["observed_at"],
                 "evidence_path": str(evidence), "evidence_sha256": page["evidence_sha256"],
                 "access_status": page.get("access_status"),
                 "recognized_page_type": page.get("recognized_page_type"),
                 "actual_count": page.get("actual_count", 0), "page_status": page.get("status"),
                 "source_context_valid": page.get("source_context_valid"),
                 "category_navigation_valid": page.get("category_navigation_valid"),
                 "page_url": page.get("page_url"), "flags": page.get("flags", [])}
        if proof["observed_at"] != page.get("observed_at"):
            raise ValueError("SOURCE_VERIFICATION_TIMESTAMP_MISMATCH")
        if origin == "LIVE_CAPTURE":
            proof["capture_receipt_path"] = capture_receipt_path
            _validate_live_receipt(proof, row_id=source_id, source_url=row["actual_source_url"])
            proof["capture_receipt_sha256"] = digest(Path(capture_receipt_path).read_bytes())
        if proof in row.get("verification_history", []):
            return row
        row.setdefault("verification_history", []).append(proof)
        row["tested_page_count"] += 1
        row["tested_record_count"] += page.get("actual_count", 0)
        row["adapter_version"] = page.get("parser_version")
        if origin == "LIVE_CAPTURE":
            row["verification_evidence"] = proof
            row["last_verified_at"] = proof["observed_at"]
            if page.get("access_status") in {"CHALLENGE", "BLOCKED", "RATE_LIMITED", "CAPTCHA"}:
                row["source_status"] = "BLOCKED"
            elif (page.get("access_status") == "NORMAL" and page.get("recognized_page_type")
                    and page.get("actual_count", 0) > 0 and page.get("source_context_valid") is True
                    and page.get("category_navigation_valid") is not False
                    and not {"CATEGORY_NAVIGATION_MISMATCH", "PAGE_SOURCE_CATEGORY_MISMATCH",
                             "PAGINATION_CATEGORY_MISMATCH"}.intersection(page.get("flags", []))):
                row["source_status"] = "AVAILABLE"
            else:
                row["source_status"] = "TEMPORARILY_UNAVAILABLE"
        return self.upsert(row)

    def set_enabled(self, source_id: str, enabled: bool) -> dict:
        row = next((item for item in self.list() if item["source_id"] == source_id), None)
        if row is None:
            raise KeyError(source_id)
        row["enabled"] = bool(enabled)
        return self.upsert(row)

    def coverage(self) -> dict:
        rows = self.list()
        matrix = []
        for kind in SOURCE_ROOTS:
            for category in RESEARCH_CATEGORIES:
                matched = [row for row in rows if row["source_type"] == kind
                           and row.get("research_category") == category]
                statuses = {row["source_status"] for row in matched}
                if "AVAILABLE" in statuses:
                    status = "AVAILABLE"
                elif statuses and statuses <= {"UNSUPPORTED"}:
                    status = "UNSUPPORTED"
                elif "BLOCKED" in statuses:
                    status = "BLOCKED"
                elif "TEMPORARILY_UNAVAILABLE" in statuses:
                    status = "TEMPORARILY_UNAVAILABLE"
                else:
                    status = "UNVERIFIED"
                proofs = {"discovery_evidence": [], "verification_evidence": [],
                          "unsupported_evidence": []}
                for registered in matched:
                    for field in proofs:
                        value = registered.get(field)
                        if field == "unsupported_evidence" and not value:
                            value = registered.get("status_evidence")
                        values = value if isinstance(value, list) else [value] if value else []
                        for proof in values:
                            # Context belongs to each contributing source, not
                            # merely to an aggregate combination's status label.
                            proofs[field].append({**deepcopy(proof),
                                "source_id": registered["source_id"],
                                "actual_source_url": registered["actual_source_url"]})
                matrix.append({"source_type": kind, "research_category": category,
                    "source_status": status, "status": status,
                    "source_ids": [row["source_id"] for row in matched],
                    "actual_urls": [row["actual_source_url"] for row in matched],
                    **proofs,
                    "logical_combination": True})
        return {"schema_version": 1, "total": len(matrix), "combinations": matrix,
                "counts": {status: sum(row["status"] == status for row in matrix)
                           for status in sorted(SOURCE_STATUSES)},
                "registered_source_count": len(rows)}
