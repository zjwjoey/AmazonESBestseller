"""Run this checkout's CLI without relying on a shared editable install.

Use ``python tools/run_local.py --check-runtime`` for a read-only preflight.
All remaining arguments are forwarded to the existing CLI and its gates.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "src"
    sys.path.insert(0, str(source))
    # Do not silently unload a package that was imported from another checkout.
    for name, module in list(sys.modules.items()):
        if name == "amazon_es_bestseller" or name.startswith("amazon_es_bestseller."):
            path = getattr(module, "__file__", None)
            if path and not Path(path).resolve().is_relative_to(source):
                raise RuntimeError("RUNTIME_SOURCE_PATH_MISMATCH: " + str(path))
    cli = importlib.import_module("amazon_es_bestseller.cli")
    path = Path(cli.__file__).resolve()
    if not path.is_relative_to(source):
        raise RuntimeError("RUNTIME_SOURCE_PATH_MISMATCH: " + str(path))
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--check-runtime"]:
        print(json.dumps({"status": "READY", "code_root": str(root), "cli_path": str(path)}, ensure_ascii=False))
        return 0
    return cli.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
