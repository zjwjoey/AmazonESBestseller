"""Known runtime-hardening blocker; never send a signal during reproduction."""
from __future__ import annotations

import os
import sys

import pytest

from amazon_es_bestseller.translation import budget


@pytest.mark.skipif(sys.platform != "win32", reason="Windows signal-zero liveness safety")
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="P0: budget._FileLock._alive sends Windows CTRL_C_EVENT; deferred runtime hardening")
def test_windows_budget_owner_liveness_does_not_signal_process(monkeypatch):
    def forbid_signal(*_args):
        raise AssertionError("LIVENESS_PROBE_MUST_NOT_SIGNAL_PROCESS")

    monkeypatch.setattr(budget.os, "kill", forbid_signal)
    assert budget._FileLock._alive(os.getpid()) is True
