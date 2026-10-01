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


def test_translate_cli_accepts_internal_research_csv(tmp_path, capsys):
    products = tmp_path / "research.csv"
    out = tmp_path / "plan.json"
    products.write_text(
        "序号,ASIN,商品名称（西语）,品牌,一级类目,核心规格（西语）\n"
        "1,B00000001,Bolsa 500 ml,Acme,Hogar y cocina,Capacidad: 500 ml\n",
        encoding="utf-8-sig",
    )
    assert main(["--offline", "translate", "--products", str(products),
                 "--out", str(out), "--dry-run"]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["total_records"] == 1
    assert plan["total_fields"] == 2
    assert plan["estimated_api_requests"] == 2
    assert "dry-run" in capsys.readouterr().out


def test_parallel_dry_run_reports_pool_without_api_calls(tmp_path, capsys):
    products = tmp_path / "products.json"
    config = tmp_path / "dual.json"
    out = tmp_path / "plan.json"
    products.write_text(json.dumps([{"asin": "B00000001", "title_es_raw": "Taladro 18V"}], ensure_ascii=False), encoding="utf-8")
    config.write_text(json.dumps({
        "provider": "qwen-mt", "providers": [
            {"name": "qwen-a", "api_key_env": "MISSING_A", "endpoint_env": "MISSING_EA", "rate": 0.5},
            {"name": "qwen-b", "api_key_env": "MISSING_B", "endpoint_env": "MISSING_EB", "rate": 0.5},
        ], "max_workers": 2,
    }), encoding="utf-8")
    assert main(["--offline", "translate", "--products", str(products),
                 "--config", str(config), "--out", str(out), "--dry-run",
                 "--parallel-providers"]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["pool"]["max_workers"] == 2
    assert set(plan["estimated_provider_requests"]) == {"qwen-a", "qwen-b"}
    assert "Provider qwen-a" in capsys.readouterr().out
