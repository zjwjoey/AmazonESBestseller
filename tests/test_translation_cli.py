import json

from amazon_es_bestseller.cli import main


def test_translate_cli_dry_run_is_offline_and_reports_plan(tmp_path, capsys):
    products = tmp_path / "products.json"
    out = tmp_path / "plan.json"
    products.write_text(json.dumps([{"asin": "B00000001", "title_es_raw": "Taladro 18V USB-C"}], ensure_ascii=False), encoding="utf-8")
    assert main(["--offline", "translate", "--products", str(products),
                 "--out", str(out), "--dry-run"]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["estimated_api_requests"] == 1
    assert plan["cache_hits"] == 0
    assert "dry-run" in capsys.readouterr().out
