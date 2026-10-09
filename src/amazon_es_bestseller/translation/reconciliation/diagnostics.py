"""Measure the EXISTING cache on synthetic data in independent directories."""
from __future__ import annotations

import json
import tempfile
import time
import tracemalloc
from pathlib import Path

from ..cache import TranslationCache
from ..providers.base import ProviderResponse, TranslationProvider
from ..service import TranslationService
from .evidence import digest, utc


class StoppedFakeProvider(TranslationProvider):
    name = "fake-stopped"
    model = "offline"

    def __init__(self):
        self.entries = 0

    def translate(self, _text, **_kwargs):
        self.entries += 1
        return ProviderResponse(provider=self.name, model=self.model, status="pending",
                                error="FAKE_STOP_NEW_SENDS", attempts=0)


class MeasuredCache(TranslationCache):
    def __init__(self, path):
        self.loads = self.saves = self.read_bytes = self.written_bytes = 0
        super().__init__(path)

    def load(self):
        self.loads += 1
        if self.path.exists():
            self.read_bytes += self.path.stat().st_size
        super().load()

    def save(self):
        super().save()
        self.saves += 1
        self.written_bytes += self.path.stat().st_size


def benchmark(output: str | Path, *, sizes=(100, 1000, 5000), operations: int = 3,
              stopped_records: int = 20) -> dict:
    """No real source, live cache, ProviderPool or Qwen configuration is touched."""
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError("DIAGNOSTICS_OUTPUT_ALREADY_EXISTS")
    out.mkdir(parents=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="synthetic-cache-", dir=out) as directory:
        for count in sizes:
            path = Path(directory) / f"cache-{count}.json"
            cache = MeasuredCache(path)
            payload = {"translation_status": "success", "qa_status": "pass", "provider": "fake",
                       "candidate_text": "synthetic " * 8, "translated_text": "合成样本 " * 8}
            for index in range(count):
                key = TranslationCache.memory_key(str(index), "es", "zh-CN", "title", "fake", "offline", "v1", "p1")
                cache.memory[key] = dict(payload)
                cache.memory_results["|".join(key.split("|")[:6])] = dict(payload)
            cache.save()
            original_size = path.stat().st_size
            cache.loads = cache.saves = cache.read_bytes = cache.written_bytes = 0
            tracemalloc.start()
            start = time.perf_counter()
            for index in range(operations):
                key = TranslationCache.memory_key(f"new-{index}", "es", "zh-CN", "title", "fake", "offline", "v1", "p1")
                cache.claim_memory(key, {"translation_status": "pending"})
                cache.settle_memory(key, payload)
            seconds = time.perf_counter() - start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            rows.append({"scenario": "claim_and_settle", "synthetic_memory_entries": count,
                "starting_cache_bytes": original_size, "operations": operations, "elapsed_seconds": seconds,
                "seconds_per_operation": seconds / operations, "loads": cache.loads, "saves": cache.saves,
                "read_bytes": cache.read_bytes, "written_bytes": cache.written_bytes, "peak_traced_bytes": peak})
        cache = MeasuredCache(Path(directory) / "stopped.json")
        provider = StoppedFakeProvider()
        service = TranslationService(provider, cache, max_fields=["title_es_raw"])
        start = time.perf_counter()
        result = service.translate_records([{"asin": f"B{index:09d}", "title_es_raw": f"Objeto pendiente {index}"}
                                           for index in range(stopped_records)])
        rows.append({"scenario": "stopped_fake_provider_pending_drain", "records": stopped_records,
                     "provider_entries": provider.entries, "http_attempts": 0,
                     "loads": cache.loads, "saves": cache.saves, "written_bytes": cache.written_bytes,
                     "elapsed_seconds": time.perf_counter() - start,
                     "saved_results": len(result.get("records", {}))})
    source = Path(__file__).resolve().parents[1]
    report = {"schema_version": "translation-cache-offline-performance-v1", "observed_at": utc(),
              "network_calls": 0, "synthetic_only": True, "rows": rows,
              "code_file_hashes": {name: digest((source / name).read_bytes()) for name in ("cache.py", "service.py", "pool.py")}}
    (out / "measurements.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = ["# Cache / Stop performance diagnosis", "", "Synthetic only; production cache format is unchanged.", "",
        "Each claim/settle invokes load(), and save() copies and serializes all four dictionaries,",
        "fsyncs a temporary full JSON file and atomically replaces the old file. Work grows with cache size.",
        "A stopped provider returns pending; TranslationService still traverses later records and structured",
        "items and calls claim_memory/settle_memory. The existing runner does not cancel the workload iterator.",
        "Thus HTTP stops first while pending persistence can continue for hours. A persistent lock filename",
        "is not proof that a lock is currently owned.", "", "## Measurements", "", "```json",
        json.dumps(rows, ensure_ascii=False, indent=2), "```", "", "## Proposed next slice (not implemented)", "",
        "1. Stop admission at the workload iterator before NEW claims; let already entered work settle.",
        "2. Keep unknown send outcomes quarantined; never use stop as authorization to resend.",
        "3. Append fsynced run events/responses for attempts, usage and diagnostics. Only translate state",
        "   and no-repeat claims need the shared cache. Do not keep provider bodies in all four indexes.",
        "4. Measure an append journal plus verified checkpoints or a reviewed transactional store in a",
        "   separate migration task. Do not replace the production cache format in this foundation.",
        "5. Future batch checkpoints must preserve per-response durability and cross-process atomic claims;",
        "   a faster whole-batch write without these guarantees would reopen the crash resend window.", "",
        "Timing measures this machine and these fake sizes only, not a promise of live 850 drain time."]
    (out / "cache_shutdown_diagnostics.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return report
