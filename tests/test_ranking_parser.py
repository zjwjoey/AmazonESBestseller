# -*- coding: utf-8 -*-
"""collection/ranking.py 测试：显式徽章 → 排名；DOM 顺序 → index；节点类目（B1）。

browse_node_id / category_l1..l3 / leaf_category 是一等字段（QA_RULES §6/§13）：
主源 = 榜单 URL 的 /zgbs/<NODE> + 面包屑节点类目路径；无证据 → None，不臆造。
"""
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page

#: 叶节点榜单页（Juegos de recipientes），节点号在 URL 中
SRC = ("https://www.amazon.es/Best-Sellers-Hogar-y-cocina-"
       "Almacenamiento-y-organizacion/zgbs/689078031")
T = "2026-08-26T00:00:00Z"


def test_parse_three_rows(bestsellers_grid_html):
    records = parse_bestsellers_page(bestsellers_grid_html, SRC, T)
    assert len(records) == 3
    assert records[0] == {
        "index": 0,
        "asin": "B078C6QR1C",
        "category_l1": "Hogar y cocina",
        "category_l2": "Almacenamiento y organización",
        "category_l3": "Juegos de recipientes",
        "leaf_category": "Juegos de recipientes",
        "browse_node_id": "689078031",
        "bestseller_rank": 1,
        "ranking_source_url": SRC,
        "collected_at": T,
    }
    assert records[1]["bestseller_rank"] == 2
    assert records[2]["asin"] == "B07RN64P2R"
    assert records[2]["bestseller_rank"] == 3


def test_browse_node_and_category_first_class(bestsellers_grid_html):
    # B1：节点类目来自榜单 URL + 面包屑证据，页面所有记录共享同一上下文
    records = parse_bestsellers_page(bestsellers_grid_html, SRC, T)
    for r in records:
        assert r["browse_node_id"] == "689078031"
        assert r["category_l1"] == "Hogar y cocina"
        assert r["category_l2"] == "Almacenamiento y organización"
        assert r["category_l3"] == "Juegos de recipientes"
        assert r["leaf_category"] == "Juegos de recipientes"
        assert r["ranking_source_url"] == SRC


def test_browse_node_fallback_from_breadcrumb_when_url_bare(bestsellers_grid_html):
    # source_url 无节点号时，回退到面包屑最深类目链接的节点（仍是页面证据）
    bare = "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs"
    records = parse_bestsellers_page(bestsellers_grid_html, bare, T)
    assert records[0]["browse_node_id"] == "689078031"
    assert records[0]["category_l1"] == "Hogar y cocina"


def test_no_breadcrumb_categories_null():
    # QA_RULES §6/§73：无面包屑 → 类目路径全 None，绝不臆造。
    # 用无节点号的 URL，browse_node_id 也无法回退 → 全 None。
    html = """
    <html><body>
      <div id="gridItemRoot">
        <span class="a-badge-text">#1</span>
        <a href="/dp/B078C6QR1C"><span>Sin breadcrumb</span></a>
      </div>
    </body></html>
    """
    bare = "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs"
    records = parse_bestsellers_page(html, bare, T)
    assert records[0]["browse_node_id"] is None
    assert records[0]["category_l1"] is None
    assert records[0]["category_l2"] is None
    assert records[0]["category_l3"] is None
    assert records[0]["leaf_category"] is None


def test_breadcrumb_root_links_and_repeats_excluded(bestsellers_grid_html):
    # 根链接（Los más vendidos）与当前页重复链接不进入类目路径
    records = parse_bestsellers_page(bestsellers_grid_html, SRC, T)
    assert records[0]["category_l1"] != "Los más vendidos"
    assert records[0]["category_l3"] != "Los más vendidos"


def test_no_badge_rank_none_but_index_recorded():
    # QA_RULES §11：无徽章 → bestseller_rank=None，index 仍为 DOM 序
    html = """
    <html><body>
      <div id="gridItemRoot">
        <a href="/dp/B078C6QR1C"><span>Producto sin badge</span></a>
      </div>
      <div id="gridItemRoot">
        <span class="a-badge-text">#5</span>
        <a href="/dp/B075JJRFVV"><span>Otro</span></a>
      </div>
    </body></html>
    """
    records = parse_bestsellers_page(html, SRC, T)
    assert records[0]["bestseller_rank"] is None
    assert records[0]["index"] == 0
    assert records[1]["bestseller_rank"] == 5
    assert records[1]["index"] == 1


def test_missing_dp_link_skipped():
    html = """
    <html><body>
      <div id="gridItemRoot"><span class="a-badge-text">#1</span><span>Sin link</span></div>
      <div id="gridItemRoot"><a href="/dp/B078C6QR1C"><span>OK</span></a></div>
    </body></html>
    """
    records = parse_bestsellers_page(html, SRC, T)
    assert len(records) == 1
    assert records[0]["asin"] == "B078C6QR1C"


def test_asin_uppercased():
    html = """
    <html><body>
      <div id="gridItemRoot">
        <span class="a-badge-text">#9</span>
        <a href="/dp/b078c6qr1c"><span>lower</span></a>
      </div>
    </body></html>
    """
    records = parse_bestsellers_page(html, SRC, T)
    assert records[0]["asin"] == "B078C6QR1C"
    assert records[0]["bestseller_rank"] == 9


# ---------- 现代 Amazon.es 页面结构（2026-08-26 真实页面核实） ----------
# 排名徽章 → span.zg-bdg-text（旧 a-badge-text 已下线）；类目层级 → unv 父级链 +
# h1 "Los más vendidos en X"；URL 节点 → /gp/bestsellers/<slug>/<NODE>/
MODERN_TOP_HTML = """
<html><body>
  <h1 class="a-size-large a-spacing-medium a-text-bold">Los más vendidos en Hogar y cocina</h1>
  <ul class="_p13n-zg-nav-tree-all_style_zg-browse-group__88fbz">
    <li><span><a href="/gp/bestsellers/ref=zg_bs_unv_kitchen_0_2">Cualquier departamento</a></span></li>
  </ul>
  <div id="gridItemRoot" class="_cDEzb_grid-column_2hIsc">
    <div data-asin="B078C6QR1C">
      <div class="zg-bdg-ctr"><span class="zg-bdg-text">#1</span></div>
      <a href="/Utopia-Protector/dp/B078C6QR1C/ref=zg_bs_g_kitchen">Protector</a>
    </div>
  </div>
  <div id="gridItemRoot">
    <div data-asin="B07RN64P2R">
      <div class="zg-bdg-ctr"><span class="zg-bdg-text">#2</span></div>
      <a href="/Lunchbag/dp/B07RN64P2R/ref=zg_bs_g_kitchen">Lunch bag</a>
    </div>
  </div>
</body></html>
"""

MODERN_SUB_HTML = """
<html><body>
  <h1 class="a-size-large">Los más vendidos en Almacenamiento y organización</h1>
  <ul class="_p13n-zg-nav-tree-all_style_zg-browse-group__88fbz">
    <li><span><a href="/gp/bestsellers/ref=zg_bs_unv_kitchen_0_3359926031_2">Cualquier departamento</a></span></li>
    <li><span><a href="/gp/bestsellers/kitchen/ref=zg_bs_unv_kitchen_1_3359926031_1">Hogar y cocina</a></span></li>
  </ul>
  <div id="gridItemRoot">
    <div data-asin="B07RN64P2R">
      <div class="zg-bdg-ctr"><span class="zg-bdg-text">#1</span></div>
      <a href="/Lunchbag/dp/B07RN64P2R/ref=zg_bs_g_kitchen">Lunch bag</a>
    </div>
  </div>
</body></html>
"""


def test_modern_top_page_rank_category_node_none():
    """现代顶级页：zg-bdg-text 徽章 → rank；unv 只含根 → 类目=Hogar y cocina；URL 无节点 → node None。"""
    url = "https://www.amazon.es/gp/bestsellers/kitchen/ref=zg_bs_nav_kitchen_0"
    records = parse_bestsellers_page(MODERN_TOP_HTML, url, T)
    assert len(records) == 2
    assert records[0]["bestseller_rank"] == 1
    assert records[1]["bestseller_rank"] == 2
    assert records[0]["category_l1"] == "Hogar y cocina"
    assert records[0]["category_l2"] is None
    assert records[0]["browse_node_id"] is None          # 顶级页无节点号，不臆造
    assert records[0]["asin"] == "B078C6QR1C"


def test_modern_subcategory_page_full_trail_and_node():
    """现代子类页：unv 父级链 + h1 当前类目 → Hogar y cocina > Almacenamiento y organización；
    /gp/bestsellers/<slug>/<NODE>/ URL → browse_node_id。"""
    url = "https://www.amazon.es/gp/bestsellers/kitchen/3359926031/ref=zg_bs_nav_kitchen_1"
    records = parse_bestsellers_page(MODERN_SUB_HTML, url, T)
    r = records[0]
    assert r["bestseller_rank"] == 1
    assert r["browse_node_id"] == "3359926031"
    assert r["category_l1"] == "Hogar y cocina"
    assert r["category_l2"] == "Almacenamiento y organización"
    assert r["category_l3"] is None
    assert r["leaf_category"] == "Almacenamiento y organización"
