# AmazonESBestseller 4000–5000 SKU 交付闭环 Implementation Plan

> **For agentic workers:** Use the executing-plans skill to implement this plan task-by-task in the current task. Steps use checkbox (`- [ ]`) syntax for tracking. Continuous execution is requested: internal review checkpoints do not require another user message when existing authorization is sufficient. Do not delegate unless separately authorized.

**Goal:** 在现有程序上实现按任务清单执行、可中断恢复、按证据补采补译、可对账交付的 4000–5000 个全局唯一 SKU 工作流。

**Architecture:** 保留现有 Playwright、解析、规范化、翻译、QA 和导出模块。新增轻量任务配置、逐 SKU 原子检查点和薄编排层；榜单原始记录不受商品失败策略影响。源码修复先离线验证，真实访问低频串行，访问限制仍停止。

**Tech Stack:** Python、Playwright、BeautifulSoup/lxml、JSON/JSONL、openpyxl/Pillow、pytest；不引入数据库、网站、代理或分布式系统。

---

## 0. 使用方式、完成边界与授权

本文件是实施规划；其中已落地的参数包括 `collect --pages-per-url`、
`translate-ds --repair-partial`、`export --profile business`，其余示例仍需
以当前 CLI 帮助和测试为准，不把规划文字本身当作功能证据。

把本文件交给 Codex 并要求执行后，Codex 应连续完成“修复 → 功能补全 → 离线验证 → 已获授权的真实验证 → 文档/交付报告”，不在每个子任务结束后询问“是否下一步”。

必须区分两个完成状态：

- **代码交付完成**：所有实现任务、故障恢复测试、5000 条离线容量验证、代码复核、文档同步通过。
- **数据交付完成**：实际任务要求的唯一 ASIN 数量、类目配额、详情、翻译、图片及最终 Excel 对账通过。没有真实数据证据不能把代码通过称为采集完成。

执行边界：

1. 用户的新规模目标覆盖旧 MD 中“仅限 200 SKU 阶段”的阶段性限制；不覆盖证据、身份、价格、访问安全规则。
2. 本次规划不自动调用 DS。执行时，先展示模型/端点、预计待译 SKU/字段数和可获得的成本估算依据，再征询用户；未经授权不得调用，不能管道自动输入 YES 或从历史聊天提取密钥。已明确批准的同一批次范围内续跑可沿用授权，扩大范围须再确认。
3. CAPTCHA、Robot Check、403、429、访问拒绝等触发停止联网；可以继续整理本地已成功数据。不得用自动重启、换 profile、换代理、换账号绕过。
4. 用户要求正式导出两表：`西班牙语选品清单`、`中文选品清单`。实施增加显式业务导出模式；旧三表模式保留作兼容/研究用途，类目规划成为任务输入，不擅自删原有列或覆盖备注。
5. 最终数量由真实任务清单确定，支持 4000–5000，但不替用户虚构类目配额。现有 1000 配置可用于验证，不能把数量改成 5000 就当来源足够。
6. 保留现有未提交修改和 outputs；不 reset、不清空、不改写历史证据。可以按完成模块做本地提交；远端 push、合并和发布按该次执行授权处理。
7. 如果实际清单或 DS 授权尚缺，先完成所有不依赖它们的代码和验证，再报告具体剩余项；不要提前把整个任务停在“请确认方案”。

## 1. 已核实基线

基于 [2026-08-31 审查报告](../../reviews/2026-08-31-scale-readiness.md)：

- 工作区 `E:/amazon_es`，HEAD `ad138b8`，存在未提交修改；实际 CLI 位于根工作区 `src/amazon_es_bestseller/cli.py`。
- 全量离线测试 520 通过、2 跳过；这不等于真实规模闭环通过。
- 固定来源 42 个 URL、7 个组；2520 条榜单记录、2430 个唯一 ASIN；目标 manifest 为 1000 个唯一 ASIN。
- 本轮保存 556 个 HTML，而结构化状态只有 344 条；没有该轮最终 details.json 和 Excel。
- 隔离文件名过滤、缓存刷新、partial 翻译和 HTML 审查内存均有未关闭问题。

## 2. 固定的数据与状态契约

### 2.1 四种数量必须分开

`target_unique` 是要求交付数量；`candidate_unique` 是候选量；`detail_success` 是有效详情数；`deliverable_unique` 是通过业务交付检查的数量。文件数、请求数、尝试次数均不能代替这些指标。

已选任务槽位状态互斥：`pending / running / success / skipped / failed`。访问限制是运行级 `access_stopped`，不得转换为一批虚假的商品失败。断电后的 running 槽位先核对证据/checkpoint，再恢复为待处理，不直接认定成功。

最终状态至少区分：`running / paused / access_stopped / awaiting_translation_approval / partial / failed / complete`。

### 2.2 任务配置

新增 JSON 作为规范任务格式，支持 `selection_mode=exact_asins` 与 `category_quota`：

- exact_asins：严格执行给定 ASIN 集合；不允许自动换商品凑数量。
- category_quota：按已批准来源、组配额和排序选商品；允许同组备用候选补位，保存 replaced_ASIN、原因、replacement_ASIN 及来源。不能跨组偷偷补位。
- 输入提供 ASIN/商品链接时验证二者一致、去重、合法性；若榜单来源必填但缺失，应报清楚错误，不能静默漏项。仅详情任务可没有榜单来源，此时排名和对应来源保持空，不伪造。
- `run_id` 与解析后的输入 hash 绑定；续跑时配置改变必须拒绝或显式新建任务，不混用缓存统计。
- 任务中的 URL、ASIN、路径都校验；不把网页内容当指令，不让非法 ASIN 进入文件路径。

用于首个迁移验证的实际配置（新增文件 `configs/tasks/scale_1000_recovery.json`）：

```json
{
  "schema_version": 1,
  "run_id": "scale_1000_recovery",
  "selection_mode": "exact_asins",
  "target_unique": 1000,
  "manifest_file": "outputs/scale_1000/manifest_1000.json",
  "rankings_file": "outputs/scale_1000/rankings_pages_1_2.json",
  "legacy_cache_dir": "outputs/scale_1000/detail_full",
  "run_dir": "outputs/scale_1000/recovery_v2",
  "headful": true,
  "request_delay_seconds": 3,
  "request_timeout_seconds": 45,
  "batch_size": 50,
  "export_profile": "business",
  "translation_policy": "ask",
  "embed_original_images": true
}
```

数值是本计划建议的运行默认值，不是 Amazon 安全访问保证。批次分段不表示自动冷却后绕过访问停止；真正的停止状态不能自动解除。

### 2.3 运行目录

```text
run_dir/
  task.json                       # 冻结任务和输入 hash
  run_manifest.json               # 阶段、计数、停止原因
  events.jsonl                    # 追加事件；不是商品真相来源
  items/ASIN.json                 # 每 SKU 原子 checkpoint：状态、最新有效详情、证据引用
  raw/ASIN/attempt_id.html        # 每次尝试独立保存
  raw/ASIN/attempt_id.meta.json   # requested/final URL、HTTP、时间、hash
  rankings/                      # 榜单原始证据/记录，不按详情失败删除
  quarantine/                    # 隔离证据引用和原因，不靠文件名决定永久黑名单
  images/                        # ASIN→URL→原图文件及 hash
  reports/                       # 对账、字段 QA、资源统计、故障报告
  exports/                       # 西语/中文工作簿
```

`items/ASIN.json` 是每 SKU 恢复权威；原始 HTML 是证据权威；run_manifest 是可重建汇总。旧的 `details.json` 继续作为兼容快照输出，在批次结束/暂停/完成时由 checkpoints 重建，不为每个 SKU 重写全部数千条数据。

## 3. 代码文件落地方向

以下“新增”文件均为计划名称，实施时创建；避免把所有逻辑塞进 CLI。

| 文件（相对 E:/amazon_es） | 类型 | 责任 |
|---|---|---|
| `src/amazon_es_bestseller/cli.py` | 修复 | 参数、委托、清楚错误；移除按 quarantine 文件名删榜单逻辑 |
| `collection/detail.py` | 修复 | 页面证据/解析、每项结果事件；分离普通商品失败与访问停止 |
| `collection/planning.py` | 修复 | 显式缓存动作、完整任务 ASIN 集、源缺失与刷新规则 |
| `access/browser.py` | 修复 | 可见模式、资源清理、超时和保留原始异常 |
| `access/detector.py` | 保留并局部修复 | 访问限制仍停止，不削弱识别 |
| `run_manifest.py` | 接入 | 运行阶段、计数、当前项、心跳和退出原因 |
| `task_config.py` | 新增 | 规范任务配置、manifest/CSV/XLSX 输入和校验 |
| `checkpoints.py` | 新增 | 原子 JSON、逐 SKU checkpoint、旧缓存迁移 |
| `collection/outcomes.py` | 新增 | 结果类型、状态转换、隔离复核规则 |
| `collection/discovery.py` | 新增 | 真实类目链接/分页发现及候选覆盖统计 |
| `collection/ranking.py`、`collection/quota.py` | 完善 | 页级恢复、分页指纹、全局唯一及备用候选 |
| `runner.py` | 新增薄层 | 调用现有阶段、锁、暂停/恢复、进度；不重新实现解析器 |
| `translation/ds.py` | 修复 | partial 补译、hash/schema、原子缓存和请求汇总 |
| `qa/repair_plan.py` | 新增 | 按来源证据生成字段补采/补译清单 |
| `images.py` | 新增 | 图片下载、原图缓存、校验和失败记录 |
| `qa/field_closure.py` | 修复 | 逐页证据处理、多榜单上下文、导出后核对 |
| `qa/reconciliation.py` | 新增 | 任务集合、唯一数、配额及交付集合对账 |
| `export/excel.py` | 完善 | 业务两表/兼容三表、原图、备注、原子交付 |
| `scripts/benchmark_scale.py` | 新增 | 明确标记为合成的离线容量/恢复验证，不连接 Amazon/DS |

## 4. 执行任务

### Task 1 — 冻结工作基线、整理已知错误测试

Files: `AGENTS.md`、`README.md`、`docs/CURRENT_STATE.md`、`tests/test_cli_smoke.py`、`tests/test_access_stop.py`、`tests/test_planning.py`。

- [ ] 检查 git status、worktree、实际 Python 导入路径；保存现有改动清单。若要隔离开发，先安全承接当前未提交补丁，不能从旧提交创建工作区后漏掉修复。
- [ ] 跑 `python -m pytest -o addopts='' -q -rs`，记录当前基线。读取已有 fixture，新增真实故障的最小脱敏样本，不能把数 MB 完整页面无差别提交。
- [ ] 修正错误测试方向：quarantine 中有 A，但 rankings-only 仍必须包含 A 的原始榜单记录；详情计划才允许隔离 A。先观察此断言在旧代码失败。
- [ ] 记录普通无效页 A 后正常页 B、真正 CAPTCHA 后不得请求 B、缺来源输入不能静默消失三组预期。
- [ ] 在 MD 中声明此次规模工程阶段，保留历史阶段记录和安全约束；暂不宣称规模完成。

验收命令：`python -m pytest tests/test_cli_smoke.py tests/test_access_stop.py tests/test_planning.py -q`；新增失败测试必须对应真实缺陷，而不是为了改变测试数。

### Task 2 — 逐 SKU 原子检查点与中断保存（R03）

Files: 新增 `checkpoints.py`、`tests/test_checkpoints.py`；修改 `collection/detail.py`、`collection/planning.py`、`cli.py`。

- [ ] 在新测试中模拟 A 成功/B 触发挑战：函数即使退出，A checkpoint 必须可读，恢复不能请求 A。
- [ ] 测试写到临时文件后、replace 前故障：旧的有效 checkpoint 必须完整存在。坏 JSON 必须报损坏并走显式恢复，不能静默当空状态。
- [ ] 实现 `atomic_write_json(path, payload)`、`save_item(run_dir, item)`、`load_items(run_dir)`，在每次完成页面分类/解析后保存该 ASIN 的状态与有效结果。
- [ ] 使用同目录唯一临时文件、flush、fsync、`os.replace`。以一次 JSON 原子替换保存状态+商品详情+证据引用；刷新失败不清除已有有效详情。
- [ ] 在正常结束、Ctrl+C、AccessStopError、可处理异常时重建汇总/兼容快照；强制终止时恢复最多丢失正在处理的一项，不丢此前成功项。

参考原子写入核心（临时路径须用 tempfile 在目标目录创建）：

```python
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, ensure_ascii=False)
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temp_path, destination)
```

验收：`python -m pytest tests/test_checkpoints.py tests/test_detail_collection.py -q`；保存不依赖整个 ASIN 列表正常返回。每个 checkpoint 的 `asin`、`status`、`attempt_count`、`latest_valid_detail`、`evidence_ref` 必须完整。

### Task 3 — 异常分类与任务隔离（R04）

Files: 新增 `collection/outcomes.py`、`tests/test_collection_outcomes.py`；修改 `detail.py`、`cli.py`、`detector.py` 的调用边界。

- [ ] 定义原因码：`ACCESS_CHALLENGE / ACCESS_BLOCKED / RATE_LIMITED / NETWORK_TIMEOUT / INVALID_PRODUCT_PAGE / ASIN_CONFLICT / PARSER_ERROR / SOURCE_MISSING / INTERNAL_ERROR`。
- [ ] 移除 CLI 的 quarantine 文件名过滤；历史 quarantine 只作为迁移输入，经过分类后写状态，不能自动永久排除全部同名 ASIN。
- [ ] 普通无效商品页、已确认身份冲突记隔离并继续下一项；不合并不同 ASIN 数据、不推断 parent 关系。`SOURCE_MISSING` 仅用于有正常源证据证明缺字段，不把解析失败写成源缺失。
- [ ] CAPTCHA/403/429/明确拒绝或不能排除访问限制的页面停止联网，并保存运行级原因；不得依靠跳过当前 ASIN继续访问下一页。普通网络超时记录失败，单轮不立即重复请求，按预算另行续跑。
- [ ] 通用编程错误 INTERNAL_ERROR 不得被大 `except Exception` 吞为普通 SKU 失败；停止并保留 traceback。测试 A 无效/B 正常、A 挑战/B 未请求、恢复已隔离项、重试预算耗尽。

核心预期：

```python
assert [row["asin"] for row in raw_rankings] == ["B084ZNZV3S", "B008YETL18"]
assert outcomes["B084ZNZV3S"]["reason"] == "INVALID_PRODUCT_PAGE"
assert outcomes["B008YETL18"]["status"] == "success"
# 在 CAPTCHA 场景中，下一 ASIN 的网络调用次数必须为 0。
```

验收：`python -m pytest tests/test_collection_outcomes.py tests/test_access_stop.py tests/test_cli_smoke.py -q`。

### Task 4 — 缓存动作、schema 升级与时间语义（R05）

Files: `collection/planning.py`、`collection/detail.py`、`checkpoints.py`、`cli.py`；测试 `tests/test_cache_actions.py`。

- [ ] 规划输出保留动作，不再在 `collect_asins` 后丢失动作：`reuse / recover / reparse / fetch_new / refresh / repair_fetch / skip`。
- [ ] reuse 直接读当前 checkpoint；recover 仅解析有 HTML、无 checkpoint 的项；reparse 只升级旧 schema，保存 `parsed_at`，不改真实 `fetched_at`。
- [ ] refresh/repair_fetch 明确发请求，即使旧 HTML 存在；新尝试独立保存，旧有效详情不被无效页面覆盖。无旧记录时不得传 None 导致重解析所有文件。
- [ ] 缺评分/原价/日期等若已审计为 SOURCE_MISSING，不能因空值每轮重采；只有证据发生变化或已到明确刷新条件才重采。
- [ ] fixture 验证：100 条当前版本记录无 HTML 解析/网络调用；1 条旧版本仅重解析该条；1 条过期 refresh 实际请求 1 次；2 条中断遗留 HTML 仅恢复这 2 条。无 timestamp 的历史证据保留 unknown 或注明文件时间近似值，不写成今天采集。

验收：`python -m pytest tests/test_cache_actions.py tests/test_planning.py tests/test_detail_parser.py -q`。

### Task 5 — 任务输入与范围校验（R01）

Files: 新增 `task_config.py`、`tests/test_task_config.py`、`configs/tasks/scale_1000_recovery.json`；修改 `cli.py`。

- [ ] 实现 `load_task(path)`，按第 2.2 节验证 schema、run_id、selection_mode、target_unique、来源及输出目录；输出错误要定位到行/字段。
- [ ] 从旧 manifest 的 records 导入；CSV/XLSX 表头映射限定为 `ASIN/asin`、`商品链接/product_url`、`category_group`、`ranking_source_url`、`quota`、`enabled`，不猜任意列含义，不执行 Excel 公式。
- [ ] exact_asins 中重复/非法 ASIN、URL 与 ASIN冲突都出输入报告；缺失源但要求排名的行报缺来源错误。详情-only 模式允许无榜单来源并保留空排名。
- [ ] 对 manifest 不再只取非空交集：报告 requested/matched/missing/duplicate，缺项不能静默通过。任务 config hash 与 checkpoints 关联。
- [ ] 新增 `plan-task --task FILE`，纯离线输出计划与输入错误，不启动浏览器；解析源和目的路径必须稳定，不依赖当前 shell 临时环境。

验收：`python -m pytest tests/test_task_config.py tests/test_cli.py -q`。离线示例 `python -m amazon_es_bestseller.cli plan-task --task configs/tasks/scale_1000_recovery.json` 必须显示 target=1000，区分有效缓存、待恢复 HTML、隔离及待请求项。

### Task 6 — 薄编排、运行锁、可见进度（R06）

Files: 新增 `runner.py`、`tests/test_runner.py`；修改 `cli.py`、`run_manifest.py`、`access/browser.py`。

- [ ] 新增 `run-task --task FILE --resume`、`status-task --run-dir DIR`、`pause-task --run-dir DIR`；不破坏现有子命令。run-task 只协调现有模块，不复制解析或 QA 规则。
- [ ] 先完成离线输入检查/必要恢复，再启动 BrowserSession；零网络任务不启动浏览器。默认使用普通 Playwright，headful 来源于任务配置，不再自动复用日常 Chrome profile。
- [ ] 每项完成及每阶段切换写事件、更新 run_manifest；本地 CPU 处理或等待页面时也有心跳。显示 `stage/current_asin/success/failed/skipped/pending/elapsed`，不能仅看文件数说成功。
- [ ] 每个 run_dir 只允许一个 runner：锁记录 run_id、PID、启动标识；不能仅凭旧 PID 存在判定同一进程，也不能无条件删锁。暂停请求只在安全边界完成当前项后保存退出。
- [ ] 资源清理使用 try/finally；browser.close 的次生异常不得覆盖原始采集异常。运行级退出码约定：0=完整完成，2=输入/配置错误，3=访问停止，4=部分完成，5=内部故障，6=暂停/等待授权；旧命令兼容行为单独记录。

验收：`python -m pytest tests/test_runner.py tests/test_cli_smoke.py tests/test_access_detector.py -q`。假浏览器里验证双启动拒绝、暂停恢复、关闭异常、心跳以及无需网络时不启动 BrowserSession。

### Task 7 — 类目来源、分页与候选覆盖（R02）

Files: 新增 `collection/discovery.py`、`tests/test_discovery.py`；修改 `ranking.py`、`quota.py`、`tests/test_ranking_parser.py`、`tests/test_quota.py`。

- [ ] 从保存的真实榜单导航解析父子节点、Browse Node 和分页链接；未观察到的层级保持空，不通过标题造分类。
- [ ] 对每个榜单页保存实际 URL、最终 URL、页面指纹、rank 范围、ASIN 集、时间和访问状态；区分页 1/2，不把 query 去噪规则当作分页去重规则。
- [ ] 重复页、空页、跳转到别的部门分别报告；部门匹配规则覆盖已配置各组，而非只校验 car。保留真实跨榜单出现，不按 ASIN 删榜单记录。
- [ ] 新增 `discover-task --task FILE`，真实发现只访问批准的大类及其导航范围；维护页级 checkpoint、唯一候选数和组配额缺口，触发访问限制即停止。
- [ ] 为 4000–5000 配置准备足够真实候选；预算建议按目标多准备 20% 候选，但该比例不是凑数保证。只要有组不满足配额，就输出 shortfall，不能宣布任务 ready。

验收：`python -m pytest tests/test_discovery.py tests/test_ranking_parser.py tests/test_quota.py -q`。以 fixture 覆盖分页重复、跨组同 ASIN、源部门不符、候选不足；真实扩源结果记录在任务目录，不提交用户原始数据。

### Task 8 — 字段证据与定向补采（R07 上游）

Files: 新增 `qa/repair_plan.py`、`tests/test_repair_plan.py`；修改 `qa/field_closure.py`、`pipeline.py`，有证据时局部修复 `detail.py`/`normalization/specification.py`。

- [ ] 每个字段区分 SOURCE_MISSING、PARSER_MISSED、MAPPING_MISSED、DERIVED_MISSING、EVIDENCE_UNAVAILABLE、SOURCE_CONFLICT。字段之间有依赖：折扣缺失先核实两个价格，不能直接抓折扣填入。
- [ ] `plan-repair --run-dir DIR` 输出离线重解析、规范化修复、待补采和待翻译四类动作；没有源证据时不能直接判为 SOURCE_MISSING。
- [ ] 优先旧 HTML 补解析/映射，只有缓存证据不足或明确要求刷新才进入有预算的 repair_fetch；同一修复清单有最大尝试次数，耗尽后输出未解决状态。
- [ ] 保留完整动态属性、原始西语和卖点；规格仅为派生摘要。新增字段标签不在字典中也保留 raw。ASIN 冲突只保存对照证据，不自动把 A 数据写到 B。
- [ ] 每个真实 parser 修复都由 fixture 复现；A+ 目前不作为全量硬性承诺，若实际需求列入再增加明确 raw 区块和验证，不能在 MD 先宣称已覆盖。

验收：`python -m pytest tests/test_repair_plan.py tests/test_field_closure.py tests/test_detail_parser.py tests/test_specification.py -q`。证明 SOURCE_MISSING 不触发无限补采，存在源却映射丢失的字段能定位到具体层。

### Task 9 — DS 部分补译与授权边界（R07 下游）

Files: `translation/ds.py`、`cli.py`、`runner.py`、`tests/test_ds_translation.py`、`tests/test_pipeline.py`。

- [ ] 同 hash/schema 的 success 复用；partial 在显式 repair 模式计算 missing_fields，并只请求缺失字段。旧的已成功译文不被空响应覆盖。
- [ ] 请求必须保留相应源上下文，返回字段只允许当前批准字段；源 hash 改变时不得合并旧源译文。遇到西语残留、空对象或类型错误，字段状态不得写 success。
- [ ] 在 CLI 增加 `translate-ds --repair-partial`；run-task 进入 awaiting_translation_approval 前输出真正需要调用的 ASIN 数、字段数、缓存命中数；已有缓存的纯离线复用无需新 API 调用。
- [ ] 未授权时不构造发请求流程；授权、端点和模型只记录非敏感元数据。日志、任务 JSON、git 不保存 API key。401/403 等非重试错误停止翻译阶段，普通临时失败有限重试并保留结果。
- [ ] 保持每项原子缓存、partial/failed/source_missing 分开统计；减小逐项整份缓存重写的成本，使用逐 SKU 存储后再生成兼容汇总。

验收：`python -m pytest tests/test_ds_translation.py tests/test_pipeline.py tests/test_runner.py -q`。全部使用 fake transport，测试调用 payload 只含缺字段、成功字段保持、hash 变化失效、无授权零请求。

### Task 10 — 原图下载、缓存和身份映射（R08 图片）

Files: 新增 `images.py`、`tests/test_images.py`；修改 `cli.py`、`export/excel.py`。

- [ ] 实现 `download-images --run-dir DIR`，按 `ASIN→image_url→文件→hash` 建立清单，原图下载单独于详情导航，不重复访问商品页。
- [ ] 限制为批准的 Amazon 图片 CDN；校验重定向目标、状态码、MIME、实际图像可解码，拒绝 HTML/损坏图片、路径穿越及超大文件。异常输出状态，不把下载失败当有效图片。
- [ ] 保留原始图片字节，不为省内存重压缩；Excel 内只改显示宽高。源 URL 变更时重新验证，已存在且 hash 有效的原图不重下。
- [ ] 超时/服务器临时失败最多有限次数，拒绝/限流时停止图片阶段，不通过换源绕过；失败记录保留以便明确重新执行。
- [ ] 用本地 fixture/fake HTTP 测试身份错配、损坏文件、内容为 HTML、正常缓存命中和 URL 更新。

验收：`python -m pytest tests/test_images.py tests/test_excel_export.py -q`；没有原图时明确 image_missing，正式图文交付不能把它算作图片完成。

### Task 11 — 流式字段审查与最终任务对账（R08/R09）

Files: `qa/field_closure.py`；新增 `qa/reconciliation.py`、`tests/test_reconciliation.py`、`tests/test_field_closure_streaming.py`。

- [ ] 将 `_html_by_asin` 的 ASIN→完整 HTML 全量字典改成证据路径索引；按商品读取并释放，缓存有明确大小上限。不要每个字段重新读同一页。
- [ ] 保留同 ASIN 的多条榜单来源，不用单个字典项覆盖全部排名上下文。源文件不存在、源不存在字段和未执行解析分开报告。
- [ ] 实现 `reconcile_task(task, items, products, translations, images, workbook_asins)`：列出 missing/extra/duplicate/conflict 及每组 requested/success/shortfall；最终集合以批准目标清单为准。
- [ ] exact_asins 无自动补位；category_quota 的补位须先写追加选择记录，再执行，并在交付 manifest 中标记淘汰及替代关系。
- [ ] 数据仍有 unresolved 身份错误、P0/P1 parser/映射/翻译问题或数量不足，则运行只能 partial/failed。SOURCE_MISSING 可允许为空，但解析/翻译错误不能换个标签放行。

核心验收示例：

```python
target_asins = {"B008YETL18", "B078C6QR1C"}
delivered_asins = {"B008YETL18"}
assert target_asins - delivered_asins == {"B078C6QR1C"}
# reconcile 输出 complete=False，missing 中包含 B078C6QR1C。
```

验收：`python -m pytest tests/test_reconciliation.py tests/test_field_closure_streaming.py tests/test_field_closure.py -q`。全是干净产品但少一个目标，也必须判未完成。

### Task 12 — 正式两表、兼容三表与导出后核验（R08/R09）

Files: `export/excel.py`、`cli.py`、`qa/field_closure.py`、`tests/test_excel_export.py`、`tests/test_cli.py`。

- [ ] 增加显式 `--export-profile business|research`：business 为两张产品表；research 保留三表。旧 export 默认兼容 research；本次规模任务配置明确使用 business。两表字段顺序不随意重排。
- [ ] ES 不嵌图、ZH 嵌原图，序号和 ASIN 同源生成；所有数值来自同一 canonical 记录。按 ASIN 读取旧备注，备注冲突报告而非默默覆盖。
- [ ] 导出只从通过对账的任务范围读取产品；不把 2430 候选表直接作为 1000 目标表导出。预检包括任务数、源证据和翻译状态。
- [ ] 先输出临时工作簿，再读回核对表名、完整行数、ASIN集合/顺序、关键字段、中文图片行和备注；检查通过后原子发布为正式文件。失败保留报告，不覆盖已有正式交付。
- [ ] 图片缺失、中文残留和数量不足必须在正式验收报告可见；`--force` 只能产出明确标记的研究草稿，不能令 run_manifest.complete=True。

验收：`python -m pytest tests/test_excel_export.py tests/test_cli.py tests/test_reconciliation.py -q`；二表/三表、缺图、旧备注、少条、错 ASIN 和强制草稿均有覆盖。

### Task 13 — 恢复既有数据、离线压力测试与真实规模验证（R10）

Files: 新增 `scripts/benchmark_scale.py`、`tests/test_runner_integration.py`；产物放 task 的 reports 目录。

- [ ] 离线迁移旧 556 个 HTML：使用已知正常 checkpoint，逐一检查尚未入状态的证据；记录有效/异常/未知数量和时间来源，原目录不删除。不要把 556 直接写成成功数。
- [ ] 复用真实小 fixture 和合成 ASIN，构建 5000 条**明确标记 synthetic**的离线任务；连续运行、在第 1/50/4999 项模拟中断并恢复，验证集合、原子保存、请求替身计数和输出一致。
- [ ] 使用具有代表性大小的独立 HTML/图片测内存，避免同一小字节串或单张图去重让压力结果虚低。记录 CPU、峰值 RSS、磁盘量、QA/导出耗时；建议初始预算 QA 峰值≤1 GiB、导出≤2 GiB，达不到时先修工程问题或报告实测资源需求，不私自压图/拆表。
- [ ] 代码与离线压力通过后，已获真实采集授权的执行才进行：先 20–50 个跨类目 SKU 验证正确性，之后续接 1000，再 3000，再任务目标 4000–5000。各规模复用已验收记录，不每一阶段重新全量抓取。DIY 样本覆盖电动工具、耗材、配件等不同结构。
- [ ] 遇访问门禁保存 partial 报告和已完成数据；不能承诺一次外部请求全程无阻。只有最终真实任务验收成功才宣布数据交付完成。

验收命令（脚本参数由本任务实现）：

```powershell
python -m pytest tests/test_runner_integration.py -q
python scripts/benchmark_scale.py --count 5000 --offline --out-dir outputs/benchmarks/scale_5000
python -m pytest -o addopts='' -q -rs
python -m compileall -q src
git diff --check
```

压力数据和真实数据分别输出，不混入任务成果，不以合成数据冒充 Amazon 商品。

### Task 14 — MD 同步、复核、版本交付（R11）

Files: `AGENTS.md`、`README.md`、`docs/CURRENT_STATE.md`、`docs/ROADMAP.md`、`docs/ARCHITECTURE.md`、`docs/DATA_MODEL.md`、`docs/QA_RULES.md`、`task_plan.md`、`progress.md`、`.github/workflows/ci.yml`。

- [ ] 每完成一个模块就更新相应说明，不留到最后凭记忆补写；状态文档保留“代码通过/真实数据未完成”的区别。
- [ ] 文档明确：新命令、配置 schema、状态和退出码、权限/DS 授权、访问停止、原图、业务两表及兼容三表、恢复/刷新区别和任务对账。
- [ ] 执行全量测试、真实故障回归、5000 离线压力及依赖安装/入口检查；CI 默认不联网，压力套件可单独运行避免日常 CI 资源失控。
- [ ] 以 HIGH/MEDIUM/LOW/INFO 复核所有改动；HIGH 必须关闭，影响本目标的 MEDIUM 修复或清楚列出剩余项，不能声称未解决风险已经消失。
- [ ] 输出最终代码摘要、测试报告、已验证规模、实际数据数量、未解决项、启动命令、恢复命令和 commit 信息；本地提交只包含相关代码/fixture/MD，不含输出数据、浏览器 profile、图片或密钥。未经本次授权不自动推送/合并远端。

## 5. 完成验收矩阵

| 场景 | 必须看到的结果 |
|---|---|
| 正常页 A、无效页 B、正常页 C | A/C 保存成功，B 有隔离原因，原榜单 A/B/C 都保留 |
| 正常页 A、挑战页 B、正常页 C | A checkpoint 留存；B 触发 access_stopped；C 零网络请求 |
| 同任务恢复 | 已确认最新有效项不重复访问；待恢复 HTML 有可见进度 |
| 状态写入中断/损坏 | 已提交 checkpoint 可恢复；损坏显式报告，不能变空成功 |
| refresh 过期页面 | 实际请求并保存新证据版本；失败不抹掉旧有效详情 |
| 一个字段源不存在 | 合法空值且原因明确，不无限重采 |
| partial 中文缓存 | 授权后只补缺字段；旧成功字段保持、hash 不同不混用 |
| 5000 目标只交付 4999 | partial + 明确 missing，不能 complete |
| 图片/中文/ASIN 错配 | 正式导出验收失败，保留上一份正式文件 |
| 整体浏览器等待/本地解析 | 显示实际阶段与耗时，不用空白窗口推断网络故障 |

## 6. 交付物与推进原则

代码交付：14 个任务的实现、对应回归测试、七份核心 MD 同步、离线压力报告和代码审查结果。

数据交付：任务 manifest、原始榜单、逐 SKU checkpoint/详情、隔离及失败清单、翻译缓存、图片清单、QA/字段闭环、最终集合对账、正式双语两表 Excel。实际未完成时同样交付部分数据和准确缺口，不伪造完整结果。

推荐先完成 Task 1–6 和 Task 11 的恢复/对账核心，再接 Task 7–10、Task 12，最后 Task 13–14。期间无需等待用户逐条确认；DS 与外部访问限制按第 0 节处理。

不做：全站重写、数据库、网站、自动验证码处理、代理轮换、账户轮换、盲目双线程、在源数据缺失时推测补数。性能优先来自减少重复读取和重复请求。

## 7. 可直接交给 Codex 的执行指令

```text
请执行 E:/amazon_es/docs/superpowers/plans/2026-08-31-5000sku-delivery-closure.md。
在现有 AmazonESBestseller 程序上连续完成所有代码修复、功能补全、离线测试、规模压力验证、代码复核和 MD 同步，不在每个小步骤后等待我说下一步。
保留当前未提交修改、已采集数据和原始证据；先检查实际工作区，不回滚、不重写全部程序。
优先解决逐 SKU 保存、异常隔离、缓存刷新、进度和任务数量对账，再完成字段补采、DS 部分补译、原图和正式两表导出。
真实采集按我的任务清单及已授权范围执行，严格串行低频；普通无效 SKU 按计划记录并跳过，验证码/403/429/访问拒绝必须停止访问，不能绕过。
调用 DS API 前必须征询我；没有授权时先完成其他工作，不将翻译缺口伪报成功。最终类目配额和目标数量取真实任务清单，不自行编造。
以任务清单、真实数据、翻译和 Excel 的 ASIN 集合对账作为数据完成标准；仅代码或离线测试通过时，明确标记为代码完成、数据仍待验收。
结束时给出完成清单、未解决项、实际测试结果、数据覆盖/缺口、可直接运行的命令和提交信息。不得只因时间长或程序能启动就宣告完成。
```
