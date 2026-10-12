"""Append-only response evidence, persisted before parsing or QA.

No headers, credentials or environment values are accepted by this interface.
Each actual transport attempt has its own directory; cache consumers retain
references to these original bytes rather than manufacturing raw responses.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Event


class RawJournalError(RuntimeError):
    """A billed/unknown attempt must be held when evidence cannot be saved."""


class RawResponseJournal:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.halted = Event()

    def save(self, *, response: Any, binding: dict, status_code: int | None,
             request: dict, representation: str = "transport_object") -> dict:
        try:
            attempt_id = uuid.uuid4().hex
            attempt_dir = self.directory / attempt_id
            attempt_dir.mkdir(parents=True, exist_ok=False)
            raw = response if isinstance(response, bytes) else json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
            response_path = attempt_dir / "response.raw"
            digest = hashlib.sha256(raw).hexdigest()
            # Preserve bytes before interpretation. Exclusive writes and fsync
            # make both crash windows and repeated attempts recoverable.
            with response_path.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            metadata_path = attempt_dir / "metadata.json"
            metadata = {"version": "qwen-raw-journal-v1", "attempt_id": attempt_id,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "binding": binding, "request": request, "status_code": status_code,
                        "representation": representation, "response_sha256": digest,
                        "response_path": str(response_path)}
            with metadata_path.open("x", encoding="utf-8") as handle:
                json.dump(metadata, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            return {"attempt_id": attempt_id, "response_path": str(response_path),
                    "metadata_path": str(metadata_path), "response_sha256": digest,
                    "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest()}
        except Exception as exc:
            self.halted.set()
            raise RawJournalError("RAW_RESPONSE_PERSISTENCE_FAILED") from exc

    @staticmethod
    def verify(ref: dict) -> bool:
        try:
            response_path, metadata_path = Path(ref['response_path']), Path(ref['metadata_path'])
            raw = response_path.read_bytes()
            metadata_raw = metadata_path.read_bytes()
            metadata = json.loads(metadata_raw)
            return (hashlib.sha256(raw).hexdigest() == ref['response_sha256'] == metadata['response_sha256']
                    and hashlib.sha256(metadata_raw).hexdigest() == ref['metadata_sha256']
                    and metadata['attempt_id'] == ref['attempt_id'])
        except (OSError, KeyError, ValueError, TypeError):
            return False


def bind_qwen_journals(provider: Any, directory: Path | None) -> bool:
    """Cover direct Qwen, budget wrappers and all configured pool aliases."""
    seen = set()
    real_qwen = []
    shared_journal = []

    def visit(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if getattr(node, "supports_raw_response_journal", False):
            real_qwen.append(node)
            if node.response_journal is None:
                if directory is None:
                    raise RawJournalError("RAW_JOURNAL_DIRECTORY_REQUIRED")
                if not shared_journal:
                    shared_journal.append(RawResponseJournal(directory))
                node.response_journal = shared_journal[0]
            elif not shared_journal:
                shared_journal.append(node.response_journal)
        for name in ("provider", "pool"):
            visit(getattr(node, name, None))
        children = getattr(node, "providers", {})
        if isinstance(children, dict):
            for child in children.values():
                visit(child)
    visit(provider)
    return bool(real_qwen)
