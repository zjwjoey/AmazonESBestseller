# 卖家漏提与 698 条待复核：程序修复及离线核验

日期：2026-10-24。结果：本轮确认的程序缺陷已修复并通过关联离线回归；仍保留源数据复核项。未宣称本批次正式交付或生产验收通过。

## 本轮结论

698 个唯一 ASIN 全部使用原保存 HTML 重放，698/698 为有效商品页面、身份匹配，698 份原始 HTML SHA-256 核对一致。原有 attributes 及 ordered_detail_evidence 逐项计数核验，无丢失。四份原始 DetailState 与先前审查哈希一致。

| 复核结果（按 ASIN 去重） | 数量 | 含义 |
| --- | ---: | --- |
| 本轮离线复核完成 | 398 | 只表示本次来源/规则复核完成，不表示翻译、Excel 或生产验收通过 |
| 类目口径待确定 | 296 | 榜单与详情路径各自有来源，不能把两条不同路径拼起来 |
| 原页面单位冲突待复核 | 4 | 原文保留、告警保留，未换算或猜填容量 |
| 合计 | 698 | 已确认卖家漏提与待复核集合重叠，不另加成 699 |

## 已修复的程序问题

1. **卖家漏提与报价区判定。** B0D9L982GG 的真实卖家为 Haisenbao-EU。保存页 TurboCheckout 脚本与换行边界会影响 BeautifulSoup 的流式解析：保存文件读取时 CRLF → LF 可导致 BuyBox 被吞入脚本文本。增加仅针对实际 DOM 卖家控件的恢复，保留 seller_evidence；同时测试原字节换行与 LF 两种形式。不同报价同时存在时，只在明确存在 active merchant 块时区分当前报价；不能把所有 telemetry=false 都当隐藏，也不从配送方或推荐区猜卖家。
2. **Ring 型号规格漏提。** B0FZPRQ9KS 的标准属性为空。仅当唯一 current 列与该 ASIN 链接一致时读取该列，恢复 5 条文字事实；没有文字说明的图标保留原始 markup，不猜是/否。相邻 4K / Wi-Fi 6 型号、缺少 current 标识、身份不匹配或跨度不明确时拒绝拼入。对比价格只留原始属性，不代替当前售价。
3. **核心规格交接。** 西语规格优先保留明确选中容量和包装数量，再考虑明确标题件数；已有尺寸时也不能丢件数。选中单件不得被技术表的 12 件覆盖；原始属性不修改。B0001IW56S 的 0,75 L 不再被技术容量 4500 ml 盖过；B00H52GC2G 的 15 piezas 得以保留。
4. **审查误报。** 修正 l./an./al. 与升的识别、30l. metros 长度缩写、Litros/Mililitros 单位、双向控制字符对原文比对的影响，以及 1×1×1,8 被截成 1×1×1 的误报。有明确原始属性的质量容量保留其质量含义；无对应证据或明确液体体积字段仍执行单位门禁。
5. **品牌核验。** 长品牌须与明确来源一致；Clim Profesional 等含通用词的具名品牌，须品牌店铺与 Marca 两处一致才解除该类误报。纯通用词、单一不足证据和不一致证据继续拦截。
6. **来源审查判定。** 新品/喜爱榜不得套用 Detail BSR 为畅销榜排名；确实填入不适用排名仍为 P1。不同类目路径单独标为 CATEGORY_SOURCE_CONFLICT/P2 待复核，不假称漏提后拼接。月购仅认当前商品证据；空模板、脚本文字和隐藏属性不充当可见原始事实。

## 哪些不是采集程序重复提取

233 个商品涉及 364 组重复事实，全部核对到不同的原始 DOM 表格行。它们属于来源重复展示，本轮没有删掉这些原始行。296 个商品对应的 888 个深层类目项仍保留来源冲突标记；不能自动决定采用哪条路径。

原始问题类别如下，同一 ASIN 可出现多类，不能相加当商品数：

| 原问题代码 | 涉及 ASIN |
| --- | ---: |
| SPEC_UNIT_MISMATCH | 122 |
| SPEC_VARIANT_MISMATCH | 12 |
| CATEGORY_SOURCE_CONFLICT | 296 |
| DETAIL_ITEM_DUPLICATED | 233 |
| BRAND_FALSE_POSITIVE | 10 |
| SPEC_QUANTITY_CONFLICT | 68 |
| CONFIRMED_PARSER_MISSED | 1 |
| PARSER_MISSED | 1 |
| SPEC_SUSPICIOUS_VALUE | 1 |

## 仍保留的真实来源风险

| ASIN | 保存页原文 |
| --- | --- |
| B01MS3PKWM | Capacidad / Capacidad de salida = 32 centímetros（2 条告警） |
| B0CG9N8R8L | Capacidad = 30 centímetros |
| B0CN8XHNJB | Capacidad = 30 centímetros |
| B0FBKT9DV9 | Capacidad = 35 centímetros |

以上 4 个商品共 5 条单位告警：页面把长度单位填在容量属性中。保留原文和相关尺寸，不做厘米到升的换算，不用猜值消除正式门禁。本次复核不是对所有价格、翻译和展示字段的重新验收；上一轮独立的原价≤现价问题也未被此报告宣布消失。

## 测试与证据

- 562 项关联离线测试通过，0 失败、0 错误、0 跳过，覆盖 34 个测试文件；其中本轮保存页及边界回归 33 项。范围包含详情、规格、来源/字段审查、归一化流水线、Excel 导出、人工备注、配送/访问门禁、断点与并行队列。
- 12 份完整真实 HTML gzip fixture 均保留原始解压 SHA-256，另保留一份品牌事实证据片段；修复前已复现失败用例。
- 完整 698 条解析/身份重放完成；最终 QA 又使用同一重放 raw 与当前规则离线复核，见 after_final.jsonl。未以人造 READY、网络 receipt 或猜测字段替代上游证据。
- Python compileall 与三个既有 CLI help 冒烟通过；git diff --check 通过。
- 本轮未完成全仓库回归、未触发 GitHub CI，因此不声明全仓库测试或远端 CI 通过。全仓库测试尝试在无关的重量级快照测试阶段停止，最终结论依据上述关联回归及完整目标集重放。
- 所有重放/终审进程禁止 socket 网络连接；Amazon 请求 0、真实翻译 API 0。生产采集、数据库迁移、历史清理、部署均未执行。

## 代码和数据状态

修复位于独立工作树 `C:\Users\Administrator\.codex\worktrees\action5500-handoff-fix\AmazonESBestseller`，分支 `codex/field-review-698-20261024`。本轮未提交 PR、未合并 main、未更新实际运行目录。运行程序、原有本地未提交改动、5412 条原始采集结果及旧审查工作簿均未覆盖；新结果是独立的离线复核副本，不是正式交付数据替换。

机读证据目录：`F:\AmazonESBestseller\runtime\action_detail5500_live_20261023_161301_8b4e\review_698_20261024`。输入目标/证据为 targets.json，原规则对照为 before.jsonl，完整解析输出为 after.jsonl，最终规则复核为 after_final.jsonl，逐项结论为 review_details.json，来源重复核对为 duplicate_source_review.json，测试为 compatibility_tests.xml。审查前后四份 DetailState 哈希见 final_summary.json。

本交付目录另附 `698条逐项复核.md` 和 `698条复核明细.json`。下一步可以将此修复分支整理为 PR 并执行对应 CI；目前不应把离线复核结论等同于已更新运行程序。
