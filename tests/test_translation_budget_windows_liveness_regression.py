"""Read-only lock-owner probes; no signals or production locks in these fixtures."""
from __future__ import annotations

import os
import ctypes
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amazon_es_bestseller.translation import budget


@pytest.mark.skipif(sys.platform != "win32", reason="Windows signal-zero liveness safety")
def test_windows_budget_owner_liveness_does_not_signal_process(monkeypatch):
    def forbid_signal(*_args):
        raise AssertionError("LIVENESS_PROBE_MUST_NOT_SIGNAL_PROCESS")

    monkeypatch.setattr(budget.os, "kill", forbid_signal)
    assert budget._FileLock._alive(os.getpid()) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows exited-process handle")
def test_windows_exited_owner_is_not_alive_even_with_retained_process_handle(monkeypatch):
    monkeypatch.setattr(budget.os, "kill", Mock(side_effect=AssertionError("must not signal")))
    with subprocess.Popen([sys.executable, "-c", "pass"], creationflags=subprocess.CREATE_NO_WINDOW) as child:
        assert child.wait(timeout=10) == 0
        assert budget._FileLock._alive(child.pid) is False


def kernel_fixture(monkeypatch, *, handle=0x100000001, wait_result=258, error=0, close_result=1):
    kernel = SimpleNamespace(OpenProcess=Mock(return_value=handle),
                             WaitForSingleObject=Mock(return_value=wait_result),
                             CloseHandle=Mock(return_value=close_result))
    loader = Mock(return_value=kernel)
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", Mock(return_value=error), raising=False)
    return kernel, loader


@pytest.mark.parametrize("wait_result, expected", [(258, True), (0, False), (0xFFFFFFFF, True), (128, True)])
def test_windows_probe_waits_zero_and_closes_pointer_sized_handle(monkeypatch, wait_result, expected):
    from ctypes import wintypes

    kernel, loader = kernel_fixture(monkeypatch, wait_result=wait_result)
    assert budget._FileLock._windows_alive(12345) is expected
    loader.assert_called_once_with("kernel32", use_last_error=True)
    kernel.OpenProcess.assert_called_once_with(0x00100000, False, 12345)
    assert kernel.OpenProcess.restype is wintypes.HANDLE
    kernel.WaitForSingleObject.assert_called_once_with(0x100000001, 0)
    kernel.CloseHandle.assert_called_once_with(0x100000001)


@pytest.mark.parametrize("error, expected", [(87, False), (5, True), (0, True), (299, True)])
def test_windows_open_failure_reclaims_only_confirmed_missing_pid(monkeypatch, error, expected):
    kernel, _ = kernel_fixture(monkeypatch, handle=0, error=error)
    assert budget._FileLock._windows_alive(12345) is expected
    kernel.WaitForSingleObject.assert_not_called()
    kernel.CloseHandle.assert_not_called()


def test_windows_wait_error_keeps_lock_and_closes_handle(monkeypatch):
    kernel, _ = kernel_fixture(monkeypatch)
    kernel.WaitForSingleObject.side_effect = OSError("read-only wait unavailable")
    assert budget._FileLock._windows_alive(12345) is True
    kernel.CloseHandle.assert_called_once_with(0x100000001)


def test_windows_close_error_does_not_certify_owner_death(monkeypatch):
    kernel_fixture(monkeypatch, wait_result=0, close_result=0)
    assert budget._FileLock._windows_alive(12345) is True


def test_windows_api_unavailable_is_not_permission_to_reclaim(monkeypatch):
    _, loader = kernel_fixture(monkeypatch)
    loader.side_effect = OSError("kernel API unavailable")
    assert budget._FileLock._windows_alive(12345) is True


@pytest.mark.parametrize("pid", [0, -1, 2**32, True, None, "invalid"])
def test_invalid_pid_never_probes_or_certifies_death(monkeypatch, pid):
    signal = Mock(side_effect=AssertionError("invalid PID must not be signalled"))
    monkeypatch.setattr(budget, "os", SimpleNamespace(name="nt", kill=signal))
    win_probe = Mock(side_effect=AssertionError("invalid PID must not be probed"))
    monkeypatch.setattr(budget._FileLock, "_windows_alive", win_probe, raising=False)
    assert budget._FileLock._alive(pid) is True
    signal.assert_not_called()
    win_probe.assert_not_called()


def test_windows_dispatch_never_falls_back_to_posix_signals(monkeypatch):
    signal = Mock(side_effect=AssertionError("Windows must not signal"))
    monkeypatch.setattr(budget, "os", SimpleNamespace(name="nt", kill=signal))
    probe = Mock(return_value=True)
    monkeypatch.setattr(budget._FileLock, "_windows_alive", probe, raising=False)
    assert budget._FileLock._alive(12345) is True
    probe.assert_called_once_with(12345)
    signal.assert_not_called()


@pytest.mark.parametrize("error, expected", [(None, True), (ProcessLookupError(), False),
    (PermissionError(), True), (OSError("unproven"), True), (OverflowError(), True)])
def test_posix_only_confirmed_lookup_failure_is_dead(monkeypatch, error, expected):
    signal = Mock(side_effect=error)
    monkeypatch.setattr(budget, "os", SimpleNamespace(name="posix", kill=signal))
    assert budget._FileLock._alive(12345) is expected
    signal.assert_called_once_with(12345, 0)


@pytest.mark.parametrize("pid", [0, 12345])
def test_unproven_owner_preserves_existing_lock_bytes(monkeypatch, tmp_path, pid):
    path = tmp_path / "ledger.json.lock"
    evidence = json.dumps({"pid": pid, "created_at": "fixture"}).encode()
    path.write_bytes(evidence)
    if pid:
        monkeypatch.setattr(budget._FileLock, "_alive", staticmethod(lambda _pid: True))
    with pytest.raises(budget.BudgetBlocked, match="BUDGET_LEDGER_LOCK_TIMEOUT"):
        with budget._FileLock(path, timeout=0):
            pytest.fail("uncertain owner lock must not be acquired")
    assert path.read_bytes() == evidence


def test_confirmed_dead_owner_reclaims_only_temporary_fixture_lock(monkeypatch, tmp_path):
    path = tmp_path / "ledger.json.lock"
    path.write_text(json.dumps({"pid": 12345}), encoding="utf-8")
    monkeypatch.setattr(budget._FileLock, "_alive", staticmethod(lambda _pid: False))
    with budget._FileLock(path, timeout=0):
        assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert not path.exists()
