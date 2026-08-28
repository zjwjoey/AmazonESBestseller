# AmazonESBestseller

Amazon.es 畅销商品采集与选品研究项目。

目标是从 Amazon 西班牙站 Best Sellers 和商品详情页采集真实商品数据，形成可用于内部选品、类目研究、价格研究和后续 AI 分析的结构化数据集。

当前项目已经能够真实运行，并已产出 Amazon.es 商品数据和中西双语 Excel。

## 1. Project Goal

长期目标：建立 Amazon.es 主要实体商品类目的畅销商品数据库。

预计后续规模：约 6,000～10,000 个唯一 ASIN、多榜单 ranking records、完整商品详情、中西双语业务展示、可重复 QA 与导出。

## 2. Core Architecture Principle

项目正式区分：

```text
数据层 ≠ 展示层
```

### 数据层

目标：尽可能无损保存 Amazon 页面公开展示的商品信息。

商品详情采集不采用固定规格字段白名单。Amazon 新出现的 Key/Value 属性，即使当前程序不认识，也应尽可能保存原始字段和值。

### 展示层

目标：一个 SKU 一行，让人快速看懂商品。

Excel 不需要把全部动态字段展开成几百列。完整动态详情通过 `完整商品详情` 字段统一展示。

## 3. Current Status

已经验证：Amazon.es Best Sellers 页面访问、商品详情页访问、ASIN、Best Sellers 排名、商品标题、商品链接、图片链接、当前价格、评分、评论数、品牌、Parent ASIN 部分采集、Detail BSR、技术详情、规格整理、上架时间部分采集、西班牙语选品表、中文选品表、图片嵌入 Excel、类目规划、中文翻译/规格清洗、数据审计。

当前主链已包含详情身份/访问门禁、动态详情、月购买量 raw 提取、划线原价语义校验、QA、备注保护和三表导出。尚未承诺的是全类目规模化和 6,000～10,000 ASIN 稳定生产。

详细状态见 `docs/CURRENT_STATE.md`。

## 4. Product Detail Strategy

商品详情页应按“全量原始详情”处理，而不是只抽取固定几个规格字段。

建议保留的详情区块包括 Product Overview、Technical Details、Additional Information、selected variation、About this item / feature bullets、Product Description、A+ text where collected、BSR、Date First Available 和其他 visible Key/Value fields。

完整原始详情属于数据层。`核心规格` 只是完整详情数据的一个派生摘要。

## 5. Default Excel Export

当用户要求“按项目默认规则导出 Excel”且没有指定其他表结构时，默认输出三张表：

1. `类目规划`
2. `西班牙语选品清单`
3. `中文选品清单`

## 6. Default Chinese Product Sheet

`中文选品清单` 默认一 SKU 一行，共 26 列：

```text
01 图片
02 序号
03 ASIN
04 Parent ASIN
05 商品名称（中文）
06 品牌
07 当前售价
08 划线原价
09 折扣率
10 评分
11 评论数
12 月购买量
13 一级类目
14 二级类目
15 三级类目
16 细分类目
17 畅销榜排名
18 当前选中规格 / 变体
19 核心规格（中文）
20 完整商品详情（中文）
21 商品卖点（中文）
22 首次上架日期
23 卖家
24 商品链接
25 图片链接
26 备注
```

默认不包含 `配送方式`。

以前的 `选品状态` 和 `研究备注` 已合并为 `备注`。

## 7. Meaning of the Detail Display Fields

### 当前选中规格 / 变体

Amazon 当前 SKU 实际选中的规格或变体，例如 30 L、Rosa / 900 ml、Pack de 2。

### 核心规格

用于快速扫表，例如 `4件套 / 1升 / 17×3.2×25.2厘米`。它不是全部商品详情。

### 完整商品详情

把数据层采集到的动态 Key/Value 详情整理成中文可读文本。Amazon 抓到多少有效详情，就尽可能展示多少。

### 商品卖点

来自 Amazon `Acerca de este producto` / About this item bullet points。不要与结构化详情混为一谈。

## 8. Spanish Sheet

`西班牙语选品清单` 与中文表保持相同 SKU 集合和相同业务逻辑。重点对应字段使用西语原始/整理内容，包括商品名称、当前选中规格/变体、核心规格、完整商品详情、商品卖点。

中文表和西语表必须按 ASIN 一一对应。西语表默认不要求嵌入图片。

## 9. Ranking Rule

Best Sellers 排名来自 Amazon Best Sellers 页面，Detail BSR 来自商品详情页，两者绝对不能混用。

完整定义见 `docs/DATA_MODEL.md`。

## 10. Product Identity

核心商品主键：`ASIN`。

ASIN 用于商品去重、ranking 关联、detail enrichment、图片关联、翻译关联、Excel 对应和历史追踪。

## 11. Data Quality Principle

核心原则：

```text
correctness > traceability > completeness
```

宁可为空，也不要填一个看起来完整但错误的数据。

详细 QA 见 `docs/QA_RULES.md`。

## 13. Installation and offline verification

```powershell
python -m pip install -e ".[test]"
python -m playwright install chromium   # only when live browser collection is explicitly needed
amazon-es --help
python -m pytest -q -rs
```

Offline commands are `select-quota`, `enrich`, `repair-cache`, `reparse-details`, `audit-detail-cache`, `qa`,
`audit-fields` and `export`. `collect` and `translate-ds` are the only commands that
require external services (`collect` uses low-frequency serial browser access; the latter
calls DeepSeek only when explicitly run). Live collection tests run only when `RUN_LIVE=1`
is explicitly set. CI and the default test suite never access Amazon or DeepSeek, and the
project does not provide CAPTCHA, proxy-rotation or stealth bypass behavior.

`reparse-details --html-dir DIR1 DIR2 ...` accepts multiple saved-HTML directories;
for duplicate ASINs, the first valid record in the supplied directory order wins.
`translate-ds` reports `success`, `partial` and `failed` counts separately so partial
translation is not mistaken for a complete failure. Before constructing the DS client,
it prints the request summary and requires an explicit `YES`; any other input (including
EOF) cancels without an API request. `--offline translate-ds` is rejected.

The minimum local verification is:

```powershell
python -m pip install -e ".[test]"
amazon-es --help
python -m pytest -q -rs
```

`run_manifest.py` provides JSON-only run metadata helpers for a future orchestrator; no
`amazon-es run` command or scheduler is implemented in the current phase.

### Field Closure Audit

The offline `audit-fields` command follows `collect → enrich → translate-ds → enrich → qa → audit-fields →
export` and emits deterministic JSON plus Markdown. It diagnoses automatic-field
gaps as `SOURCE_MISSING`, `PARSER_MISSED`, `MAPPING_MISSED` or `DERIVED_MISSING`;
source-missing values remain empty rather than being guessed.

```powershell
amazon-es audit-fields --products outputs/products.json `
  --details outputs/details.json --rankings outputs/rankings.json `
  --out outputs/field_closure.json
```

## 12. Documentation

核心文档：`AGENTS.md`、`docs/CURRENT_STATE.md`、`docs/DATA_MODEL.md`、`docs/QA_RULES.md`、`docs/ARCHITECTURE.md`、`docs/ROADMAP.md`。

## 14. Project Principle

```text
Data Layer: 尽可能无损
Display Layer: 快速可读
QA: 宁缺毋错
```

最终目标不是抓最多的数据，而是得到足够多、足够准确、能真正支持选品判断的 Amazon.es 商品数据。

导出前会执行字段闭环审计；翻译缓存按 ASIN、schema 和西语源哈希复用，详情 schema
升级优先离线重解析保存 HTML。150 家居 + 50 DIY 是全局唯一配额，缺口会以
`QUOTA_UNIQUE_SHORTFALL` 明确失败。
