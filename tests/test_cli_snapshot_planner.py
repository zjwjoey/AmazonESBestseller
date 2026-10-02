# -*- coding: utf-8 -*-
import json

from amazon_es_bestseller import cli


def test_v1_commands_are_registered():
    parser = cli.build_parser()
    assert parser.parse_args(["ranking-snapshot", "--rankings-file", "r.json"]).command == "ranking-snapshot"
    assert parser.parse_args(["detail-plan", "--snapshot", "s.json", "--out-dir", "out"]).command == "detail-plan"
    assert parser.parse_args(["detail-run", "--plan", "p.json", "--out-dir", "out"]).command == "detail-run"


def test_offline_cli_chain_writes_snapshot_and_plan(tmp_path):
    rankings = tmp_path / "rankings.json"
    rankings.write_text(json.dumps([{
        "asin": "B000000001", "bestseller_rank": 1,
        "ranking_source_url": "https://www.amazon.es/zgbs/1",
        "ranking_product_url_raw": "/Producto/dp/B000000001/ref=x",
        "ranking_product_url_normalized": "https://www.amazon.es/dp/B000000001",
        "ranking_link_asin": "B000000001", "ranking_link_identity_status": "MATCH",
    }]), encoding="utf-8")
    snapshot_root = tmp_path / "snapshots"
    assert cli.main(["--offline", "ranking-snapshot", "--rankings-file", str(rankings),
                     "--out-dir", str(snapshot_root)]) == 0
    snapshot = next(snapshot_root.glob("**/rankings.json"))
    plan_root = tmp_path / "plan"
    assert cli.main(["--offline", "detail-plan", "--snapshot", str(snapshot),
                     "--out-dir", str(plan_root)]) == 0
    plan = json.loads((plan_root / "detail_plan.json").read_text(encoding="utf-8"))
    assert plan[0]["detail_action"] == "FETCH_NEW"


def test_detail_plan_cli_rejects_incomplete_snapshot(tmp_path):
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "rankings.json").write_text("[]", encoding="utf-8")
    (snapshot_dir / "manifest.json").write_text(json.dumps({
        "snapshot_id": "snapshot_bad", "snapshot_status": "INCOMPLETE",
    }), encoding="utf-8")
    try:
        cli.main(["--offline", "detail-plan", "--snapshot", str(snapshot_dir),
                  "--out-dir", str(tmp_path / "plan")])
    except ValueError as exc:
        assert "AUTHORITATIVE" in str(exc)
    else:
        raise AssertionError("INCOMPLETE snapshot must not reach detail planner")
