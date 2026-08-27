# -*- coding: utf-8 -*-
"""collection/planning.py 测试：详情采集规划（新/缺/刷新/高频）+ 状态缓存。

纯离线：不联网、不依赖浏览器；时间用固定 now 保证确定性。
"""
from datetime import datetime, timedelta

from amazon_es_bestseller.collection.planning import (
    DetailState,
    build_plan,
    collect_asins,
)

NOW = datetime(2026, 8, 26, 12, 0, 0)


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _state(asins: dict) -> DetailState:
    st = DetailState(":memory:")
    for a, rec in asins.items():
        st.update([dict({"asin": a}, **rec)])
    return st


def _rank(asins, pages=("https://www.amazon.es/p1/zgbs/1",), ranks=None):
    """榜单记录：默认 rank 10+（不触发前 5 名高频）；可显式传 rank。"""
    out = []
    for i, a in enumerate(asins):
        url = pages[i] if i < len(pages) else pages[0]
        rank = ranks[i] if ranks else 10 + i
        out.append({"asin": a, "ranking_source_url": url, "bestseller_rank": rank})
    return out


FULL_REC = {
    "current_price_raw": "12,62 €",
    "title_es_raw": "Bolsa térmica",
    "rating_raw": "4,5 de 5 estrellas",
    "review_count_raw": "3.873",
    "access_state": "NORMAL",
    "collected_at": _iso(1),
}


def test_new_asin_must_collect():
    plan = build_plan(_rank(["B078C6QR1C"]), DetailState(":memory:"), now=NOW)
    assert plan["stats"] == {"total_asins": 1, "collect": 1, "skip": 0}
    assert plan["collect"][0] == {
        "asin": "B078C6QR1C", "action": "collect", "priority": "new",
        "reason": "新 ASIN 必补"}


def test_complete_fresh_skip():
    st = _state({"B078C6QR1C": FULL_REC})
    plan = build_plan(_rank(["B078C6QR1C"]), st, now=NOW)
    assert plan["stats"]["collect"] == 0
    assert plan["skip"][0]["priority"] == "fresh"


def test_complete_stale_refresh():
    st = _state({"B078C6QR1C": dict(FULL_REC, collected_at=_iso(8))})
    plan = build_plan(_rank(["B078C6QR1C"]), st, now=NOW)
    assert plan["collect"][0]["priority"] == "refresh"
    assert "7" in plan["collect"][0]["reason"]


def test_missing_key_field_incomplete():
    st = _state({"B078C6QR1C": dict(FULL_REC, current_price_raw="")})
    plan = build_plan(_rank(["B078C6QR1C"]), st, now=NOW)
    assert plan["collect"][0]["priority"] == "incomplete"
    assert "现价" in plan["collect"][0]["reason"]


def test_old_detail_schema_is_not_considered_fresh():
    st = _state({"B078C6QR1C": dict(FULL_REC, detail_schema_version=1)})
    plan = build_plan(_rank(["B078C6QR1C"]), st, now=NOW)
    assert plan["collect"][0]["priority"] == "incomplete"
    assert "schema" in plan["collect"][0]["reason"]


def test_unreliable_state_forced_recollect():
    # CHALLENGE/BLOCKED 绝不当缓存：上次访问不可信 → 强制重采
    st = _state({"B078C6QR1C": dict(FULL_REC, access_state="CHALLENGE")})
    plan = build_plan(_rank(["B078C6QR1C"]), st, now=NOW)
    assert plan["collect"][0]["priority"] == "incomplete"
    assert "CHALLENGE" in plan["collect"][0]["reason"]


def test_hot_asin_shortens_refresh_period():
    # 出现在 ≥2 个榜单页 → 高频，刷新周期 7→1 天；完整 3 天 → 需重采
    st = _state({"B078C6QR1C": dict(FULL_REC, collected_at=_iso(3))})
    pages = ["https://www.amazon.es/p1/zgbs/1", "https://www.amazon.es/p2/zgbs/2"]
    recs = [{"asin": "B078C6QR1C", "ranking_source_url": p, "bestseller_rank": 1}
            for p in pages]
    plan = build_plan(recs, st, now=NOW)
    assert "B078C6QR1C" in plan["hot_asins"]
    assert plan["collect"][0]["priority"] == "refresh"
    assert "1" in plan["collect"][0]["reason"]


def test_top_rank_is_hot_within_normal_period():
    # 榜单前 5 名 → 高频；完整 3 天在普通 7 天内，但超过高频 1 天 → 重采
    st = _state({"B078C6QR1C": dict(FULL_REC, collected_at=_iso(3))})
    recs = [{"asin": "B078C6QR1C", "ranking_source_url": "https://www.amazon.es/p1/zgbs/1",
             "bestseller_rank": 1}]
    plan = build_plan(recs, st, now=NOW)
    assert "B078C6QR1C" in plan["hot_asins"]
    assert plan["collect"][0]["priority"] == "refresh"


def test_plan_deterministic_order_and_normalization():
    # ASIN 大小写归一；collect 按优先级+ASIN 排序
    st = _state({
        "B075JJRFVV": dict(FULL_REC, collected_at=_iso(8)),       # 完整旧 → refresh
        "b078c6qr1c": dict(FULL_REC, collected_at=_iso(8)),       # 小写旧 → refresh
        "B07RN64P2R": dict(FULL_REC, collected_at=_iso(1), rating_raw=""),  # 缺评分 → incomplete
    })
    plan = build_plan(_rank(["B078C6QR1C", "B075JJRFVV", "B07RN64P2R"]), st, now=NOW)
    priorities = [i["priority"] for i in plan["collect"]]
    assert priorities == ["incomplete", "refresh", "refresh"]  # 缺=1 刷=2（新=0 不在本批）
    # 同一优先级按 ASIN 排序
    assert plan["collect"][1]["asin"] == "B075JJRFVV"
    assert plan["collect"][2]["asin"] == "B078C6QR1C"


def test_collect_asins_order():
    plan = build_plan(_rank(["B07RN64P2R", "B078C6QR1C"]), DetailState(":memory:"), now=NOW)
    # 同为 new，按 ASIN 字母序
    assert collect_asins(plan) == ["B078C6QR1C", "B07RN64P2R"]


def test_detail_state_roundtrip(tmp_path):
    path = tmp_path / "state" / "details_state.json"
    st = DetailState(path)
    st.update([{"asin": "B078C6QR1C", "current_price_raw": "12,62 €"}])
    # 最近一次整记录覆盖（ASIN 大写去重），collected_at 自动盖章
    st.update([{"asin": "b078c6qr1c", "title_es_raw": "dup", "current_price_raw": "13,50 €"}])
    st.save()
    st2 = DetailState(path)
    assert len(st2) == 1
    assert st2.get("B078C6QR1C")["title_es_raw"] == "dup"
    assert st2.get("B078C6QR1C")["current_price_raw"] == "13,50 €"
    assert "collected_at" in st2.get("B078C6QR1C")


def test_detail_state_handles_missing_corrupt(tmp_path):
    path = tmp_path / "nope.json"
    st = DetailState(path)
    assert len(st) == 0
    st.save()
    st2 = DetailState(path)
    assert len(st2) == 0
    path.write_text("{corrupt", encoding="utf-8")
    st3 = DetailState(path)
    assert len(st3) == 0  # 损坏状态不崩溃


def test_detail_state_records_full_cache_not_incremental():
    """records() 返回全量缓存：resume 场景下 update 只覆盖本次 ASIN，旧记录保留。

    cmd_collect 用它重建 details.json——直接覆盖成"本次增量"会丢已缓存详情。
    """
    st = DetailState(":memory:")
    st.update([{"asin": "B078C6QR1C", "title_es_raw": "Protector", "current_price_raw": "12,62 €"},
               {"asin": "B075JJRFVV", "title_es_raw": "Fiambrera", "current_price_raw": "9,99 €"}])
    # 第二轮 resume 只重采 1 个（增量），其余已缓存记录必须仍在
    st.update([{"asin": "B078C6QR1C", "title_es_raw": "Protector v2", "current_price_raw": "13,50 €"}])
    recs = st.records()
    assert [r["asin"] for r in recs] == ["B075JJRFVV", "B078C6QR1C"]  # 按 ASIN 排序
    assert st.records()[1]["title_es_raw"] == "Protector v2"          # 增量覆盖生效
    assert st.records()[0]["title_es_raw"] == "Fiambrera"             # 未重采记录保留
    assert all("collected_at" in r for r in recs)
