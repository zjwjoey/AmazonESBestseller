# -*- coding: utf-8 -*-
"""
AmazonESBestseller — 基础功能板块（离线核心 + 采集 + 导出）。

此包从仓库根目录的历史一次性脚本中抽取稳定逻辑，
作为未来统一 CLI / 生产管线的并行地基。历史脚本保持不动、继续可用。

分层（对应 docs/ARCHITECTURE.md）：
  access/      浏览器访问层（访问状态检测，保守、无绕过）
  collection/  采集层（榜单页 / 详情页，纯解析器可离线测试）
  normalization/  离线规范化（日期/品牌/价格/规格/BSR/类目/月购）
  translation/ 中文业务派生层（确定性词典 + 商品类型 + ASIN 例外）
  qa/          校验层（PASS/WARN/FAIL/SOURCE_CONFLICT）
  export/      呈现层（Excel 工作簿，不做业务推断）
"""

__version__ = "0.1.0"
