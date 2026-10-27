import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amazon_es_bestseller.collection import runtime_io as io


def denied(code=32):
    error = PermissionError(errno.EACCES, 'injected sharing violation')
    error.winerror = code
    return error


def test_read_retries_verified_sharing_error(monkeypatch, tmp_path):
    path = tmp_path / 'plan.json'
    path.write_text('{"asin":"B07B8D8RC5"}')
    original = Path.read_text
    calls = []
    def read(self, **kwargs):
        calls.append(self)
        if len(calls) < 3:
            raise denied()
        return original(self, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(io.time, 'sleep', lambda _: None)
    assert io.read_json(path)['asin'] == 'B07B8D8RC5'
    assert len(calls) == 3


@pytest.mark.parametrize('code', [5, None])
def test_real_read_permission_is_not_missing_or_retried(monkeypatch, tmp_path, code):
    calls = []
    def read(*a, **kw):
        calls.append(1)
        raise denied(code)
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(PermissionError):
        io.read_json(tmp_path / 'plan.json', {})
    assert len(calls) == 1


def test_replace_retries_without_truncating_original(monkeypatch, tmp_path):
    path = tmp_path / 'worker_status.json'
    path.write_text('{"old":true}')
    replace = io.os.replace
    calls = []
    def locked(source, target):
        calls.append(str(source))
        assert json.loads(path.read_text()) == {'old': True}
        if len(calls) < 3:
            raise denied()
        replace(source, target)
    monkeypatch.setattr(io.os, 'replace', locked)
    monkeypatch.setattr(io.time, 'sleep', lambda _: None)
    io.atomic_json(path, {'new': True})
    assert json.loads(path.read_text()) == {'new': True}
    assert len(calls) == 3


@pytest.mark.parametrize('code,attempts', [(32, 6), (33, 6), (5, 6), (None, 1)])
def test_persistent_write_failure_preserves_old_and_staging(monkeypatch, tmp_path, code, attempts):
    path = tmp_path / 'checkpoint.json'
    path.write_text('{"old":true}')
    calls = []
    def fail(*a):
        calls.append(1)
        raise denied(code)
    monkeypatch.setattr(io.os, 'replace', fail)
    monkeypatch.setattr(io.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        io.atomic_json(path, {'new': True})
    assert len(calls) == attempts
    assert json.loads(path.read_text()) == {'old': True}
    pending = list(tmp_path.glob('.*.tmp'))
    assert len(pending) == 1
    assert json.loads(pending[0].read_text()) == {'new': True}


def test_simultaneous_writers_have_unique_staging_files(monkeypatch, tmp_path):
    path = tmp_path / 'status.json'
    paths = []
    replace = io.os.replace
    def record(source, target):
        paths.append(str(source))
        replace(source, target)
    monkeypatch.setattr(io.os, 'replace', record)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: io.atomic_json(path, {'writer': i}), range(40)))
    assert len(set(paths)) == 40  # retries may repeat their own staging path
    assert json.loads(path.read_text())['writer'] in range(40)
    assert not list(tmp_path.glob('.*.tmp'))


def test_only_absence_uses_default_and_corruption_surfaces(tmp_path):
    path = tmp_path / 'state.json'
    assert io.read_json(path, {'missing': True}) == {'missing': True}
    path.write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        io.read_json(path, {})


def test_checkpoint_permission_cannot_become_cache_miss(monkeypatch, tmp_path):
    from amazon_es_bestseller.collection.checkpoints import read_checkpoint
    def fail(*a, **kw):
        raise denied(5)
    monkeypatch.setattr(Path, 'read_text', fail)
    with pytest.raises(PermissionError):
        read_checkpoint(tmp_path, 'B07B8D8RC5')


def test_formal_plan_write_failure_preserves_old_plan(monkeypatch, tmp_path):
    from amazon_es_bestseller.monitoring.detail_planner import write_detail_plan
    target = tmp_path / 'detail_plan.json'
    target.write_text('{"old":"immutable evidence"}')
    monkeypatch.setattr(io.os, 'replace', lambda *a: (_ for _ in ()).throw(denied(5)))
    monkeypatch.setattr(io.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        write_detail_plan({'snapshot_id': 'offline', 'records': []}, tmp_path)
    assert json.loads(target.read_text()) == {'old': 'immutable evidence'}


def test_cli_json_output_failure_never_truncates_existing_file(monkeypatch, tmp_path):
    from amazon_es_bestseller.cli import _save_json
    path = tmp_path / 'report.json'
    path.write_text('{"notes":"preserve"}')
    monkeypatch.setattr(io.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError(28, 'disk full')))
    with pytest.raises(OSError):
        _save_json({'notes': 'replacement'}, str(path))
    assert json.loads(path.read_text()) == {'notes': 'preserve'}


@pytest.mark.skipif(os.name != 'nt', reason='real Windows handle sharing contract')
@pytest.mark.parametrize('operation', ['read', 'replace'])
def test_real_windows_locked_file_recovers_after_handle_release(monkeypatch, tmp_path, operation):
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    path = tmp_path / 'locked.json'
    path.write_text('{"old":true}')
    handle = api.CreateFileW(str(path), 0x80000000, 0 if operation == 'read' else 1,
                             None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    released = []
    def release(_seconds):
        if not released:
            assert api.CloseHandle(handle)
            released.append(True)
    monkeypatch.setattr(io.time, 'sleep', release)
    try:
        if operation == 'read':
            assert io.read_json(path) == {'old': True}
        else:
            io.atomic_json(path, {'new': True})
            assert json.loads(path.read_text()) == {'new': True}
        assert released  # an actual OS denial, followed by successful retry
    finally:
        if not released:
            api.CloseHandle(handle)
