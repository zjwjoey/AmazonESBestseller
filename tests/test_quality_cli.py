from __future__ import annotations

import json
from pathlib import Path

from amazon_es_bestseller.cli import build_parser, main


def test_quality_cli_writes_manifest_and_issue_exports(tmp_path: Path):
    rankings = tmp_path / "rankings.json"
    rankings.write_text(json.dumps([{
        "asin": "B078C6QR1C",
        "ranking_asin": "B078C6QR1C",
        "ranking_product_url_raw": "",
    }]), encoding="utf-8")
    output = tmp_path / "quality"
    assert main([
        "quality-audit", "--rankings", str(rankings),
        "--check", "ranking_identity", "--out-dir", str(output),
    ]) == 0
    runs = list(output.iterdir())
    assert len(runs) == 1
    assert (runs[0] / "quality_manifest.json").exists()
    assert (runs[0] / "quality_issues.csv").exists()
    manifest = json.loads((runs[0] / "quality_manifest.json").read_text(encoding="utf-8"))
    assert manifest["profile"] == "stable-research"
    assert manifest["network_requests"] == 0


def test_stable_research_offline_uses_same_gate(tmp_path: Path):
    rankings = tmp_path / "rankings.json"
    rankings.write_text(json.dumps([{
        "asin": "B078C6QR1C",
        "ranking_asin": "B078C6QR1C",
        "ranking_product_url_raw": "",
    }]), encoding="utf-8")
    output = tmp_path / "stable"
    args = build_parser().parse_args([
        "stable-research", "--offline", "--rankings", str(rankings),
        "--check", "ranking_identity", "--out-dir", str(output),
    ])
    assert args.command == "stable-research"
    assert args.offline is True
    args.func(args)
    assert list((output / "quality").iterdir())
