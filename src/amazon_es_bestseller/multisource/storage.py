"""Atomic file storage and OS-released cross-process locks."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical_bytes(value)).hexdigest()


def atomic_json(path: str | Path, value, *, backup: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value)
    if backup and path.exists():
        previous = path.read_bytes()
        try:
            parsed = json.loads(previous)
            valid = not (isinstance(parsed, dict) and "payload_sha256" in parsed) or (
                digest(parsed.get("payload")) == parsed["payload_sha256"])
        except (ValueError, TypeError):
            valid = False
        if valid:
            _atomic_bytes(path.with_suffix(path.suffix + ".bak"), previous)
    _atomic_bytes(path, data)


def _atomic_bytes(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def immutable_bytes(path: str | Path, data: bytes) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("IMMUTABLE_EVIDENCE_CONFLICT: " + str(path))
        return False
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Exclusive publication of a complete inode; never replace a rival.
            os.link(temporary, path)
            return True
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("IMMUTABLE_EVIDENCE_CONFLICT: " + str(path))
            return False
    finally:
        os.unlink(temporary)


def immutable_json(path: str | Path, value) -> bool:
    return immutable_bytes(path, canonical_bytes(value))


@contextmanager
def file_lock(path: str | Path):
    """The OS releases the lock after a crash; no unsafe stale-PID deletion."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def sealed(value: dict, *, schema_version: int = 1) -> dict:
    return {"schema_version": schema_version, "payload_sha256": digest(value), "payload": value}


def read_sealed(path: str | Path, *, recover_backup: bool = False) -> dict:
    path = Path(path)
    candidates = [path, path.with_suffix(path.suffix + ".bak")] if recover_backup else [path]
    errors = []
    for candidate in candidates:
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1 or digest(value["payload"]) != value["payload_sha256"]:
                raise ValueError("SCHEMA_OR_CHECKSUM_MISMATCH")
            return value["payload"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
    raise ValueError("CORRUPT_PERSISTED_STATE: " + "; ".join(errors))
