# Amazon.es 畅销榜侦察阶段设计

## 目标与边界

本项目只验证 Amazon.es Best Sellers 是否能作为后续畅销商品数据源。范围限于 `Hogar y cocina`（家居与厨房）的有限试跑；不实现全站采集、数据库、并发、代理、反检测、验证码处理或图片下载。

访问使用 Playwright，以低频、串行方式进行。若任一页面出现 403、429、Robot Check、验证码、访问拒绝或登录要求，立即停止本次网页访问，并保存证据。访问状态固定为 `NORMAL`、`BLOCKED`、`RATE_LIMITED`、`CHALLENGE`、`NETWORK_ERROR`、`UNKNOWN`。

历史目录 `E:\amazon_es` 中已有的 CSV、JSON 与脚本仅作为历史参考，绝不被改写或纳入本阶段运行。

## 架构

`browser_probe` 是唯一能访问 Amazon 的模块。它负责顺序导航、测量耗时、记录最终 URL/标题/状态，并保存 HTML、截图和访问事件。每个页面导航后均先运行 `access_detector`；只有结果为 `NORMAL` 才允许继续解析或访问下一页。

其余模块一律离线工作：`page_inspector` 发现商品卡候选和 JSON-LD/嵌入结构化数据；`category_discovery` 从已保存页面生成类目节点；`product_card_parser` 用主策略、回退策略和验证规则提取字段；`reports` 汇总 CSV、统计和 Markdown 报告。未来的 Creators API 仅预留接口目录，不接入或要求凭证。

## 运行流程与硬上限

1. 依次探测首页、Best Sellers 根页和厨房入口页；每页保存 HTML、截图及导航元数据。
2. 三页均为 `NORMAL` 时，对厨房入口保存的 HTML 离线分析，发现类目树、卡片结构、结构化数据及字段可用性。
3. 从实际发现的二级类目中，串行检查最多 3 个类目；每类最多采集页面自然呈现的前 50 条商品，不构造翻页或懒加载绕过。
4. 仅在前三步全程 `NORMAL` 时，从已发现 ASIN 中选择最多 5 个详情页做字段侦察；不批量进入详情页。
5. 生成结果后停止，不进入第二阶段开发。

页面之间使用固定、保守的延迟；不重试 `BLOCKED`、`RATE_LIMITED` 或 `CHALLENGE` 页面。网络错误最多记录一次失败，不进行攻击式重试。

## 数据与证据

每次运行创建独立的 `runs/YYYYMMDD_HHMMSS/`，包含 `html/`、`screenshots/`、`raw/`、`failures/`、`parsed/`、`logs/run.log` 与下列结果：

- `access_events.csv`：URL、时间、标题、状态和原因；
- `category_tree.csv`、`category_tree.json`：真实发现的类目、URL、Browse Node（可识别时）与层级；
- `ranking_records.csv`：每次榜单出现一条记录，保留类目与 Rank，不按 ASIN 去重；
- `products.csv`：按 ASIN 聚合，Parent ASIN 不能可靠确认时为 null；
- `field_availability.csv`：各字段的非空数与可用率；
- `structured_data_report.md` 与 `report.md`：实际证据、页面限制、字段分工与最终 GO/CONDITIONAL GO/NO-GO 结论。

产品卡字段包括 ASIN 与来源、标题、URL、图片 URL、明确展示的 Rank 与来源、价格/货币、评分、评论数、月购买量原文及可解析值，以及其他销售提示字段。缺失字段统一为 null；DOM 顺序绝不充当官方 Rank。ASIN 将优先从 `/dp/<ASIN>` URL 提取，再回退到 DOM 属性；低于 95% 的提取率需在报告中标为风险。

## 错误处理与测试

任何非 NORMAL 页面均保存 HTML、截图和失败原因。解析器不得因单个字段或商品卡缺失而中止；只记录 null 与统计。

测试完全离线：以每种保存的页面结构作为 fixture，覆盖访问状态识别、ASIN/Rank 提取、类目树构建、字段可用率、同一 ASIN 多榜单记录和产品聚合。线上运行只验证访问与生成工件，不以测试名义增加访问次数。

## 完成判定

报告必须依据本次保存的页面回答：可稳定字段、ASIN/Rank 成功率、类目树及深度、类目榜单深度/分页/懒加载观察、排名记录与唯一 ASIN 的重复率、详情页可补字段、页面与 Creators API 的建议分工、访问稳定性，以及 GO、CONDITIONAL GO 或 NO-GO 结论。

若访问限制在探测期出现，报告应诚实给出 NO-GO 或 CONDITIONAL GO，并停止后续访问；这属于有效侦察结果。
