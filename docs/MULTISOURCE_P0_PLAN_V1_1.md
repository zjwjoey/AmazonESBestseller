# Multi-Source Snapshot V1.1：P0 实时测试待审方案

本方案尚未获得实时采集授权。计划文件的 `live_enabled=false`、授权模板的 `approved=false`，开发期间没有执行这些 URL 的网络访问。历史导航证据仅支持候选入口的来源，不证明入口现在可用。

## 1. 拟执行范围及证据

顺序为五种榜单候选根入口，然后按原研究类目顺序串行访问十五个 Best Sellers 细分类目候选入口。仅访问各入口第一页；只有页面实际提供 ACP 元数据、授权范围允许、剩余预算足够时才请求对应 ACP 续载。不自动添加分页、不抓商品详情、不从 Best Sellers 路径替换生成其他榜单类目 URL。

五个根地址由任务书提出，均为待验证候选：

| 顺序 | 榜单类型 | 入口 |
| --- | --- | --- |
| 1 | BEST_SELLERS | https://www.amazon.es/gp/bestsellers |
| 2 | MOVERS_SHAKERS | https://www.amazon.es/gp/movers-and-shakers |
| 3 | NEW_RELEASES | https://www.amazon.es/gp/new-releases |
| 4 | MOST_WISHED_FOR | https://www.amazon.es/gp/most-wished-for |
| 5 | MOST_GIFTED | https://www.amazon.es/gp/most-gifted |

细分类目选择同时满足两项证据：此前已审核 5,300 SKU 计划中的明确研究类目映射，以及历史 Amazon 导航树中实际保存的同一链接。仅去除明确追踪字段后匹配 URL；下表没有新造地址。

| 顺序 | 研究类目 | 原始 Amazon 导航名称 | Browse Node | 拟访问 URL |
| --- | --- | --- | --- | --- |
| 6 | 居家生活 | Muebles | 2822691031 | https://www.amazon.es/gp/bestsellers/kitchen/2822691031 |
| 7 | 厨房用品 | Café y té | 2165154031 | https://www.amazon.es/gp/bestsellers/kitchen/2165154031 |
| 8 | 家务用品 | Productos y utensilios de limpieza | 3678025031 | https://www.amazon.es/gp/bestsellers/kitchen/3678025031 |
| 9 | 办公与文具 | Material de oficina | 4352791031 | https://www.amazon.es/gp/bestsellers/office/4352791031 |
| 10 | 兴趣爱好 | Costura y manualidades | 4358016031 | https://www.amazon.es/gp/bestsellers/kitchen/4358016031 |
| 11 | DIY与五金 | Herramientas manuales y eléctricas | 3049288031 | https://www.amazon.es/gp/bestsellers/tools/3049288031 |
| 12 | 玩具 | Juegos de construcción | 1641927031 | https://www.amazon.es/gp/bestsellers/toys/1641927031 |
| 13 | 花园 | Jardinería | 5940346031 | https://www.amazon.es/gp/bestsellers/lawn-garden/5940346031 |
| 14 | 旅行 | Mochilas | 2007986031 | https://www.amazon.es/gp/bestsellers/luggage/2007986031 |
| 15 | 个人护理 | Cuidado de la piel | 6397934031 | https://www.amazon.es/gp/bestsellers/beauty/6397934031 |
| 16 | 食品饮料 | Café, té y bebidas | 6347509031 | https://www.amazon.es/gp/bestsellers/grocery/6347509031 |
| 17 | 数码多媒体 | Informática | 683279031 | https://www.amazon.es/gp/bestsellers/electronics/683279031 |
| 18 | 时尚 | Mujer | 5517558031 | https://www.amazon.es/gp/bestsellers/fashion/5517558031 |
| 19 | 体育用品 | Ejercicio y fitness | 2928495031 | https://www.amazon.es/gp/bestsellers/sports/2928495031 |
| 20 | 宠物用品 | Perros | 13441887031 | https://www.amazon.es/gp/bestsellers/pet-supplies/13441887031 |

可携带的证据摘录位于 `evidence/archive_source_candidates.json`，保留原链接、导航名、父入口、节点、原文件路径、JSON 索引位置及完整文件 SHA256：

- 原审核计划：`F:\AmazonESBestseller\configs\tasks\amazon_es_bestseller_5300_202610_plan.json`，SHA256 `5e01ac8ecdbe0a582539efff68b78db12213a01552f393ae420dee9e6d29dd9e`。
- 原导航树：`F:\AmazonESBestseller\.audit-remote\outputs\amazon_es_bestseller_5000_202610\discovery\category_tree_snapshot.json`，SHA256 `b7c0b9901acccaea154b90581b08c98f40c824e284a02891bbfc4cfd9ee44b02`。
- 证据摘录 SHA256：`b610bedc321163cf63bb2ff8cb2f45ad2eabeb7b0f88f50f03b63ec42f11fece`。

导航树时间如 `2026-10-01T16:46:48` 没有时区。摘录原样保留为 `discovered_at_raw`，UTC 时间和原采集时区为空；不能把这些时间补成 UTC，也不能用今天的回放时间作为历史采集时间。

## 2. 预算、缺口及停止条件

| 控制项 | 上限 / 设置 |
| --- | --- |
| 阶段 | P0 |
| 并行度 | 1，全部串行 |
| 每次受控请求间隔 | 至少 3 秒，保留既有浏览器访问保护 |
| 页面快照 | 20 个已列明的第一页 |
| 受控请求总数 | 45，含页面尝试与 ACP 尝试；失败和中断尝试也占预算 |
| 每日受控请求 | 15，按 Europe/Madrid 本地日期计费 |
| 累计观察记录 | 不超过 1,500；P0 上限，不是必须填满的配额 |
| 规划估算 | 0～1,000 条，按每页至多约 50 条粗估；真实页面可能不同 |
| 已完成真实采集 | 0，未经授权未执行 |

估算绝不能作为 `expected_count` 写入 QA。页面实际没有明确总数时记录 `UNVERIFIED`。无法完成入口或预算不足时报告缺口，不追加来源、不跨研究类目补满。没有继承旧 5,000 SKU 任务的三类目并发权限。

75 个逻辑组合当前全部为 `UNVERIFIED`。Best Sellers 的十五个研究类目已有历史候选映射；另外四种榜单的六十个研究类目组合尚无真实细类入口证据。本轮从实际根页面导航发现其候选链接后保存证据；新地址要另行加入受审范围，不能在本次授权下自动扩展访问。

看到 HTTP 403、HTTP 429、Robot Check、CAPTCHA、Access Denied、明确 Challenge 或其他非正常访问状态，立刻停止后续受控网络请求并保存原始响应、Access Gate 和 checkpoint。`UNKNOWN`、`NETWORK_ERROR` 也停止。不会破解验证码或轮换代理、IP、账号、Cookie。预算耗尽停止，保留已完成页面；跨日恢复仍受同一阶段总上限和授权有效期限制。

## 3. 人工审核与执行步骤

1. 审核以上二十个实际 URL、研究类目映射、三个请求 / 页面预算和 1,500 观察上限；需要缩小范围可删除相应来源，并同步精确授权范围。不要扩大计划而不重新授权。
2. 在独立配置目录复制 `configs/multisource/p0_review_plan.json` 和 `configs/multisource/p0_authorization.template.json`。修改副本，保留待审原件。
3. 获得明确 P0 授权后，填写授权副本的唯一 `approval_id`、人工 `approved_by`、带时区的 `reviewed_at` 和 `expires_at`，逐来源核实 `source_id`、`source_type`、`research_category`、精确 `page_urls`、`allow_acp` 和 `evidence`；将各 `evidence.reviewed` 设为 `true`，全局 `approved=true`。P0 不要求上一阶段门禁。
4. 核查实时 Access Gate 为 NORMAL。初次批准不勾选 `clear_access_gate`；若已有访问限制，等待人工处理，并取得新的独立授权后才可使用该字段。
5. 仅在批准后的计划副本把 `plan.live_enabled` 改为 `true`。使用独立实时测试目录和稳定运行 ID 执行以下命令。模块会在打开浏览器前检查批准状态、精确范围、有效期、预算和串行约束。
6. 每轮查看状态和 QA，保留失效入口、源结构变化、身份冲突及未确认记录。P0 的未知完整性不能视为已通过；P1 必须另行授权并有上一阶段真实质量门禁证据。

以下为 PowerShell 实际 CLI 格式。准备目录和帮助检查属于离线操作；本文件没有替你授权或启动实时命令。

```powershell
$taskWorktree = 'C:\Users\Administrator\.codex\worktrees\multisource-snapshot-v1-1\AmazonESBestseller'
$taskPython = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
Set-Location -LiteralPath $taskWorktree
$env:PYTHONPATH = Join-Path $taskWorktree 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $taskPython -m amazon_es_bestseller.cli multisource --help

$p0ReviewId = 'p0_review_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
$p0ConfigDir = Join-Path $taskWorktree ('outputs\' + $p0ReviewId)
if (Test-Path -LiteralPath $p0ConfigDir) { throw '审核目录已存在，请使用新名称' }
New-Item -ItemType Directory -Path $p0ConfigDir | Out-Null
$p0Plan = Join-Path $p0ConfigDir 'p0_plan.approved.json'
$p0Authorization = Join-Path $p0ConfigDir 'p0_authorization.approved.json'
Copy-Item -LiteralPath 'configs\multisource\p0_review_plan.json' -Destination $p0Plan
Copy-Item -LiteralPath 'configs\multisource\p0_authorization.template.json' -Destination $p0Authorization

# 在获得授权后人工完成上述审核字段；此时两份副本仍默认禁止联网。
$p0RunId = $p0ReviewId
$p0Store = Join-Path $taskWorktree ('outputs\multisource_live_' + $p0ReviewId)

# 只有人工审核并明确授权后的副本才能通过此命令的实时门禁。
& $taskPython -m amazon_es_bestseller.cli multisource run --root $p0Store --plan $p0Plan --authorization $p0Authorization --run-id $p0RunId --phase P0 --live

& $taskPython -m amazon_es_bestseller.cli multisource status --root $p0Store --run-id $p0RunId
& $taskPython -m amazon_es_bestseller.cli multisource pause --root $p0Store --run-id $p0RunId
& $taskPython -m amazon_es_bestseller.cli multisource resume --root $p0Store --run-id $p0RunId --plan $p0Plan --authorization $p0Authorization --live

# 完成冻结快照后：验证来源、运行 QA、导出到新的报告目录。
& $taskPython -m amazon_es_bestseller.cli multisource verify --root $p0Store --run-id $p0RunId
& $taskPython -m amazon_es_bestseller.cli multisource qa --root $p0Store --run-id $p0RunId
$p0ReportDir = Join-Path 'F:\AmazonESBestseller\交付文件\20261013\MultiSourceSnapshotTestV1_1' ('P0报告_' + $p0ReviewId)
& $taskPython -m amazon_es_bestseller.cli multisource report --root $p0Store --run-id $p0RunId --out $p0ReportDir
```

每日请求额度耗尽后，需要到下一个 Europe/Madrid 日期再恢复；授权到期则重新取得授权。跨研究类目默认冷却 1,800 秒，进程显示倒计时并持久化 deadline，重启后继续。暂停与恢复不创建新观察槽位，已经验证并提交的页面优先复用。访问限制后的恢复需要新的人工授权；不能通过改状态文件或删除响应来绕过门禁。

## 4. P0 验收清单

- 五个根入口逐个保存状态、真实标题和导航证据。错误页面不是成功；一次失败不能标成 `UNSUPPORTED`。
- 已接受观察均能追到对应不可变主页面或 ACP 正文，保存请求原 URL、原始商品链接和证据 SHA256。
- 各榜单位置语义单独保存。Movers 的当前 / 之前销售排名和提升百分比不充当实际销量增长；心愿单和送礼榜不充当销量榜。
- 实际卡片数量与页面提供的预期数量核对；排名异常、重复页、遗漏槽位、ACP 未完成、导航不匹配和损坏证据必须进入 QA。
- 卡片 ASIN 与链接 ASIN 不一致的记录隔离；父 ASIN 只表示已证实的变体家族。同父不同子不能自动合并。单独链接 ASIN 及重建规范 URL不能作为两条独立身份证据。
- 未确认 Product ID 的观察不进入已确认唯一商品和正式 5,500 配额统计。保留历史待确认映射和真实缺口。
- 记录已访问页、网络尝试、页面快照、观察、候选实体与已确认实体的不同计数。1500 是累计上限，禁止重复计费恢复页面来填目标。
- P0 只检验入口、结构、基本提取和证据；没有本轮真实采集前，全部结果继续标为待验证。没有真实 14 天采集和比较前不能宣称时间序列验收完成。
