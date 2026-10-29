"""Legacy query commands use isolated stores and never publish source changes."""
import hashlib
from pathlib import Path
import pytest

from amazon_es_bestseller.highspeed.store import RunStore
from test_highspeed_cli import invoke, offline_fixture


READ_OPERATIONS = ('status', 'qa', 'report', 'gate-export', 'snapshot-review-status')


def fingerprint(root):
    return {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('operation', READ_OPERATIONS)
def test_query_missing_store_never_creates_source(tmp_path, operation):
    root = tmp_path/'missing'
    args = ['--offline', 'highspeed', operation, '--root', str(root), '--run-id', 'TEST_ONLY']
    if operation in {'report', 'gate-export', 'snapshot-review-status'}:
        args += ['--out', str(tmp_path/'external')]
    with pytest.raises(ValueError):
        invoke(*args)
    assert not root.exists()


def test_queries_read_active_wal_and_preserve_source_bytes_and_metadata(tmp_path):
    plan_path, plan = offline_fixture(tmp_path)
    root = tmp_path/'source'
    invoke('--offline', 'highspeed', 'run', '--root', str(root), '--plan', str(plan_path))
    writer = RunStore(root)
    try:
        writer.db.execute('CREATE TABLE test_only_wal(value INTEGER)')
        writer.db.execute('INSERT INTO test_only_wal VALUES(42)')
        writer.update_run(plan['run_id'], test_only_wal_marker='visible-from-wal')
        before = fingerprint(root)
        for operation in ('status', 'qa', 'report', 'gate-export'):
            args = ['--offline', 'highspeed', operation, '--root', str(root), '--run-id', plan['run_id']]
            if operation in {'report', 'gate-export'}:
                args += ['--out', str(tmp_path/('external-'+operation))]
            result = invoke(*args)
            if operation == 'status':
                assert result['run']['test_only_wal_marker'] == 'visible-from-wal'
            assert fingerprint(root) == before
    finally:
        writer.close()


@pytest.mark.parametrize('operation', ('report', 'gate-export', 'snapshot-review-status'))
def test_query_output_cannot_write_inside_source(tmp_path, operation):
    plan_path, plan = offline_fixture(tmp_path)
    root = tmp_path/'source'
    invoke('--offline', 'highspeed', 'run', '--root', str(root), '--plan', str(plan_path))
    before = fingerprint(root)
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_OUTSIDE'):
        invoke('--offline', 'highspeed', operation, '--root', str(root), '--run-id', plan['run_id'],
               '--out', str(root/'forbidden'))
    assert fingerprint(root) == before


def test_metadata_correction_requires_explicit_apply_before_opening_store(tmp_path):
    root = tmp_path/'missing'
    with pytest.raises(ValueError, match='EXPLICIT_METADATA_APPLY_REQUIRED'):
        invoke('--offline', 'highspeed', 'snapshot-review-correct', '--root', str(root),
               '--run-id', 'TEST_ONLY', '--out', str(tmp_path/'outside.json'))
    assert not root.exists()
