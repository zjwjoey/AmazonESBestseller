import csv
import importlib.util
from pathlib import Path


def _tool():
    path = Path(__file__).parents[1] / "tools" / "local_translation_benchmark.py"
    spec = importlib.util.spec_from_file_location("local_translation_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_csv(path, headers, row):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader(); writer.writerow(row)


def test_local_benchmark_matches_asin_and_never_promotes_local_zh(tmp_path):
    es, zh, out = tmp_path / "es.csv", tmp_path / "zh.csv", tmp_path / "out"
    _write_csv(es, ["ASIN", "title_es_raw"], {"ASIN": "B000000001", "title_es_raw": "Botella 500 ml"})
    _write_csv(zh, ["ASIN", "title_zh"], {"ASIN": "B000000001", "title_zh": "水瓶 500 ml"})
    summary = _tool().benchmark(es, zh, out)
    assert summary["unique_skus"]["matched"] == 1
    assert summary["authority"] == "LOCAL_ZH_NOT_GOLD"
    assert (out / "summary.json").exists() and (out / "dictionary_candidates.json").exists()
    assert (out / "dictionary_statistics.json").read_text(encoding="utf-8").find('"promoted": 0') >= 0
