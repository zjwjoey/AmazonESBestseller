"""Field-level translation cache with atomic writes and safe recovery."""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional


class TranslationCache:
    VERSION = 5

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.memory: Dict[str, Dict[str, Any]] = {}
        # ``results`` and ``memory_results`` are intentionally append-only
        # indexes of the first observed provider/QA envelope.  They are not a
        # second cache: derived namespace entries still live in ``entries``.
        # The indexes make a dictionary/schema key change unable to disguise a
        # prior provider attempt as a fresh request.
        self.results: Dict[str, Dict[str, Any]] = {}
        self.memory_results: Dict[str, Dict[str, Any]] = {}
        self.recovered_from_corruption = False
        self.corruption_error: Optional[str] = None
        self._corruption_preserved = False
        self._lock = threading.RLock()
        self._claim_depth = 0
        self._disk_fresh = False
        self._disk_bytes: Optional[bytes] = None
        self._disk_signature = None
        self.resume_admission = None
        self.excluded_attempts = set()
        self.load()

    @contextmanager
    def _claim_lock(self, *, timeout_seconds: float = 10.0):
        # Keep lock order consistent even for callers taking this private lock
        # directly. Nested save() must not acquire a second OS handle/lock.
        with self._lock:
            if self._claim_depth:
                self._claim_depth += 1
                try:
                    yield
                finally:
                    self._claim_depth -= 1
                return
            with self._file_claim_lock(timeout_seconds=timeout_seconds):
                self._claim_depth = 1
                self._disk_fresh = False
                try:
                    yield
                finally:
                    self._claim_depth = 0
                    self._disk_fresh = False

    @contextmanager
    def _file_claim_lock(self, *, timeout_seconds: float = 10.0):
        """Take an OS-owned advisory lock for one check-and-claim transition.

        The lock file is deliberately persistent.  Its presence is not proof of
        ownership: Windows ``msvcrt`` and POSIX ``flock`` hold the byte-range
        lock in the kernel, so an abrupt process exit releases the lock without
        making an uncertain cache claim eligible for another provider send.
        """
        lock_path = self.path.with_name(self.path.name + ".claim.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_seconds
        handle = open(lock_path, "a+b")
        try:
            # A non-empty range makes Windows byte-range locking unambiguous.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("translation cache claim lock timed out: %s" % lock_path)
                    time.sleep(0.02)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @staticmethod
    def key(asin: str, field: str, source_hash: str, provider: str,
            model: str, schema_version: str, prompt_version: str,
            dictionary_version: str = "0", dictionary_hash: str = "",
            structured_schema_version: str = "") -> str:
        return "|".join((asin.upper(), field, source_hash, provider, model,
                          str(dictionary_version), str(dictionary_hash),
                          schema_version, str(structured_schema_version), prompt_version))

    @staticmethod
    def result_key(asin: str, field: str, source_hash: str) -> str:
        """Stable per-ASIN raw-result identity, independent of render namespace."""
        return "|".join((str(asin or "").upper(), str(field or ""), str(source_hash or "")))

    @staticmethod
    def memory_result_key(source_text: str, source_language: str, target_language: str,
                          field_type: str, provider: str, model: str) -> str:
        import hashlib
        digest = hashlib.sha256(str(source_text or "").encode("utf-8")).hexdigest()
        return "|".join((digest, source_language, target_language, field_type, provider, model))

    @staticmethod
    def _signature(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _read_disk(self):
        try:
            with self.path.open("rb") as handle:
                raw = handle.read()
                signature = self._signature(os.fstat(handle.fileno()))
            return raw, signature
        except FileNotFoundError:
            return None, None

    @staticmethod
    def _decode(raw: bytes) -> dict:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and "entries" in data:
            return {name: dict(data.get(name) or {})
                    for name in ("entries", "memory", "results", "memory_results")}
        return {"entries": dict(data) if isinstance(data, dict) else {},
                "memory": {}, "results": {}, "memory_results": {}}

    def _install(self, payload: dict) -> None:
        for name in ("entries", "memory", "results", "memory_results"):
            setattr(self, name, payload[name])

    def load(self) -> None:
        with self._lock:
            self._load_locked()

    def _load_locked(self) -> None:
        if not self.path.exists():
            self._disk_bytes = None
            self._disk_signature = None
            self._disk_fresh = bool(self._claim_depth)
            return
        try:
            raw, signature = self._read_disk()
            if raw is None:
                return
            # load() deliberately discards unsaved local edits (legacy API).
            # Release its obsolete byte baseline before decoding the new tree.
            self._disk_bytes = raw
            self._install(self._decode(raw))
            self._disk_signature = signature
            self._disk_fresh = bool(self._claim_depth)
        except (OSError, ValueError, TypeError) as exc:
            self.recovered_from_corruption = True
            self.corruption_error = str(exc)
            # Preserve the bad evidence; never delete or silently overwrite it.
            stamp = str(time.time_ns())
            corrupt = self.path.with_name(self.path.name + ".corrupt-" + stamp)
            try:
                self.path.replace(corrupt)
                self._corruption_preserved = True
            except OSError:
                # Do not later overwrite a malformed cache if its evidence
                # could not be moved out of the way.
                self._corruption_preserved = False
            self.entries = {}
            self._disk_bytes = None
            self._disk_signature = None
            self._disk_fresh = False

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self.entries.get(key)
            return deepcopy(value) if isinstance(value, dict) else None

    def put(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            snapshot = deepcopy(value)
            self.entries[key] = snapshot
            identity = self.result_key(snapshot.get("asin", ""), snapshot.get("field", ""),
                                       snapshot.get("source_hash", ""))
            # Do not overwrite the raw provider/QA evidence selected by the
            # first attempt with a later namespace render or retry envelope.
            if (all(identity.split("|")) and identity not in self.results
                    and snapshot.get("translation_status") != "pending"
                    and snapshot.get("resolution_source") != "immutable_cache_namespace_reuse"):
                self.results[identity] = deepcopy(snapshot)

    def claim(self, key: str, pending: Dict[str, Any], *, resume_key: str = "") -> tuple[bool, Dict[str, Any]]:
        """Durably publish an uncertain provider attempt before any send.

        ``False`` means another process or a previous crashed attempt already
        owns the key. Callers must not retry it automatically.
        """
        with self._lock, self._claim_lock():
            self.load()
            existing = self.entries.get(key)
            if isinstance(existing, dict):
                if resume_key and self.resume_eligible(resume_key):
                    # Field rendering is shared; the semantic claim below is
                    # the atomic one-use ownership boundary. Keep this field.
                    return True, deepcopy(existing)
                return False, deepcopy(existing)
            snapshot = deepcopy(pending)
            snapshot.setdefault("translation_status", "pending")
            snapshot.setdefault("attempt_state", "claimed")
            snapshot.setdefault("claim_id", uuid.uuid4().hex)
            self.entries[key] = snapshot
            self.save()
            return True, deepcopy(snapshot)

    def settle(self, key: str, value: Dict[str, Any]) -> None:
        """Persist a provider result immediately; never replace first raw evidence."""
        with self._lock, self._claim_lock():
            self.load()
            self.put(key, value)
            self.save()

    def find_result(self, asin: str, field: str, source_hash: str) -> Optional[Dict[str, Any]]:
        """Find immutable evidence even when its derived cache key changed.

        The entry scan keeps old entries-only JSON caches usable after a
        process restart without rewriting or trusting a synthetic migration.
        """
        identity = self.result_key(asin, field, source_hash)
        with self._lock:
            value = self.results.get(identity)
            if isinstance(value, dict):
                return deepcopy(value)
            for candidate in self.entries.values():
                if not isinstance(candidate, dict):
                    continue
                if self.result_key(candidate.get("asin", ""), candidate.get("field", ""),
                                   candidate.get("source_hash", "")) == identity:
                    return deepcopy(candidate)
        return None

    @staticmethod
    def memory_key(source_text: str, source_language: str, target_language: str,
                   field_type: str, provider: str, model: str,
                   schema_version: str, prompt_version: str,
                   dictionary_version: str = "v1", dictionary_hash: str = "",
                   structured_schema_version: str = "") -> str:
        stable = TranslationCache.memory_result_key(
            source_text, source_language, target_language, field_type, provider, model)
        return "|".join((stable, dictionary_version, str(dictionary_hash), schema_version,
                          str(structured_schema_version), prompt_version))

    def get_memory(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self.memory.get(key)
            return deepcopy(value) if isinstance(value, dict) else None

    def put_memory(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            snapshot = deepcopy(value)
            existing = self.memory.get(key) or {}
            for marker in ('resume_prior_claim', 'resume_admission_id', 'claim_id'):
                if marker in existing:
                    snapshot.setdefault(marker, deepcopy(existing[marker]))
            self.memory[key] = snapshot
            # The first six components are the canonical TM context.  It does
            # not include ASIN by design, but it does include field type and
            # language pair so unlike fields never cross-contaminate.
            parts = key.split("|")
            if len(parts) >= 6:
                identity = "|".join(parts[:6])
                prior = self.memory_results.get(identity)
                if not isinstance(prior, dict) or prior.get("translation_status") == "pending":
                    self.memory_results[identity] = deepcopy(snapshot)

    def resume_eligible(self, key: str) -> bool:
        admission = self.resume_admission
        return bool(admission and admission.allows(
            key, self.memory.get(key), self.memory_results.get('|'.join(key.split('|')[:6]))))

    def claim_memory(self, key: str, pending: Dict[str, Any], *, replace_terminal: bool = False) -> tuple[bool, Dict[str, Any]]:
        """Durably reserve one canonical TM unit before provider entry."""
        with self._lock, self._claim_lock():
            if "|".join(key.split("|")[:6]) in self.excluded_attempts:
                return False, {"translation_status": "pending", "qa_status": "review_required",
                               "qa_issues": [{"code": "PARENT_PROVIDER_ATTEMPT_HOLD"}]}
            self.load()
            existing = self.memory.get(key)
            resuming = self.resume_eligible(key)
            if (isinstance(existing, dict) and not (replace_terminal and
                    existing.get("translation_status") in {"partial", "failed", "qa_failed"}) and not resuming):
                return False, deepcopy(existing)
            if self.resume_admission and not resuming:
                return False, deepcopy(existing or {})
            snapshot = deepcopy(pending)
            snapshot.setdefault("translation_status", "pending")
            snapshot.setdefault("attempt_state", "claimed")
            snapshot.setdefault("claim_id", uuid.uuid4().hex)
            if resuming:
                snapshot['resume_prior_claim'] = deepcopy(existing)
                snapshot['resume_admission_id'] = self.resume_admission.identity
            self.memory[key] = snapshot
            self.save()
            return True, deepcopy(snapshot)

    def settle_memory(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock, self._claim_lock():
            self.load()
            self.put_memory(key, value)
            self.save()

    def wait_memory_settlement(self, key: str, *, timeout_seconds: float = 2.0) -> Optional[Dict[str, Any]]:
        """Join a live claimant, but leave a crashed/unknown claim pending."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                # Metadata is a polling hint only, never admission authority.
                # Claims/settlements always load actual bytes under the OS lock.
                try:
                    signature = self._signature(self.path.stat())
                except FileNotFoundError:
                    signature = None
                if signature != self._disk_signature:
                    self.load()
                value = self.memory.get(key)
                if isinstance(value, dict) and value.get("translation_status") != "pending":
                    return deepcopy(value)
            time.sleep(0.02)
        return None

    def find_memory_result(self, source_text: str, source_language: str, target_language: str,
                           field_type: str, provider: str, model: str) -> Optional[Dict[str, Any]]:
        identity = self.memory_result_key(source_text, source_language, target_language,
                                          field_type, provider, model)
        with self._lock:
            value = self.memory_results.get(identity)
            if isinstance(value, dict):
                return deepcopy(value)
            # Compatibility with V1--V3 memory-only cache payloads.
            for key, candidate in self.memory.items():
                if key.startswith(identity + "|") and isinstance(candidate, dict):
                    return deepcopy(candidate)
        if identity in self.excluded_attempts:
            return {"translation_status": "pending", "qa_status": "review_required",
                    "qa_issues": [{"code": "PARENT_PROVIDER_ATTEMPT_HOLD"}]}
        return None

    def _payload(self) -> dict:
        # The RLock protects serialization; API reads return isolated envelopes.
        # Only the four outer references are needed, not four whole-tree copies.
        return {"cache_version": self.VERSION, "entries": self.entries,
                "memory": self.memory, "results": self.results,
                "memory_results": self.memory_results}

    @staticmethod
    def _encode(payload: dict) -> bytes:
        # Avoid retaining the JSON fragment list, joined Unicode string and
        # full byte buffer at once. Keep the old text-mode newline bytes.
        buffer = io.BytesIO()
        encoder = json.JSONEncoder(ensure_ascii=False, indent=2)
        for fragment in encoder.iterencode(payload):
            buffer.write(fragment.replace("\n", os.linesep).encode("utf-8"))
        return buffer.getvalue()

    def _merge_external(self, external: dict) -> dict:
        baseline = self._decode(self._disk_bytes) if self._disk_bytes is not None else {
            name: {} for name in ("entries", "memory", "results", "memory_results")}
        missing = object()
        for name in baseline:
            local = getattr(self, name)
            previous = baseline[name]
            remote = external[name]
            for key in local.keys() | previous.keys():
                value, prior = local.get(key, missing), previous.get(key, missing)
                if value == prior:
                    continue
                current = remote.get(key, missing)
                if current != prior and current != value:
                    # No LWW merge of conflicting attempts, original QA or raw
                    # candidates. Leave the on-disk evidence unchanged.
                    raise RuntimeError("CACHE_CONCURRENT_UPDATE_CONFLICT:" + name)
                if value is missing:
                    remote.pop(key, None)
                else:
                    remote[key] = value
        return external

    def save(self) -> None:
        with self._lock, self._claim_lock():
            if self.recovered_from_corruption and not self._corruption_preserved and self.path.exists():
                raise RuntimeError("corrupt translation cache could not be preserved: %s" % self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            raw, signature = ((self._disk_bytes, self._disk_signature) if self._disk_fresh
                              else self._read_disk())
            encoded = self._encode(self._payload())
            if raw is not None and raw != self._disk_bytes:
                try:
                    external = self._decode(raw)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("CACHE_EXTERNAL_EVIDENCE_UNREADABLE") from exc
                if encoded != self._disk_bytes:
                    external = self._merge_external(external)
                self._install(external)
                self._disk_bytes = raw
                self._disk_signature = signature
                encoded = self._encode(self._payload())
            if encoded == raw:
                self._disk_bytes = raw
                self._disk_signature = signature
                self._disk_fresh = True
                return
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".tmp-", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
                self._disk_bytes = encoded
                self._disk_signature = self._signature(self.path.stat())
                self._disk_fresh = True
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

    def __len__(self) -> int:
        return len(self.entries)
