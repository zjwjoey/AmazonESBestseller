import gzip
from pathlib import Path

import pytest

from amazon_es_bestseller.audit_ledger import AuditLedger
from amazon_es_bestseller.evidence_store import CacheCapacityExceeded, read_evidence
from amazon_es_bestseller.saved_evidence import SavedHtmlIndex
from amazon_es_bestseller.task_workspace import TaskWorkspace


def task(tmp_path, size=1000):
    return TaskWorkspace.create(tmp_path, "offline", cache_limit_bytes=size)


def audit(ledger, stage, unit, digest, **kwargs):
    return ledger.record(stage=stage, unit_id=unit, digests=[digest], record={"id": unit},
                         status="PASS", fields={}, anomalies=[], **kwargs)


def test_shared_cache_thresholds_and_capacity(tmp_path):
    ledger = AuditLedger(task(tmp_path))
    store = ledger.evidence
    page, digest = store.save(b"a" * 800)
    assert store.capacity()["state"] == "WARN"
    detail, detail_digest = store.save(b"b" * 100)
    assert store.capacity()["state"] == "COMPACT"
    with pytest.raises(CacheCapacityExceeded):
        store.save(b"x" * 101)
    store.save(b"y" * 100)
    assert store.capacity() == {"active_bytes": 1000, "peak_bytes": 1000, "state": "PAUSE"}
    assert read_evidence(page, digest) == b"a" * 800
    assert read_evidence(detail, detail_digest) == b"b" * 100
    audit(ledger, "snapshot", "page", digest)
    with pytest.raises(CacheCapacityExceeded):
        store.archive(digest)
    assert page.exists()


def test_archive_requires_audit_deduplicates_and_recovers(tmp_path):
    workspace = task(tmp_path)
    ledger = AuditLedger(workspace)
    store = ledger.evidence
    body = b"<html>original bytes</html>" * 15
    active, digest = store.save(body)
    with pytest.raises(ValueError, match="REQUIRES_DURABLE_AUDIT"):
        store.archive(digest)
    assert audit(ledger, "snapshot", "page", digest) == audit(ledger, "snapshot", "page", digest)
    audit(ledger, "detail", "sku", digest)
    gz = store.archive(digest)
    assert not active.exists() and read_evidence(active, digest) == body
    assert store.save(body) == (active, digest) and not active.exists()
    # Simulate archive publication before index commit, then index commit
    # before original removal.
    active.write_bytes(body)
    with workspace.transaction() as conn:
        conn.execute("UPDATE evidence SET archive_path=NULL,archive_hash=NULL,archive_size=NULL")
    assert store.recover() == []
    active.write_bytes(body)
    assert store.recover() == []
    assert not active.exists() and gzip.decompress(gz.read_bytes()) == body
    assert store.archive_stats(verify=True)["files"] == 1
    assert store.archive_stats(verify=True)["errors"] == []


def test_corrupt_archive_retains_original_and_orphans(tmp_path):
    ledger = AuditLedger(task(tmp_path))
    store = ledger.evidence
    active, digest = store.save(b"evidence" * 30)
    audit(ledger, "detail", "sku", digest)
    destination = store.archive(digest)
    active.write_bytes(b"evidence" * 30)
    destination.write_bytes(gzip.compress(b"changed"))
    orphan = store.temporary / "recover-later"
    orphan.write_bytes(b"unknown")
    issues = store.recover()
    assert {x["reason"].split(":")[0] for x in issues} == {
        "ARCHIVE_ORIGINAL_MISMATCH", "UNRESOLVED_TEMPORARY"}
    assert active.exists() and orphan.exists()
    assert store.archive_stats(verify=True)["errors"] == [digest]
    unindexed = store.archive_root / "orphan.html.gz"
    unindexed.write_bytes(gzip.compress(b"undocumented evidence"))
    assert "UNINDEXED_ARCHIVE:" + unindexed.name in store.archive_stats(verify=True)["errors"]
    assert unindexed.exists()


def test_legacy_and_gzip_reads_with_memory_limit(tmp_path):
    raw = tmp_path / "B000000001.html"
    raw.write_bytes(b"legacy")
    zipped = tmp_path / "B000000002.html.gz"
    zipped.write_bytes(gzip.compress(b"archived"))
    index = SavedHtmlIndex(tmp_path, max_body_bytes=55)
    assert index.read(raw) == "legacy"
    assert index.read(zipped) == "archived"
    assert index.body_bytes <= 55
    assert set(index.detail_paths(prefer_filename=True)) == {"B000000001", "B000000002"}
    with pytest.raises(ValueError, match="EVIDENCE_SHA256_MISMATCH"):
        read_evidence(zipped, "wrong")


def test_changed_evidence_switches_current_without_erasing_history(tmp_path):
    workspace = task(tmp_path)
    ledger = AuditLedger(workspace)
    _, old = ledger.evidence.save(b"old")
    _, new = ledger.evidence.save(b"new")
    old_id = audit(ledger, "detail", "sku", old)
    new_id = audit(ledger, "detail", "sku", new, parser_version="v2.1")
    assert old_id != new_id
    assert audit(ledger, "detail", "sku", old) == old_id
    assert ledger.current("detail", current_versions=True) == []
    assert [a["audit_id"] for a in AuditLedger(TaskWorkspace(tmp_path)).current("detail")] == [new_id]
    with workspace.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM audit_attempts").fetchone()[0] == 2



def test_default_gib_thresholds_and_temporary_bytes(tmp_path, monkeypatch):
    from amazon_es_bestseller.task_workspace import GIB

    workspace = TaskWorkspace.create(tmp_path, "offline")
    store = AuditLedger(workspace).evidence
    occupied = [8 * (2 * GIB) // 10]
    real_bytes = store._bytes
    monkeypatch.setattr(store, "_bytes", lambda: occupied[0] + real_bytes())
    assert store.capacity()["state"] == "WARN"
    occupied[0] = 9 * (2 * GIB) // 10
    assert store.capacity()["state"] == "COMPACT"
    occupied[0] = 2 * GIB - 2
    # Raw evidence and transient work consume the same byte allowance.
    (store.temporary / "working-decompression").write_bytes(b"xy")
    assert store.capacity() == {"active_bytes": 2 * GIB, "peak_bytes": 2 * GIB, "state": "PAUSE"}
    with pytest.raises(CacheCapacityExceeded):
        store.save(b"a")
    assert not list(store.active.iterdir())


def test_archive_interrupted_before_publish_retains_active_bytes(tmp_path, monkeypatch):
    from amazon_es_bestseller import evidence_store

    workspace = task(tmp_path)
    ledger = AuditLedger(workspace)
    snapshot_store = ledger.evidence
    detail_store = AuditLedger(TaskWorkspace(tmp_path)).evidence
    active, digest = snapshot_store.save(b"x" * 740)
    audit(ledger, "snapshot", "page", digest)
    with pytest.raises(CacheCapacityExceeded):
        detail_store.save(b"y" * 261)

    def interrupt(src, compressed, **_kwargs):
        compressed.write(src.read(128))
        raise KeyboardInterrupt

    with monkeypatch.context() as scoped:
        scoped.setattr(evidence_store.shutil, "copyfileobj", interrupt)
        with pytest.raises(KeyboardInterrupt):
            detail_store.archive(digest)
        assert active.read_bytes() == b"x" * 740
    assert detail_store.recover() == []
    assert read_evidence(active, digest) == b"x" * 740
    assert detail_store.capacity()["active_bytes"] == 0


def test_rule_version_changes_invalidate_current_lookup(tmp_path):
    ledger = AuditLedger(task(tmp_path))
    _, evidence = ledger.evidence.save(b"example")
    old = audit(ledger, "detail", "sku", evidence, qa_version="old-qa")
    assert ledger.lookup("detail", "sku", {"id": "sku"}) is None
    assert not ledger.current("detail", current_versions=True)

    latest = audit(ledger, "detail", "sku", evidence)
    assert latest != old
    assert ledger.lookup("detail", "sku", {"id": "sku"}) == "PASS"
    assert ledger.current("detail", current_versions=True)[0]["audit_id"] == latest



def test_snapshot_cannot_reserve_using_a_separate_cache_allowance(tmp_path):
    from contextlib import closing
    from amazon_es_bestseller.highspeed.store import RunStore

    workspace = task(tmp_path)
    detail_cache = AuditLedger(workspace).evidence
    detail_cache.save(b"a" * 1000)
    with closing(RunStore(tmp_path / "store")) as snapshot:
        snapshot.task_evidence = AuditLedger(TaskWorkspace(tmp_path)).evidence
        with pytest.raises(CacheCapacityExceeded, match="ACTIVE_CACHE_HARD_LIMIT"):
            snapshot.reserve_request("not-created", {})
