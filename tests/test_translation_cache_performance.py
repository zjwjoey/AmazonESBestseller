"""Synthetic-only cache I/O/durability regressions; no live inputs or API."""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import TranslationService


def field(value):
    return {"translation_status": "success", "translated_text": value, "qa_issues": [{"code": "fixture"}]}


def test_unchanged_save_does_not_replace_or_fsync(tmp_path, monkeypatch):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.put("one", field("first"))
    cache.save()
    original = cache.path.read_bytes()
    def forbidden(*args):
        pytest.fail("unchanged cache was rewritten")
    monkeypatch.setattr(os, "replace", forbidden)
    monkeypatch.setattr(os, "fsync", forbidden)
    cache.save()
    cache.save()
    assert cache.path.read_bytes() == original


@pytest.mark.parametrize("namespace,getter", [("entries", "get"), ("memory", "get_memory")])
def test_read_envelopes_do_not_expose_mutable_nested_cache_evidence(tmp_path, namespace, getter):
    cache = TranslationCache(tmp_path / "cache.json")
    getattr(cache, namespace)["one"] = field("first")
    result = getattr(cache, getter)("one")
    result["qa_issues"][0]["code"] = "changed"
    assert getattr(cache, namespace)["one"]["qa_issues"] == [{"code": "fixture"}]


def test_stale_unchanged_save_cannot_erase_another_clients_claim(tmp_path):
    path = tmp_path / "cache.json"
    old = TranslationCache(path)
    old.put("one", field("first"))
    old.save()
    fresh = TranslationCache(path)
    fresh.claim("second", {"translation_status": "pending"})
    evidence = path.read_bytes()
    old.save()
    assert path.read_bytes() == evidence
    assert old.get("second")["translation_status"] == "pending"


def test_independent_local_update_merges_with_new_external_result(tmp_path):
    path = tmp_path / "cache.json"
    old = TranslationCache(path)
    old.put("original", field("first"))
    old.save()
    fresh = TranslationCache(path)
    old.put("local", field("local"))
    fresh.settle("remote", field("remote"))
    old.save()
    assert set(TranslationCache(path).entries) == {"original", "local", "remote"}


def test_overlapping_update_conflict_preserves_external_evidence(tmp_path):
    path = tmp_path / "cache.json"
    old = TranslationCache(path)
    old.put("same", field("first"))
    old.save()
    fresh = TranslationCache(path)
    old.put("same", field("local"))
    fresh.settle("same", field("remote"))
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="CACHE_CONCURRENT_UPDATE_CONFLICT"):
        old.save()
    assert path.read_bytes() == original


def test_unsaved_direct_nested_edits_are_detected_by_save(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.memory["one"] = field("first")
    cache.save()
    cache.memory["one"]["qa_issues"][0]["code"] = "updated"
    cache.save()
    assert TranslationCache(cache.path).get_memory("one")["qa_issues"] == [{"code": "updated"}]


def test_parallel_independent_saves_preserve_both_clients(tmp_path):
    path = tmp_path / "cache.json"
    a, b = TranslationCache(path), TranslationCache(path)
    a.put("A", field("A"))
    b.put("B", field("B"))
    barrier = threading.Barrier(2)
    def save(cache):
        barrier.wait(5)
        cache.save()
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(save, (a, b)))
    assert set(TranslationCache(path).entries) == {"A", "B"}


def test_unknown_claim_survives_stale_save_and_remains_non_replayable(tmp_path):
    path = tmp_path / "cache.json"
    old = TranslationCache(path)
    old.put("one", field("first"))
    old.save()
    other = TranslationCache(path)
    other.claim_memory("unknown", {"translation_status": "pending", "last_error": "TRANSPORT_OUTCOME_UNKNOWN"})
    old.put("local", field("local"))
    old.save()
    restarted = TranslationCache(path)
    assert not restarted.claim_memory("unknown", {"translation_status": "pending"})[0]
    assert restarted.get_memory("unknown")["last_error"] == "TRANSPORT_OUTCOME_UNKNOWN"


def test_wait_for_pending_uses_metadata_poll_without_repeated_full_load(tmp_path, monkeypatch):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.claim_memory("one", {"translation_status": "pending"})
    loads = []
    original = cache.load
    def load():
        loads.append(True)
        original()
    monkeypatch.setattr(cache, "load", load)
    assert cache.wait_memory_settlement("one", timeout_seconds=0.1) is None
    assert len(loads) <= 1


def test_wait_detects_another_instances_atomic_settlement(tmp_path):
    path = tmp_path / "cache.json"
    cache = TranslationCache(path)
    cache.claim_memory("one", {"translation_status": "pending"})
    writer = TranslationCache(path)
    writer.settle_memory("one", {"translation_status": "success", "candidate_text": "raw"})
    assert cache.wait_memory_settlement("one")["candidate_text"] == "raw"


def test_claim_always_reads_actual_bytes_even_when_timestamps_and_size_match(tmp_path):
    path = tmp_path / "cache.json"
    cache = TranslationCache(path)
    cache.claim("aaaa", {"translation_status": "pending"})
    stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b'"aaaa"', b'"bbbb"'))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    # Stat is not permission to admit: a same-size, restored-mtime edit must
    # still be read at the real ownership boundary.
    assert not cache.claim("bbbb", {"translation_status": "pending"})[0]


def test_save_does_not_deepcopy_entire_indexes(tmp_path, monkeypatch):
    from amazon_es_bestseller.translation import cache as module
    cache = TranslationCache(tmp_path / "cache.json")
    cache.put("one", field("first"))
    def forbidden(*args):
        pytest.fail("whole cache was deep-copied during save")
    monkeypatch.setattr(module, "deepcopy", forbidden)
    cache.save()
    assert TranslationCache(cache.path).entries["one"]["translated_text"] == "first"


def test_successful_scalar_settles_memory_exactly_once_and_before_field(tmp_path, monkeypatch):
    class Fake(TranslationProvider):
        name = "fixture"
        model = "fixture"
        def translate(self, text, **kwargs):
            return ProviderResponse(text="测试商品", provider=self.name, model=self.model)
    cache = TranslationCache(tmp_path / "cache.json")
    calls = []
    original = cache.settle_memory
    def settle_memory(key, value):
        calls.append(key)
        original(key, value)
    monkeypatch.setattr(cache, "settle_memory", settle_memory)
    service = TranslationService(Fake(), cache)
    service.translate_records([{"asin": "B000000001", "title_es_raw": "Producto especial"}])
    assert len(calls) == 1
    assert next(iter(TranslationCache(cache.path).memory.values()))["candidate_text"] == "测试商品"


def test_failed_replace_retains_previous_bytes_and_can_retry(tmp_path, monkeypatch):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.put("one", field("first"))
    cache.save()
    original = cache.path.read_bytes()
    cache.put("two", field("second"))
    replace = os.replace
    def fail(*args):
        raise OSError("fixture replace failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="fixture replace failure"):
        cache.save()
    assert cache.path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp-*"))
    monkeypatch.setattr(os, "replace", replace)
    cache.save()
    assert set(TranslationCache(cache.path).entries) == {"one", "two"}


def test_independent_process_saves_preserve_both_stale_updates(tmp_path):
    path = tmp_path / "cache.json"
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from amazon_es_bestseller.translation.cache import TranslationCache
c = TranslationCache(sys.argv[2])
c.put(sys.argv[3], {'translation_status': 'success', 'candidate_text': sys.argv[3]})
print('READY', flush=True)
sys.stdin.readline()
c.save()
"""
    source = str(Path(__file__).resolve().parents[1] / "src")
    children = []
    try:
        for key in ("A", "B"):
            children.append(subprocess.Popen(
                [sys.executable, "-u", "-c", script, source, str(path), key],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        for child in children:
            assert child.stdout.readline().strip() == "READY"
        for child in children:
            child.stdin.write("SAVE\n")
            child.stdin.flush()
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, (stdout, stderr)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()  # Only synthetic children created in this fixture.
            child.communicate(timeout=10)
    assert set(TranslationCache(path).entries) == {"A", "B"}


def test_failed_fsync_leaves_durable_unknown_claim_and_no_replay(tmp_path, monkeypatch):
    cache = TranslationCache(tmp_path / "cache.json")
    assert cache.claim_memory("unknown", {"translation_status": "pending"})[0]
    original = cache.path.read_bytes()
    def fail(*args):
        raise OSError("fixture fsync failure")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="fixture fsync failure"):
        cache.settle_memory("unknown", field("result"))
    assert cache.path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp-*"))
    assert not TranslationCache(cache.path).claim_memory("unknown", {})[0]


def test_external_corruption_is_not_overwritten_by_stale_save(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.put("one", field("first"))
    cache.save()
    cache.path.write_bytes(b"{broken fixture")
    with pytest.raises(RuntimeError, match="CACHE_EXTERNAL_EVIDENCE_UNREADABLE"):
        cache.save()
    assert cache.path.read_bytes() == b"{broken fixture"


def test_saved_bytes_keep_version_five_and_existing_text_newlines(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.put("one", field("中文\nsecond line"))
    cache.save()
    payload = {"cache_version": 5, "entries": cache.entries, "memory": {},
               "results": {}, "memory_results": {}}
    expected = json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", os.linesep).encode("utf-8")
    assert cache.path.read_bytes() == expected


def test_diagnostics_count_physical_reads_and_noop_saves_separately(tmp_path):
    from amazon_es_bestseller.translation.reconciliation.diagnostics import MeasuredCache
    cache = MeasuredCache(tmp_path / "cache.json")
    cache.put("one", field("first"))
    cache.save()
    size = cache.path.stat().st_size
    assert cache.physical_writes == 1
    assert cache.written_bytes == size
    cache.save()
    assert cache.saves == 2
    assert cache.physical_writes == 1
    assert cache.written_bytes == size
    assert cache.disk_reads == 1
    assert cache.read_bytes == size
