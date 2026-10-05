from __future__ import annotations

import argparse

from amazon_es_bestseller import cli
from amazon_es_bestseller.commands import collection, detail, export, quality, ranking, translation


def test_cli_reexports_extracted_command_handlers_for_existing_importers():
    assert cli.cmd_select_quota is collection.cmd_select_quota
    assert cli.cmd_ranking_identity_extract is ranking.cmd_ranking_identity_extract
    assert cli.cmd_detail_plan is detail.cmd_detail_plan
    assert cli.cmd_translate is translation.cmd_translate
    assert cli.cmd_quality_audit is quality.cmd_quality_audit
    assert cli.cmd_export is export.cmd_export


def test_cli_batch_facade_passes_legacy_countdown_monkeypatches(monkeypatch):
    observed = {}

    def fake_batch(args, parser, *, countdown, countdown_until):
        observed.update({"args": args, "parser": parser,
                         "countdown": countdown, "countdown_until": countdown_until})

    marker = object()
    monkeypatch.setattr(collection, "cmd_batch_collect", fake_batch)
    monkeypatch.setattr(cli, "_batch_countdown", marker)
    monkeypatch.setattr(cli, "_batch_countdown_until", marker)
    parser = argparse.ArgumentParser()
    args = argparse.Namespace()

    cli.cmd_batch_collect(args, parser)

    assert observed == {"args": args, "parser": parser,
                        "countdown": marker, "countdown_until": marker}


def test_cli_parser_keeps_public_handler_names_for_old_dispatch_contract():
    parser = cli.build_parser()
    quota = parser.parse_args(["select-quota", "--rankings", "r.json", "--config", "c.json", "--out", "o.json"])
    export_args = parser.parse_args(["export"])

    assert quota.func is cli.cmd_select_quota
    assert export_args.func is cli.cmd_export
