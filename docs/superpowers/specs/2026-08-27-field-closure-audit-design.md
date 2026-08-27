# 字段闭环审查升级设计

## 目标

把字段闭环审查从“检查产品 JSON 是否为空”升级为可追踪 Amazon 网页证据、原始数据、规范字段、派生字段和最终 Excel 展示的一致性审查。

## 已证实的问题

1. 历史 HTML 以 `page_01.html` 命名，现有审查只查找 `<ASIN>.html`，导致已保存页面证据没有进入审查。
2. 审查没有读取导出的工作簿，不能发现 JSON 到 Excel 的列映射、行身份或图片关联错误。
3. 原价、折扣、月购买量、已选变体、Parent ASIN、卖家等条件字段，在页面未展示时被一律记为 P2 `SOURCE_MISSING`，混淆覆盖率与程序缺陷。
4. 审查 HTML 模式未覆盖页面实际标签，例如首次上架日期的 `Producto en Amazon.es desde`。

## 范围与非目标

本次只修改离线审查能力和测试；不访问 Amazon、不改写已有商品数据或 Excel、不从空字段推断数据。

## 设计

### 1. 证据索引

新增离线 HTML 证据索引：扫描任意 `.html` 文件，优先从 `input#ASIN` 或 `input[name=ASIN]` 读取 ASIN，必要时从 `/dp/<ASIN>` URL 回退。这样既支持当前 `<ASIN>.html` 缓存，也支持历史 `page_01.html` 缓存。

每个商品字段使用该 ASIN 对应的页面 HTML。排名类目仍只使用排名页 HTML，避免把详情页 BSR 或面包屑当成榜单证据。

### 2. 闭环分类

保留真正的故障分类：

- `PARSER_MISSED`：页面证据存在，但 raw 字段为空；
- `MAPPING_MISSED`：raw 存在，但 canonical 字段为空；
- `DERIVED_MISSING`：canonical 存在，但中译、规格或展示字段为空；
- `EXPORT_MISSING` / `EXPORT_VALUE_MISMATCH`：导出的 Excel 缺行、缺列或与预期展示值不一致；
- `IMAGE_MISSING`：中文表该 ASIN 有图片链接但未嵌入图片。

页面没有展示的条件字段归为 `NOT_OBSERVED`（INFO），从缺陷统计中排除。没有保存可核验 HTML 时归为 `EVIDENCE_UNAVAILABLE`（INFO），不假装成页面未展示。

### 3. Excel 展示核验

审查函数可选接收工作簿路径和翻译映射。它读取西班牙语与中文工作表：

- 核对工作表、表头、行数、ASIN 集、序号与双表顺序；
- 用现有导出函数的只读行值规则逐列比对产品记录与 Excel 单元格；
- 检查中文图像的锚点行是否能对应到 ASIN；
- 审查不改动工作簿。

### 4. 报告

报告增加 `coverage_summary`（页面可观测、证据不可用、条件字段未展示）与 `defect_summary`（真正程序/展示缺陷）。原有 `field_summary` 保留并加入新分类，确保既能识别故障，也能说明哪些字段客观不存在。

## 验收标准

1. `page_01.html` 中的 ASIN 证据能被正确关联到商品。
2. 页面有明确证据而 raw 为空时为 `PARSER_MISSED`；没有页面字段时为 `NOT_OBSERVED`，不是 P2。
3. 页面包含 `Producto en Amazon.es desde` 时，日期字段审查能识别来源证据。
4. 一个故意改错的 Excel 单元格被判为 `EXPORT_VALUE_MISMATCH`；缺失中文图片被判为 `IMAGE_MISSING`。
5. 当前 200 SKU 缓存重新审查时，报告能够区分“历史缓存漏存字段”和“页面确实没有字段”，并保留不修改源数据。
