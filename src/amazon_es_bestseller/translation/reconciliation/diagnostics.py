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
        self.disk_reads = self.physical_writes = 0
        super().__init__(path)

    def load(self):
        self.loads += 1
        super().load()

    def _read_disk(self):
        raw, signature = super()._read_disk()
        if raw is not None:
            self.disk_reads += 1
            self.read_bytes += len(raw)
        return raw, signature

    def save(self):
        # Synthetic single-writer measurement; signatures distinguish no-ops
        # from atomic replacements. Calls are not equivalent to disk writes.
        before = self._signature(self.path.stat()) if self.path.exists() else None
        super().save()
        self.saves += 1
        after = self.path.stat()
        if self._signature(after) != before:
            self.physical_writes += 1
            self.written_bytes += after.st_size


def benchmark(output: str | Path, *, sizes=(100, 1000, 5000), operations: int = 3,
              stopped_records: int = 20) -> dict:
    """No real source, live cache, ProviderPool or Qwen configuration is touched."""
    from .report import _version

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
            cache.disk_reads = cache.physical_writes = 0
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
                "disk_reads": cache.disk_reads, "physical_writes": cache.physical_writes,
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
                     "disk_reads": cache.disk_reads, "physical_writes": cache.physical_writes,
                     "read_bytes": cache.read_bytes,
                     "elapsed_seconds": time.perf_counter() - start,
                     "saved_results": len(result.get("records", {}))})
        cache = MeasuredCache(Path(directory) / "admission-stopped.json")
        provider = StoppedFakeProvider()
        service = TranslationService(provider, cache, max_fields=["title_es_raw"], stop_requested=lambda: True)
        start = time.perf_counter()
        result = service.translate_records([{"asin": f"B{index:09d}", "title_es_raw": f"Objeto pendiente {index}"}
                                           for index in range(stopped_records)])
        rows.append({"scenario": "explicit_stop_probe_admission", "records": stopped_records,
                     "provider_entries": provider.entries, "http_attempts": 0,
                     "loads": cache.loads, "saves": cache.saves, "physical_writes": cache.physical_writes,
                     "disk_reads": cache.disk_reads, "read_bytes": cache.read_bytes,
                     "written_bytes": cache.written_bytes, "elapsed_seconds": time.perf_counter() - start,
                     "saved_results": len(result.get("records", {})), "admission": result.get("admission")})
    source = Path(__file__).resolve().parents[1]
    report = {"schema_version": "translation-cache-offline-performance-v2", "observed_at": utc(),
              "network_calls": 0, "synthetic_only": True, "rows": rows,
              "code": _version(), "observation_state": "SYNTHETIC_OFFLINE_DIAGNOSTIC",
              "input_evidence": "Generated synthetic data only; no production inputs",
              "measurement_contract": "saves=completed save calls; physical_writes=atomic replacements; disk_reads=actual JSON reads; lock-file I/O excluded",
              "code_file_hashes": {name: digest((source / name).read_bytes()) for name in (
                  "cache.py", "service.py", "pool.py", "reconciliation/diagnostics.py")}}
    (out / "measurements.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = ["# Cache / Stop performance diagnosis", "", "Synthetic only; production cache format is unchanged.", "",
        f"Code HEAD: `{report['code']['head']}`. Observed at: {report['observed_at']}.",
        "Inputs: generated synthetic data only. Code hashes, input sizes and measurements: measurements.json.", "",
        "Each claim/settle reads actual bytes under the OS lock. save() serializes references under",
        "the instance lock, without copying four full indexes. A changed payload still fsyncs a full",
        "temporary JSON and atomically replaces the cache; unchanged bytes do not rewrite. Work remains O(cache size).",
        "saves counts calls; physical_writes counts replacements. JSON reads include standalone save checks; lock-file I/O is excluded.",
        "The opaque fake returns pending but exposes no admission probe: this is a negative control,",
        "not proof of production stop behavior or live drain duration. The explicit-stop-probe control",
        "must show zero admitted provider entries and zero writes. Neither scenario sends HTTP.",
        "A persistent lock filename is not proof of current ownership. Unknown claims never authorize replay.",
        "", "## Measurements", "", "```json", json.dumps(rows, ensure_ascii=False, indent=2),
        "```", "", "## Remaining separate migration work (not implemented)", "",
        "A journal/checkpoint or transactional store needs independent review, migration fixtures and crash evidence.",
        "This slice changes neither VERSION 5 nor its four namespaces; there is no batch durability relaxation.",
        "Do not run old uncoordinated writers against a cache shared with the new lock protocol.", "",
        "Timing measures this machine and these fake sizes only, not a promise of live 850 drain time."]
    (out / "cache_shutdown_diagnostics.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return report
