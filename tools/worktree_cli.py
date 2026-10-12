"""Invoke this checkout's CLI without rebinding a shared editable installation.

``--check-worktree`` is import-only: it creates no run and makes no requests.
All other arguments go to the existing CLI and retain its phase/source gates.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys


def load_worktree_cli(root: Path):
    root = root.resolve()
    src = root / "src"
    sys.path.insert(0, str(src))
    cli = importlib.import_module("amazon_es_bestseller.cli")
    expected = (src / "amazon_es_bestseller/cli.py").resolve()
    if Path(cli.__file__).resolve() != expected:
        raise RuntimeError("CLI_WORKTREE_BINDING_MISMATCH")
    return cli


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    cli = load_worktree_cli(root)
    os.chdir(root)  # This child process only; relative plans cannot select another checkout.
    if arguments == ["--check-worktree"]:
        rules = importlib.import_module("amazon_es_bestseller.quality.source_fields")
        print(json.dumps({"worktree": str(root), "cli_file": str(Path(cli.__file__).resolve()),
            "source_audit_rules_version": rules.SOURCE_FIELD_AUDIT_RULES_VERSION,
            "qwen_calls": 0, "amazon_requests": 0}, ensure_ascii=False))
        return 0
    return cli.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
