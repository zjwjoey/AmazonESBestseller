"""Launcher checks are offline and do not alter shared pip environments."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_launcher_ignores_another_checkout_on_pythonpath(tmp_path):
    package = tmp_path / "amazon_es_bestseller"
    package.mkdir()
    (package / "__init__.py").write_text('raise RuntimeError("WRONG_CHECKOUT")', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, str(ROOT / "tools/run_local.py"), "--check-runtime"],
                            env=env, cwd=tmp_path, text=True, encoding="utf-8", capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert Path(report["cli_path"]).resolve() == ROOT / "src/amazon_es_bestseller/cli.py"


def test_launcher_rejects_a_preimported_foreign_package(tmp_path):
    script = '''import runpy, sys, types
module = types.ModuleType("amazon_es_bestseller")
module.__file__ = sys.argv[2]
sys.modules["amazon_es_bestseller"] = module
sys.argv = [sys.argv[1], "--check-runtime"]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    result = subprocess.run([sys.executable, "-c", script, str(ROOT / "tools/run_local.py"), str(tmp_path / "foreign.py")],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert "RUNTIME_SOURCE_PATH_MISMATCH" in result.stderr
