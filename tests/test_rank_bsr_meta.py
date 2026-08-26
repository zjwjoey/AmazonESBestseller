# -*- coding: utf-8 -*-
"""排名/BSR 防混用 meta-test（QA_RULES §5/§9/§10/§12，计划 A4）。

生产代码（src/amazon_es_bestseller/）绝不允许"从排名位置构造 BSR 文本"：
- ``bestseller_rank`` 只能来自榜单徽章（collection/ranking.py 用 span.a-badge-text）；
- ``detail_bsr`` 只能来自详情页解析（collection/detail.py），类目来自页面证据；
- 任何以 ``"n.º ... en <字面量类目>"`` 构造 BSR 字符串的代码 = 臆造，必须拒绝
  （bestseller_rank 一旦被包装成"n.º N en X"外观即为混用/造假）。

``build_output.py`` 的 ``"n.º %s en Hogar y cocina" % Rank`` 是已知历史错误：
把榜单 rank 包装成详情 BSR 且硬编码类目。钉死为"仅旧脚本存在该模式"，
生产链（新主链）必须永久复制该行为。B3 归档到 historical/ 后本测试继续有效。
"""
from __future__ import annotations

import re
from pathlib import Path

import amazon_es_bestseller

SRC_ROOT = Path(amazon_es_bestseller.__file__).resolve().parent
REPO_ROOT = SRC_ROOT.parent.parent

#: 构造签名：引号内 "n.º/nº ... en <字面量类目>"（类目不是 %s/{} 占位符 → 臆造）
_FABRICATION_RE = re.compile(
    r'''["']n\.?º[^"']*?en\s+[^"%'][^"']*["']''', re.IGNORECASE)


def _src_py_files():
    return sorted(SRC_ROOT.rglob("*.py"))


def _build_output_path():
    for p in (REPO_ROOT / "build_output.py", REPO_ROOT / "historical" / "build_output.py"):
        if p.exists():
            return p
    return None


def test_regression_no_rank_to_bsr_fabrication_in_src():
    """生产代码不得构造 BSR 文本（类目字面量 = 臆造）。"""
    hits = []
    for path in _src_py_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue  # 注释/docstring 不含执行逻辑
            if _FABRICATION_RE.search(line):
                hits.append((str(path.relative_to(SRC_ROOT)), lineno, line.strip()))
    assert not hits, "生产代码不得构造 BSR 文本: %r" % hits


def test_regression_detail_bsr_reformat_is_placeholder():
    """detail.py 对详情页 BSR 的归一化必须用占位符类目（%s），不是字面量。"""
    detail_src = (SRC_ROOT / "collection" / "detail.py").read_text(encoding="utf-8")
    assert '"n.º %s en %s"' in detail_src, "详情 BSR 归一化应保留类目占位符"
    # 归一化行不得是臆造签名（类目为字面量）
    assert not _FABRICATION_RE.search(detail_src)


def test_regression_build_output_fabrication_pinned():
    """build_output.py 的 rank→BSR 构造钉死为已知历史错误（唯一允许存在的旧模式）。"""
    path = _build_output_path()
    assert path is not None, "build_output.py 应存在（根目录或 historical/）"
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FABRICATION_RE.search(line) and re.search(r'\bRank\b|\brank\b', line):
            found = line.strip()
            break
    assert found, (
        "build_output.py 应包含 rank→BSR 构造签名（已知失败 fixture），"
        "形如 'n.º %s en Hogar y cocina' % Rank"
    )
    # 构造的类目是硬编码的，不是从页面证据解析的
    assert 'Hogar y cocina' in found


def test_regression_bestseller_rank_never_from_index():
    """parse_bestsellers_page 中 bestseller_rank 与 index 必须是两个独立字段。"""
    ranking_src = (SRC_ROOT / "collection" / "ranking.py").read_text(encoding="utf-8")
    assert '"bestseller_rank": rank' in ranking_src, "排名应来自徽章解析的 rank"
    assert '"index": i' in ranking_src, "DOM 顺序应单独存 index"
