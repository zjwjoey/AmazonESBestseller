"""Bounded Windows sharing retries; never truncate authoritative JSON in place.

Reads retry only sharing/lock violations (32/33). Windows replacement can
also report error 5 for concurrent rename/delete sharing (locally reproduced).
That operation gets bounded retries; persistent denial always surfaces.
Failed replacements retain the uniquely named staging file for recovery.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time

_DELAYS = (0.05, 0.15, 0.35, 0.75, 1.5)
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _retry(operation, codes=(32, 33)):
    for attempt in range(len(_DELAYS) + 1):
        try:
            return operation()
        except OSError as error:
            if getattr(error, 'winerror', None) not in codes or attempt == len(_DELAYS):
                raise
            time.sleep(_DELAYS[attempt])


def read_text(path, *, encoding='utf-8'):
    """Read saved evidence with the same verified sharing-lock policy as JSON."""
    path = Path(path)
    def read():
        try:
            return path.read_text(encoding=encoding)
        except PermissionError as error:
            # CPython's CRT open() can discard WinError 32 and expose only
            # errno 13. Confirm the sharing error through a read-only Win32
            # open; never infer a transient lock from errno 13 alone.
            if os.name == 'nt' and getattr(error, 'winerror', None) is None:
                code = _windows_read_error(path)
                if code in {32, 33}:
                    error.winerror = code
            raise
    return _retry(read)


def read_json(path, default=None):
    """Only an absent file is a cache miss; permission errors must stop callers."""
    try:
        text = read_text(path)
    except FileNotFoundError:
        return default
    return json.loads(text)


def _windows_read_error(path):
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return ctypes.get_last_error()
    api.CloseHandle(handle)
    return 0


def atomic_json(path, value, *, sort_keys=False):
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=sort_keys)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp includes process-safe random uniqueness, even across independent
    # writers with the same thread ID. Close the handle before Windows replace.
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(os.path.normcase(str(path.resolve())), threading.RLock())
    with lock:
        _retry(lambda: os.replace(name, path), codes=(5, 32, 33))

