import json
from pathlib import Path

from amazon_es_bestseller import cli


def test_batch_collect_command_is_registered():
    parser = cli.build_parser()
    args = parser.parse_args([
        "batch-collect", "--plan", "plan.json", "--out-dir", "out",
    ])
    assert args.command == "batch-collect"
    assert args.cooldown_seconds is None
    assert args.rankings_only is False


def test_batch_countdown_keeps_process_alive(monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))
    cli._batch_countdown(2, "测试类目")
    assert sleeps == [1, 1]
    output = capsys.readouterr().out
    assert "冷却倒计时" in output
    assert "冷却完成" in output


def _batch_args(parser, plan, out, *extra):
    return parser.parse_args(["batch-collect", "--plan", str(plan),
                              "--out-dir", str(out), *map(str, extra)])


def _plan(path, url="https://www.amazon.es/gp/bestsellers/beauty/123/"):
    path.write_text(json.dumps({"cooldown_between_categories_seconds": 0,
                                "sources": [{"category_group": "beauty",
                                             "category_name_zh": "美妆",
                                             "category_sequence": 1,
                                             "source_url": url,
                                             "status": "pending"}]}), encoding="utf-8")
    return url


def _plan_groups(path, cooldown, groups=2):
    sources = []
    for index in range(groups):
        group = "beauty" if index == 0 else "electronics"
        url = f"https://www.amazon.es/gp/bestsellers/{group}/{index + 100}/"
        sources.append({"category_group": group, "category_name_zh": group,
                        "category_sequence": index + 1, "source_url": url,
                        "status": "pending"})
    path.write_text(json.dumps({"cooldown_between_categories_seconds": cooldown,
                                "sources": sources}), encoding="utf-8")
    return [row["source_url"] for row in sources]


def _patch_batch_network(monkeypatch, ranking_calls=None):
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", _FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *a, **k: None)
    def rankings(urls, session, out, pages_per_url=1):
        if ranking_calls is not None:
            ranking_calls.extend(urls)
        return [{"asin": f"B{index:09d}", "ranking_source_url": url,
                 "bestseller_rank": index + 31}
                for url in urls for index in range(20)]
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings", rankings)
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details", lambda *a, **k: [])


class _FakeSession:
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


def test_batch_existing_products_skip_detail_request(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    url = _plan(plan)
    existing = tmp_path / "products.json"
    existing.write_text(json.dumps([{"asin": "B000000000"}]), encoding="utf-8")
    parser = cli.build_parser()
    calls = []
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", _FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *a, **k: None)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings",
                        lambda urls, session, out, pages_per_url=1: [
                            {"asin": "B000000000", "ranking_source_url": url,
                             "bestseller_rank": rank} for rank in range(31, 51)])
    def fake_details(*args, **kwargs):
        calls.append(args[0]); return []
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details", fake_details)
    cli.cmd_batch_collect(_batch_args(parser, plan, out, "--existing-products", existing), parser)
    assert calls == []
    assert len(json.loads((out / "rankings.json").read_text(encoding="utf-8"))) == 20


def test_batch_shortfall_is_retryable(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    url = _plan(plan)
    parser = cli.build_parser()
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", _FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *a, **k: None)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings",
                        lambda urls, session, out, pages_per_url=1: [{
                            "asin": "B000000001", "ranking_source_url": url,
                            "bestseller_rank": 31}])
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details", lambda *a, **k: [])
    cli.cmd_batch_collect(_batch_args(parser, plan, out), parser)
    state = json.loads((out / "batch_state.json").read_text(encoding="utf-8"))
    assert url not in state["completed_source_urls"]
    assert state["shortfall_sources"][url]["observed"] == 1


def test_batch_failed_detail_is_persisted_for_retry(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    url = _plan(plan)
    parser = cli.build_parser()
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", _FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *a, **k: None)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings",
                        lambda urls, session, out, pages_per_url=1: [
                            {"asin": "B000000002", "ranking_source_url": url,
                             "bestseller_rank": rank} for rank in range(31, 51)])
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details", lambda *a, **k: [])
    cli.cmd_batch_collect(_batch_args(parser, plan, out), parser)
    state = json.loads((out / "batch_state.json").read_text(encoding="utf-8"))
    assert "B000000002" in state["pending_detail_asins"]


def test_zero_cooldown_clears_stale_future_state_without_wait(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    _plan(plan)
    out.mkdir()
    (out / "batch_state.json").write_text(json.dumps({
        "cooldown_until": 4102444800, "cooldown_category": "旧类目"
    }), encoding="utf-8")
    parser = cli.build_parser()
    _patch_batch_network(monkeypatch)
    monkeypatch.setattr(cli, "_batch_countdown_until", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stale cooldown waited")))
    cli.cmd_batch_collect(_batch_args(parser, plan, out), parser)
    state = json.loads((out / "batch_state.json").read_text(encoding="utf-8"))
    assert state["cooldown_until"] is None
    assert state["cooldown_category"] is None


def test_zero_cooldown_moves_directly_to_next_category(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    _plan_groups(plan, 0)
    parser = cli.build_parser(); calls = []
    _patch_batch_network(monkeypatch, calls)
    monkeypatch.setattr(cli, "_batch_countdown_until", lambda *a, **k: (_ for _ in ()).throw(AssertionError("zero cooldown waited")))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("zero cooldown slept")))
    cli.cmd_batch_collect(_batch_args(parser, plan, out), parser)
    assert len(calls) == 2


def test_positive_cooldown_retains_resumable_countdown(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    _plan_groups(plan, 5)
    parser = cli.build_parser(); deadlines = []
    _patch_batch_network(monkeypatch)
    monkeypatch.setattr(cli, "_batch_countdown_until", lambda deadline, category: deadlines.append((deadline, category)))
    cli.cmd_batch_collect(_batch_args(parser, plan, out), parser)
    assert len(deadlines) == 1
    assert deadlines[0][0] > cli.time.time()


def test_cli_zero_overrides_plan_cooldown(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    _plan_groups(plan, 1800)
    parser = cli.build_parser(); calls = []
    _patch_batch_network(monkeypatch, calls)
    monkeypatch.setattr(cli, "_batch_countdown_until", lambda *a, **k: (_ for _ in ()).throw(AssertionError("CLI zero waited")))
    cli.cmd_batch_collect(_batch_args(parser, plan, out, "--cooldown-seconds", 0), parser)
    assert len(calls) == 2


def test_cli_positive_overrides_zero_plan(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"; out = tmp_path / "out"
    _plan_groups(plan, 0)
    parser = cli.build_parser(); deadlines = []
    _patch_batch_network(monkeypatch)
    monkeypatch.setattr(cli, "_batch_countdown_until", lambda deadline, category: deadlines.append(deadline))
    cli.cmd_batch_collect(_batch_args(parser, plan, out, "--cooldown-seconds", 10), parser)
    assert len(deadlines) == 1
