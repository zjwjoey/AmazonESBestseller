"""Shared active-cache accounting and verifiable, content-addressed gzip archival."""
from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import uuid
from pathlib import Path

from .task_workspace import TaskWorkspace, utc_now


class CacheCapacityExceeded(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evidence_path(path) -> Path:
    path = Path(path)
    if path.is_file():
        return path
    if path.parent.name == "active" and path.parent.parent.name == "cache":
        archive = path.parents[2] / "evidence" / "archive" / (path.name + ".gz")
        if archive.is_file():
            return archive
    sibling = path.with_name(path.name + ".gz")
    return sibling if sibling.is_file() else path


def read_evidence(path, expected: str | None = None) -> bytes:
    requested = Path(path)
    resolved = evidence_path(requested)
    if resolved.suffix == ".gz":
        with gzip.open(resolved, "rb") as stream:
            data = stream.read()
    else:
        data = resolved.read_bytes()
    name = requested.name.split(".")[0]
    if expected is None and len(name) == 64 and all(char in "0123456789abcdef" for char in name):
        expected = name
    if expected and sha256(data) != expected:
        raise ValueError("EVIDENCE_SHA256_MISMATCH:" + str(requested))
    return data


def _file_digest(path: Path, *, compressed=False) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    opener = gzip.open if compressed else open
    with opener(path, "rb") as handle:
        while block := handle.read(256 * 1024):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


class _BoundedOutput:
    def __init__(self, stream, remaining):
        self.stream = stream
        self.remaining = remaining

    def write(self, data):
        if len(data) > self.remaining:
            raise CacheCapacityExceeded("ACTIVE_CACHE_HARD_LIMIT")
        written = self.stream.write(data)
        self.remaining -= written
        return written


class EvidenceStore:
    def __init__(self, workspace: TaskWorkspace):
        self.workspace = workspace
        self.active = workspace.root / "cache" / "active"
        self.temporary = workspace.root / "cache" / "temporary"
        self.archive_root = workspace.root / "evidence" / "archive"
        for folder in (self.active, self.temporary, self.archive_root):
            folder.mkdir(parents=True, exist_ok=True)
        with workspace.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS evidence (
                digest TEXT PRIMARY KEY, raw_size INTEGER NOT NULL,
                archive_hash TEXT, archive_size INTEGER, archived_at TEXT,
                algorithm TEXT, archive_path TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS evidence_audits (
                digest TEXT NOT NULL, stage TEXT NOT NULL, unit_id TEXT NOT NULL,
                audit_id TEXT NOT NULL, status TEXT NOT NULL,
                parser_version TEXT NOT NULL, qa_version TEXT NOT NULL,
                PRIMARY KEY(digest, stage, unit_id, audit_id))""")
            conn.execute("CREATE TABLE IF NOT EXISTS cache_peak (id INTEGER PRIMARY KEY, bytes INTEGER NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO cache_peak VALUES(1, 0)")

    def _bytes(self):
        return sum(p.stat().st_size for root in (self.active, self.temporary)
                   for p in root.rglob("*") if p.is_file())

    def _peak(self, conn, total):
        conn.execute("UPDATE cache_peak SET bytes = max(bytes, ?) WHERE id = 1", (total,))

    def capacity(self):
        with self.workspace.transaction() as conn:
            used = self._bytes()
            self._peak(conn, used)
            peak = conn.execute("SELECT bytes FROM cache_peak WHERE id = 1").fetchone()[0]
        cfg = self.workspace.manifest
        state = ("PAUSE" if used >= cfg["cache_limit_bytes"]
                 else "COMPACT" if used >= cfg["cache_compact_threshold"]
                 else "WARN" if used >= cfg["cache_warning_threshold"] else "NORMAL")
        return {"active_bytes": used, "peak_bytes": peak, "state": state}

    def _remaining(self, used):
        return self.workspace.manifest["cache_limit_bytes"] - used

    def save(self, data: bytes) -> tuple[Path, str]:
        digest = sha256(data)
        path = self.active / (digest + ".html")
        with self.workspace.transaction() as conn:
            used = self._bytes()
            indexed = conn.execute("SELECT archive_path FROM evidence WHERE digest = ?", (digest,)).fetchone()
            if indexed and indexed["archive_path"]:
                if _file_digest(Path(indexed["archive_path"]), compressed=True) != (digest, len(data)):
                    raise ValueError("ARCHIVE_ORIGINAL_MISMATCH:" + digest)
            elif path.exists():
                if _file_digest(path) != (digest, len(data)):
                    raise ValueError("ACTIVE_EVIDENCE_MISMATCH:" + digest)
            else:
                if len(data) > self._remaining(used):
                    raise CacheCapacityExceeded("ACTIVE_CACHE_HARD_LIMIT")
                temp = self.temporary / (uuid.uuid4().hex + ".capture")
                with temp.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._peak(conn, used + len(data))
                os.replace(temp, path)
            conn.execute("INSERT OR IGNORE INTO evidence(digest,raw_size) VALUES(?,?)", (digest, len(data)))
        return path, digest

    def archive(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("INVALID_EVIDENCE_DIGEST")
        active = self.active / (digest + ".html")
        destination = self.archive_root / (active.name + ".gz")
        with self.workspace.transaction() as conn:
            row = conn.execute("SELECT * FROM evidence WHERE digest = ?", (digest,)).fetchone()
            audited = conn.execute("SELECT 1 FROM evidence_audits WHERE digest = ? LIMIT 1", (digest,)).fetchone()
            if not row or not audited:
                raise ValueError("ARCHIVE_REQUIRES_DURABLE_AUDIT:" + digest)
            expected = (digest, row["raw_size"])
            if active.exists() and _file_digest(active) != expected:
                raise ValueError("ACTIVE_EVIDENCE_MISMATCH:" + digest)
            if not destination.exists():
                used = self._bytes()
                if self._remaining(used) <= 0:
                    raise CacheCapacityExceeded("ACTIVE_CACHE_HARD_LIMIT")
                temp = self.temporary / (digest + ".gzip-part")
                # A previous interrupted compression is disposable only while
                # the complete matching original is still present.
                if not active.is_file():
                    raise ValueError("MISSING_ACTIVE_EVIDENCE:" + digest)
                temp.unlink(missing_ok=True)
                used = self._bytes()
                try:
                    with temp.open("xb") as output, active.open("rb") as source:
                        with gzip.GzipFile(fileobj=_BoundedOutput(output, self._remaining(used)),
                                           filename="", mode="wb", mtime=0) as compressed:
                            shutil.copyfileobj(source, compressed, length=256 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    self._peak(conn, used + temp.stat().st_size)
                    if _file_digest(temp, compressed=True) != expected:
                        raise ValueError("ARCHIVE_ORIGINAL_MISMATCH:" + digest)
                    os.replace(temp, destination)
                finally:
                    temp.unlink(missing_ok=True)
            if _file_digest(destination, compressed=True) != expected:
                raise ValueError("ARCHIVE_ORIGINAL_MISMATCH:" + digest)
            compressed_hash, compressed_size = _file_digest(destination)
            if row["archive_hash"] and row["archive_hash"] != compressed_hash:
                raise ValueError("ARCHIVE_COMPRESSED_MISMATCH:" + digest)
            conn.execute("""UPDATE evidence SET archive_hash=?, archive_size=?, archived_at=coalesce(archived_at,?),
                            algorithm='gzip', archive_path=? WHERE digest=?""",
                         (compressed_hash, compressed_size, utc_now(), str(destination), digest))
        # Index is durable before removing the active copy. A crash between
        # these operations leaves an original that recovery can safely remove.
        with self.workspace.transaction():
            if active.exists():
                if _file_digest(active) != expected:
                    raise ValueError("ACTIVE_EVIDENCE_MISMATCH:" + digest)
                active.unlink()
        return destination

    def recover(self):
        """Complete known archive transactions and report unidentified files."""
        with self.workspace.transaction() as conn:
            rows = {row["digest"]: dict(row) for row in conn.execute("SELECT * FROM evidence")}
            audited = {row[0] for row in conn.execute("SELECT DISTINCT digest FROM evidence_audits")}
        issues = []
        for digest in sorted(audited):
            active = self.active / (digest + ".html")
            published = self.archive_root / (active.name + ".gz")
            if active.is_file() or (published.is_file() and not rows[digest]["archive_path"]):
                try:
                    self.archive(digest)
                except (OSError, ValueError, CacheCapacityExceeded) as error:
                    issues.append({"digest": digest, "reason": str(error)})
        for path in self.active.iterdir():
            if path.name.removesuffix(".html") not in rows:
                issues.append({"path": str(path), "reason": "ORPHAN_ACTIVE"})
        for path in self.temporary.iterdir():
            issues.append({"path": str(path), "reason": "UNRESOLVED_TEMPORARY"})
        for path in self.archive_root.iterdir():
            digest = path.name.removesuffix(".html.gz")
            if digest not in audited:
                issues.append({"path": str(path), "reason": "ARCHIVE_WITHOUT_AUDIT"})
        return issues

    def archive_stats(self, *, verify=False):
        with self.workspace.transaction() as conn:
            archives = [dict(row) for row in conn.execute("SELECT * FROM evidence WHERE archive_path IS NOT NULL")]
        errors = []
        if verify:
            for row in archives:
                try:
                    path = Path(row["archive_path"])
                    if (_file_digest(path) != (row["archive_hash"], row["archive_size"])
                            or _file_digest(path, compressed=True) != (row["digest"], row["raw_size"])):
                        errors.append(row["digest"])
                except (OSError, EOFError):
                    errors.append(row["digest"])
        original = sum(row["raw_size"] for row in archives)
        compressed = sum(row["archive_size"] for row in archives)
        return {"files": len(archives), "original_bytes": original, "archive_bytes": compressed,
                "compression_ratio": compressed / original if original else None, "errors": errors}
