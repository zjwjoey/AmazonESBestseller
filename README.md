# AmazonESBestseller

Amazon.es 畅销商品采集与选品研究项目。

目标是从 Amazon 西班牙站的 Best Sellers 榜单中采集真实畅销商品，并形成可用于内部选品、类目研究、价格研究和后续 AI 分析的结构化商品数据库。

当前项目已经能够真实运行，并已产出 Amazon.es 商品数据和中西双语 Excel。

---

# 1. Project Goal

长期目标：

> 建立 Amazon.es 主要实体商品类目的畅销商品数据库。

预计第一阶段规模：

```text
约 6,000～10,000 个唯一 ASIN
```

主要用途：

* 西班牙市场选品
* Amazon 畅销商品研究
* 类目结构研究
* 价格带研究
* 商品规格研究
* 新品/老品研究
* 后续与其他欧洲零售渠道比较
* 后续 AI 选品分析

本项目不是通用 Amazon 爬虫。

---

# 2. Current Status

当前状态：

> **真实采集已跑通，正在从可运行脚本向稳定、可测试、可重复运行的正式采集系统演进。**

已经验证：

* ✅ Amazon.es Best Sellers 页面访问
* ✅ Amazon.es 商品详情页访问
* ✅ ASIN 提取
* ✅ Best Sellers 排名采集
* ✅ 商品标题采集
* ✅ 商品链接采集
* ✅ 图片链接采集
* ✅ 当前价格采集
* ✅ 评分采集
* ✅ 评论数采集
* ✅ 品牌采集
* ✅ Parent ASIN 部分采集
* ✅ Amazon Detail BSR 采集
* ✅ 商品技术详情采集
* ✅ 商品规格整理
* ✅ 上架时间部分采集
* ✅ 西班牙语选品表生成
* ✅ 中文选品表生成
* ✅ 商品图片嵌入 Excel
* ✅ 类目规划表
* ✅ 中文翻译/规格清洗
* ✅ 数据完整度审计

当前仍需完善：

* 🟡 中文商品品名 QA
* 🟡 规格解析 QA
* 🟡 品牌误识别防护
* 🟡 类目层级完整度
* 🟡 Best Sellers ranking source 追踪
* 🟡 Browse Node
* 🟡 月购买量
* 🟡 划线原价覆盖
* 🟡 自动化 regression tests
* ⬜ 全类目规模化
* ⬜ 6,000～10,000 ASIN 稳定生产

详细状态：

`docs/CURRENT_STATE.md`

---

# 3. Current Data Scale

当前真实样本约：

```text
193～200 个 Amazon.es 商品
```

其中已经筛选出：

```text
100 个质量较高、可以直接用于内部选品研究的 SKU
```

这 100 个 SKU 已完成：

* 中文/西语 ASIN 一一对应
* 商品链接一致性检查
* 图片链接一致性检查
* 中文品名检查
* 品牌检查
* 当前价格检查
* 规格检查
* 商品图片嵌入

---

# 4. Current Output Workbook

当前业务输出主要采用三张工作表：

```text
类目规划
西班牙语选品清单
中文选品清单
```

---

## 类目规划

用于管理：

* Amazon 一级类目
* 西班牙语类目名称
* 中文类目名称
* 类目优先级
* 研究建议

---

## 西班牙语选品清单

作为接近 Amazon 原始信息的业务证据层。

主要包含：

* 序号
* ASIN
* 西班牙语商品名称
* 品牌
* 当前售价
* 划线原价
* 折扣率
* 评分
* 月购买量
* 类目层级
* 畅销榜排名
* 商品规格
* 上架时间
* 商品链接
* 图片链接

当前不要求嵌入商品图片。

---

## 中文选品清单

用于内部选品研究。

主要包含：

* 商品图片
* 序号
* ASIN
* 中文商品名称
* 品牌
* 当前售价
* 划线原价
* 折扣率
* 评分
* 月购买量
* 类目层级
* 畅销榜排名
* 中文规格
* 商品详情摘要
* 上架时间
* 商品链接
* 图片链接
* 选品状态
* 研究备注

---

# 5. Core Data Model

核心原则：

```text
Ranking Record
≠
Product Record
```

---

## Product

一条商品记录对应：

```text
1 ASIN
```

例如：

```text
ASIN
title
brand
price
image
details
specification
```

---

## Ranking Record

一条排行榜记录对应：

```text
1 ASIN
×
1 ranking context
```

同一个 ASIN 可以同时出现在多个 Amazon 排行榜。

这是正常数据。

不要因为 ASIN 相同而删除这些排名记录。

完整字段定义：

`docs/DATA_MODEL.md`

---

# 6. Important Ranking Rule

项目中存在两个完全不同的排名概念。

## Best Sellers Rank

来自：

```text
Amazon Best Sellers 页面
```

例如：

```text
#1
#12
#38
```

代表商品在当前榜单的位置。

---

## Detail BSR

来自：

```text
Amazon 商品详情页
```

例如：

```text
n.º 233 en Hogar y cocina
```

或者：

```text
180285
```

两者绝对不能混用。

详细定义：

`docs/DATA_MODEL.md`

---

# 7. Product Identity

核心商品主键：

```text
ASIN
```

示例：

```text
B078C6QR1C
```

ASIN 用于：

* 商品去重
* 排名关联
* Detail enrichment
* 图片关联
* 翻译关联
* Excel 中西文对应
* 历史追踪

不要使用：

* 商品名称
* 图片 URL
* Excel 行号

替代 ASIN。

---

# 8. Price Rules

价格定义已经冻结。

## Current Price

只保存：

> Amazon 页面实际显示的当前价格。

---

## Original Price

只保存：

> Amazon 明确显示的划线原价。

---

## Discount Rate

只有：

```text
current_price
+
original_price
```

同时存在时计算：

```text
(original_price - current_price) / original_price
```

Coupon、Prime、Promotion 等优惠信息不能自动改写正式价格字段。

---

# 9. Chinese Product Name

中文商品名称不是 Amazon 原始标题的逐字翻译。

目标格式：

```text
核心商品类型
+
关键规格/数量
+
必要兼容型号
```

例如：

```text
儿童3格便当盒
玻璃保鲜盒 12件套
咖啡机除垢液 2×250毫升
SDS Plus混凝土钻头 14×160毫米
Dedica EC680/EC685兼容滤杯手柄
```

品牌已经有独立字段时：

通常不重复写入中文商品名。

详细 QA 规则：

`docs/QA_RULES.md`

---

# 10. Specifications

规格主要回答：

> 消费者实际购买的是哪个规格？

例如：

```text
90×190×40厘米
500毫升
2×250毫升
18V 4.0Ah / 2块
8件套 / 320–1200毫升
```

规格不是所有技术详情的堆叠。

已知历史错误包括：

```text
9L → 25.4L
30L → 20L
10×15cm → 10×10mm
```

这些问题必须通过 regression tests 防止再次出现。

---

# 11. Raw Evidence

项目遵循：

```text
Raw
↓
Normalized
↓
Business Presentation
```

例如：

```text
西班牙语 Amazon 原始标题
↓
商品类型识别
↓
中文商品名称
```

原始数据不得因为中文翻译而被覆盖。

---

# 12. Development Rules

所有 AI Coding Agent 在修改代码前必须阅读：

`AGENTS.md`

其中包括：

* 不推倒重写已验证流程
* 优先最小 diff
* 不猜缺失字段
* 不混淆排名
* 不覆盖原始数据
* 不覆盖人工备注
* parser 修复必须增加 regression test
* 不增加 CAPTCHA bypass / stealth / proxy rotation 等绕过机制

---

# 13. QA Rules

项目质量标准位于：

`docs/QA_RULES.md`

核心原则：

```text
correctness
>
traceability
>
completeness
```

即：

> 宁可为空，也不要填一个看起来完整但错误的数据。

---

# 14. Repository Documentation

核心长期文档：

```text
AGENTS.md

docs/
├── CURRENT_STATE.md
├── DATA_MODEL.md
├── QA_RULES.md
├── ARCHITECTURE.md
└── ROADMAP.md
```

---

## AGENTS.md

AI Coding Agent 永久开发规则。

---

## CURRENT_STATE.md

当前真实开发状态。

会随着项目推进更新。

---

## DATA_MODEL.md

字段定义和数据关系。

---

## QA_RULES.md

数据质量和 regression rules。

---

## ARCHITECTURE.md

真实程序架构和数据流。

---

## ROADMAP.md

开发阶段、当前目标和未来扩展顺序。

---

# 15. Historical Documents

仓库中已经存在早期 reconnaissance 设计和实施计划。

这些文档记录了项目早期探索过程。

历史设计可以作为参考，但：

> 当前真实代码和当前状态优先。

不要因为历史计划与当前程序结构不同，就强制重写现有工作代码。

---

# 16. Running the Project

当前仓库仍处于从工作脚本向统一正式 CLI 整理的阶段。

因此：

> 不要在 README 中虚构不存在的统一运行命令。

在正式 CLI 冻结前：

请根据当前实际工作脚本运行项目。

当统一入口稳定后，本节应更新为正式命令，例如未来可能类似：

```text
collect
enrich
qa
export
```

但不要在命令真正实现前把它们写成已完成能力。

---

# 17. Current Development Priority

当前优先级：

```text
P0
数据正确性
```

包括：

* 中文商品类型
* 品牌
* 规格
* 排名语义

然后：

```text
P1
Regression Tests
```

然后：

```text
P1
Ranking / Category Traceability
```

然后：

```text
P2
重要缺失字段
```

包括：

* monthly bought
* Browse Node
* leaf category
* original price

最后才进入：

```text
大规模类目扩展
```

---

# 18. Recommended Expansion Sequence

建议扩展顺序：

```text
Hogar y cocina
        ↓
完整跑通
        ↓
重复运行验证
        ↓
Bricolaje y herramientas
        ↓
完整跑通
        ↓
两个一级类目稳定
        ↓
继续扩主要实体商品类目
        ↓
6,000～10,000 unique ASIN
```

不要一开始直接全站并发扩大。

---

# 19. Out of Scope for Now

除非明确提出需求，目前不优先：

* PostgreSQL
* Redis
* Celery
* Kafka
* 微服务
* Web Dashboard
* SaaS
* 云端部署
* 高并发抓取
* 多机器 Worker
* Proxy rotation
* CAPTCHA solving
* Anti-detection bypass

保持项目简单、稳定、可验证。

---

# 20. Project Principle

这个项目最终追求的不是：

> 抓最多的数据。

而是：

> 得到足够多、足够准确、能够真正用于选品决策的 Amazon.es 畅销商品数据。

核心原则：

```text
Evidence over completeness.

Correctness over appearance.

Small verified changes over large rewrites.
```
