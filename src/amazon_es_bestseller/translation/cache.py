"""Field-level translation cache with atomic writes and safe recovery."""
from __future__ import annotations

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
        self.load()

    @contextmanager
    def _claim_lock(self):
        """Small cross-process lock for check-and-claim transitions only."""
        lock_path = self.path.with_name(self.path.name + ".claim.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10.0
        descriptor = None
        while descriptor is None:
            try:
                descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(descriptor, (str(os.getpid()) + "\n").encode("ascii"))
                os.fsync(descriptor)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("translation cache claim lock timed out: %s" % lock_path)
                time.sleep(0.02)
            except Exception:
                if descriptor is not None:
                    os.close(descriptor)
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                raise
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

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

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "entries" in data:
                self.entries = dict(data.get("entries") or {})
                self.memory = dict(data.get("memory") or {})
                self.results = dict(data.get("results") or {})
                self.memory_results = dict(data.get("memory_results") or {})
            else:
                # Backward-compatible V1 cache shape: old records remain
                # field entries; no implicit migration is attempted.
                self.entries = dict(data) if isinstance(data, dict) else {}
                self.memory = {}
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

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self.entries.get(key)
            return dict(value) if isinstance(value, dict) else None

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

    def claim(self, key: str, pending: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
        """Durably publish an uncertain provider attempt before any send.

        ``False`` means another process or a previous crashed attempt already
        owns the key. Callers must not retry it automatically.
        """
        with self._lock, self._claim_lock():
            self.load()
            existing = self.entries.get(key)
            if isinstance(existing, dict):
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
            return dict(value) if isinstance(value, dict) else None

    def put_memory(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            snapshot = deepcopy(value)
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

    def claim_memory(self, key: str, pending: Dict[str, Any], *, replace_terminal: bool = False) -> tuple[bool, Dict[str, Any]]:
        """Durably reserve one canonical TM unit before provider entry."""
        with self._lock, self._claim_lock():
            self.load()
            existing = self.memory.get(key)
            if (isinstance(existing, dict) and not (replace_terminal and
                    existing.get("translation_status") in {"partial", "failed", "qa_failed"})):
                return False, deepcopy(existing)
            snapshot = deepcopy(pending)
            snapshot.setdefault("translation_status", "pending")
            snapshot.setdefault("attempt_state", "claimed")
            snapshot.setdefault("claim_id", uuid.uuid4().hex)
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
        return None

    def save(self) -> None:
        with self._lock:
            if self.recovered_from_corruption and not self._corruption_preserved and self.path.exists():
                raise RuntimeError("corrupt translation cache could not be preserved: %s" % self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"cache_version": self.VERSION, "entries": deepcopy(self.entries),
                       "memory": deepcopy(self.memory), "results": deepcopy(self.results),
                       "memory_results": deepcopy(self.memory_results)}
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".tmp-", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

    def __len__(self) -> int:
        return len(self.entries)
