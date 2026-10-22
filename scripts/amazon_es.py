"""Run this checkout explicitly, without changing installed production packages.

Usage: python scripts/amazon_es.py --offline detail-run ...
       python scripts/amazon_es.py --runtime-info
"""
from pathlib import Path
import json
import runpy
import sys

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))

if sys.argv[1:] == ["--runtime-info"]:
    import amazon_es_bestseller
    print(json.dumps({"project_root": str(project_root),
                      "module_path": amazon_es_bestseller.__file__}, ensure_ascii=True))
else:
    runpy.run_module("amazon_es_bestseller.cli", run_name="__main__")
