"""Inspect the opt-in marker without ever calling a browser or collector."""
import importlib.util
from pathlib import Path
import pytest


@pytest.mark.parametrize("value, skipped", [(None, True), ("", True), ("0", True),
    ("false", True), ("true", True), ("01", True), ("1", False)])
def test_live_tests_require_exact_explicit_one(monkeypatch, value, skipped):
    if value is None:
        monkeypatch.delenv("RUN_LIVE", raising=False)
    else:
        monkeypatch.setenv("RUN_LIVE", value)
    spec = importlib.util.spec_from_file_location("inspect_live_marker_only", Path(__file__).with_name("test_collection_live.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.pytestmark.name == "skipif"
    assert module.pytestmark.args[0] is skipped
