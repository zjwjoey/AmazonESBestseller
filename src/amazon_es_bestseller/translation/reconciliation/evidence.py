"""Read-only observations, not a cross-file atomic snapshot.

TranslationCache.load is deliberately NOT used: corruption recovery can rename
the source. No claim lock is acquired and no original file is ever opened to write.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"(?i)\bsk-[a-z0-9._-]+", "[REDACTED]", value)
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if str(k).casefold() in {
            "authorization", "api_key", "secret", "access_token", "headers", "credentials"}
            else scrub(v)) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [scrub(v) for v in value]
    return value


def _signature(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


class EvidenceReader:
    def __init__(self, roots: list[str]):
        self.roots = [Path(root).resolve() for root in roots]
        if not self.roots:
            raise ValueError("EVIDENCE_ROOTS_REQUIRED")
        self.receipts: dict[str, dict] = {}
        self.values: dict[str, Any] = {}
        self.conflicts: list[dict] = []
        self.changed = False

    def conflict(self, code: str, path: str | Path = "", **facts: Any) -> None:
        self.conflicts.append(scrub({"code": code, "path": str(path), **facts}))

    def resolve(self, value: str | Path) -> Path:
        path = Path(value).resolve()
        if not any(path == root or root in path.parents for root in self.roots):
            raise ValueError("EVIDENCE_PATH_OUTSIDE_ALLOWED_ROOTS")
        if path.name.startswith(".env") or path.suffix.casefold() in {".pem", ".key"}:
            raise ValueError("CREDENTIAL_FILE_NOT_EVIDENCE")
        return path

    def raw(self, value: str | Path, *, optional: bool = False) -> bytes | None:
        path = self.resolve(value)
        identity = str(path)
        if identity in self.receipts:
            return None  # Parsed values are reused through read(), not re-read.
        receipt = {"path": identity, "read_started_at": utc(), "status": "MISSING"}
        self.receipts[identity] = receipt
        try:
            before = _signature(path)
            data = path.read_bytes()
            after = _signature(path)
        except OSError as exc:
            receipt.update(read_finished_at=utc(), error=type(exc).__name__)
            if not optional:
                self.conflict("EVIDENCE_MISSING_OR_UNREADABLE", path, error=type(exc).__name__)
            return None
        consistent = before == after and len(data) == before[0]
        receipt.update(read_finished_at=utc(), size=len(data), sha256=digest(data),
                       signature_before=before, signature_after=after,
                       status="OBSERVED" if consistent else "ACTIVE_PROVISIONAL")
        if not consistent:
            self.changed = True
            self.conflict("CHANGED_DURING_READ", path)
        return data

    def read(self, value: str | Path, *, optional: bool = False,
             expected_hash: str | None = None, jsonl: bool = False) -> Any:
        path = self.resolve(value)
        identity = str(path)
        if identity not in self.values:
            raw = self.raw(path, optional=optional)
            parsed = None
            if raw is not None:
                def pairs(values):
                    result = {}
                    for key, val in values:
                        if key in result:
                            self.conflict("DUPLICATE_JSON_KEY", path, key=key)
                            self.receipts[identity]["status"] = "INVALID"
                        result[key] = val
                    return result
                try:
                    if jsonl:
                        parsed = []
                        lines = raw.splitlines(keepends=True)
                        for index, line in enumerate(lines):
                            if not line.strip():
                                self.conflict("JSONL_EMPTY_LINE", path, line=index + 1)
                                continue
                            try:
                                obj = json.loads(line.decode("utf-8-sig"), object_pairs_hook=pairs)
                                if not isinstance(obj, dict):
                                    raise ValueError("non-object event")
                                parsed.append({**obj, "_evidence_line": index + 1})
                            except (ValueError, UnicodeError):
                                tail = index == len(lines) - 1
                                self.conflict("JSONL_INCOMPLETE_TAIL" if tail else "JSONL_INVALID_LINE",
                                              path, line=index + 1, line_hash=digest(line))
                                if tail:
                                    self.changed = True
                        if lines and not raw.endswith(b"\n"):
                            self.conflict("JSONL_UNTERMINATED_TAIL", path, line=len(lines))
                            self.changed = True
                    else:
                        parsed = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)
                except (ValueError, UnicodeError):
                    self.conflict("EVIDENCE_INVALID_JSON", path)
                    self.receipts[identity]["status"] = "INVALID"
            self.values[identity] = parsed
        if expected_hash is not None and self.receipts[identity].get("sha256") != expected_hash:
            self.conflict("BINDING_HASH_MISMATCH", path, expected=expected_hash,
                          observed=self.receipts[identity].get("sha256"))
            return None
        if self.receipts[identity].get("status") == "INVALID":
            return None
        return self.values[identity]

    def object(self, value: Any, path: str | Path) -> dict:
        """An expected object cannot be replaced by syntactically valid JSON scalars."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.conflict("EVIDENCE_SHAPE_INVALID", path, expected="object",
                          observed=type(value).__name__)
            return {}
        return value

    def bound_object(self, reference: dict) -> dict:
        value = self.bound(reference)
        return self.object(value, reference.get("path", "") if isinstance(reference, dict) else "")

    def read_object(self, value: str | Path, **options: Any) -> dict:
        return self.object(self.read(value, **options), value)

    def bound(self, reference: dict) -> Any:
        if not isinstance(reference, dict) or not reference.get("path") or not reference.get("sha256"):
            self.conflict("BOUND_REFERENCE_INVALID")
            return None
        path = self.resolve(reference["path"])
        if path.suffix not in {".json", ".jsonl"}:
            if str(path) not in self.receipts:
                self.raw(path)
            if self.receipts[str(path)].get("sha256") != reference["sha256"]:
                self.conflict("BINDING_HASH_MISMATCH", path, expected=reference["sha256"],
                              observed=self.receipts[str(path)].get("sha256"))
            return None
        return self.read(path, expected_hash=reference["sha256"], jsonl=path.suffix == ".jsonl")

    def verify_bindings(self, value: Any, *, skip_paths: set[str] | None = None) -> None:
        """Walk only explicit hash bindings, never arbitrary paths or directories."""
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                if str(self.resolve(value["path"])) not in (skip_paths or set()):
                    self.bound(value)
                return
            for key, child in value.items():
                if key not in {"records", "reference_envelopes", "units", "rows", "available_asins"}:
                    self.verify_bindings(child, skip_paths=skip_paths)
        elif isinstance(value, list):
            for child in value:
                self.verify_bindings(child, skip_paths=skip_paths)

    def finish(self) -> bool:
        """Second observation of every file; still no atomicity claim if stable."""
        for identity, receipt in self.receipts.items():
            path = Path(identity)
            receipt["rechecked_at"] = utc()
            try:
                signature = _signature(path)
                hasher = hashlib.sha256()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(block)
                stable = (signature == _signature(path) == tuple(receipt.get("signature_after", ()))
                          and hasher.hexdigest() == receipt.get("sha256"))
                receipt.update(recheck_sha256=hasher.hexdigest(), recheck_size=signature[0], unchanged=stable)
            except OSError:
                stable = receipt.get("status") == "MISSING" and not path.exists()
                receipt["unchanged"] = stable
            if not stable:
                receipt["status"] = "ACTIVE_PROVISIONAL"
                self.changed = True
                self.conflict("CHANGED_ACROSS_OBSERVATION", path)
        return not self.changed
