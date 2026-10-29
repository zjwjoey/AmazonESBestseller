"""Portable offline defaults; local checkpoint/browser integration is optional."""
import os
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def pytest_collection_modifyitems(items):
    browser_modules={'test_parallel_browser_pipeline','test_parallel_real_gate_offline','test_parallel_transport'}
    local_modules={'test_parallel_checkpoint','test_parallel_snapshot_hooks'}
    executable_exists=False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            executable_exists=Path(p.chromium.executable_path).is_file()
    except (ImportError,OSError):
        pass
    for item in items:
        if Path(str(item.path)).parent!=Path(__file__).parent:continue
        name=Path(str(item.path)).stem
        if name in local_modules and os.environ.get('SNAPSHOT_LOCAL_CHECKPOINT_TESTS')!='1':
            item.add_marker(pytest.mark.skip(reason='Requires explicit frozen local checkpoint integration opt-in'))
        elif name in browser_modules and not executable_exists:
            item.add_marker(pytest.mark.skip(reason='Local Chromium executable unavailable; no browser download in tests'))
