"""The shared Python install may point at a different editable worktree."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from amazon_es_bestseller.quality.source_fields import SOURCE_FIELD_AUDIT_RULES_VERSION

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "tools/worktree_cli.py"


def test_import_only_entry_binds_this_checkout_from_another_cwd(tmp_path):
    env = {**os.environ, "PYTHONPATH": ""}
    result = subprocess.run([sys.executable, str(ENTRY), "--check-worktree"], cwd=tmp_path,
                            env=env, check=True, capture_output=True, encoding="utf-8")
    facts = json.loads(result.stdout)
    assert Path(facts["cli_file"]) == ROOT / "src/amazon_es_bestseller/cli.py"
    assert facts["source_audit_rules_version"] == SOURCE_FIELD_AUDIT_RULES_VERSION
    assert facts["qwen_calls"] == facts["amazon_requests"] == 0
    assert list(tmp_path.iterdir()) == []


def test_wrong_import_binding_fails_before_entering_cli(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("entry", ENTRY)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(entry.importlib, "import_module", lambda _: SimpleNamespace(__file__="elsewhere/cli.py"))
    with pytest.raises(RuntimeError, match="CLI_WORKTREE_BINDING_MISMATCH"):
        entry.load_worktree_cli(tmp_path)
