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

The repository keeps several git worktrees under `.worktrees/`, and an editable
install records whichever tree it was installed from. Installing from a worktree
leaves `amazon-es` running that branch's code while `pytest` runs the checked-out
tree, which silently hides fixes. Confirm the install points at the intended tree:

```powershell
python -c "import amazon_es_bestseller.cli as m; print(m.__file__)"
```

Re-run `python -m pip install -e ".[test]"` from the intended tree if the path is wrong.

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

需要复用本机 Chrome 登录会话时，可显式传入 Chrome 的用户配置目录。程序只让
Playwright 使用该持久化上下文，不读取或导出 Cookie、本地存储等会话内容；运行前
应关闭正在使用同一配置目录的 Chrome 窗口，避免配置锁冲突：

```powershell
python -m amazon_es_bestseller.cli collect `
  --rankings-file outputs/scale_1000/rankings_batch_001.json `
  --manifest outputs/scale_1000/manifest_profile5.json `
  --out-dir outputs/scale_1000/detail_profile5 `
  --profile-dir "$env:LOCALAPPDATA\Google\Chrome\User Data" `
  --headful
```

长任务会在 `checkpoints/<ASIN>.json` 逐条记录成功、失败或门禁停止结果，
可安全中断后续跑。隔离目录只影响详情计划，不会修改原始榜单 JSON。
需要外部监控时可加 `collect --progress outputs/run_progress.json`，该文件会在
每个 ASIN 终态后更新。

`collect` 启动时会先检查 Amazon.es 顶部的实际配送目的地。若目的地不是
西班牙，程序会通过 Amazon 的正常“选择配送地点”弹窗设置西班牙邮编，默认
使用马德里 `28001`；设置后会再次核验顶部目的地，无法确认时停止采集。可按
任务所在城市覆盖邮编，例如 `collect --postal-code 08001 ...`。该检查只使用
页面公开控件，不读取或导出 Cookie，也不绕过验证码或其他访问限制。
同一浏览器再次进入采集批次时，会重新读取当前页面的配送目的地；若地址已改变，
会重新设置并核验，不会仅凭上一次成功结果放行。已确认是西班牙的地址保持不变。

若详情或榜单页遇到 Amazon 挑战页，程序默认立即停止受限浏览器的采集并保存证据。
显式使用 `--headful --manual-assist` 时，可保留页面等待人工处理，程序不会自动
识别、输入或绕过验证码。

审核过的 `task-collect` 并行任务默认采用 `scheduler.access_stop_scope="worker"`：
一个浏览器受限时，只停用它所在的工作槽，其他正常浏览器按原计划及冷却间隔继续。
不会补开浏览器替代受限工作槽，也不会把它尚未完成的详情转交其他浏览器重试。
需要全体停止时，计划可使用 `access_stop_scope="all"`，或显式传入
`--access-stop-scope all`。该设置不会增加任务原有的并发数。

受限浏览器已完成的详情、原始 HTML、checkpoint 和待处理 ASIN 会被保存。
只要存在受限工作槽，本次任务就不会被报告为完整交付；其他工作槽继续时状态为
`RUNNING_WITH_ACCESS_STOP`，全部结束后为 `ACCESS_STOP`。人工处理后需显式传入
`--resume-access-stopped` 才能重新尝试受限任务。历史缓存中的验证码页仍按原规则
阻止续跑，本轮没有自动清理或放行这类缓存。

使用独立工作树时，可通过 `python scripts/amazon_es.py --runtime-info` 核对实际
模块路径，再使用 `python scripts/amazon_es.py ...` 运行该工作树的 CLI。这个入口
明确使用同一工作树的 `src`，不会误用 Python 环境里安装的另一份项目，也不会
自动安装、更新或部署到生产目录。

`detail-run` 只有取到实际详情记录才报告 `REUSED` 或 `ALREADY_SUCCESS`。
缺少记录/证据的复用项保持 `PENDING`，汇总为 `INCOMPLETE`；可用保存页面会先
离线重解析后合并到输出。显式重抓会保留旧页面到 `cache_history`；历史验证码
缓存仍先触发原门禁。损坏缓存单独记录失败并保留原文件，其他商品可以继续。
`execution_status=COMPLETE` 仅表示详情计划动作完成，不代表生产验收通过。

详情解析覆盖当前商品的月购、现价、专属商品描述及全部已识别参数区，避免误取
推荐/赞助商品和隐藏内容；未知参数标签、重复标签的不同事实及原始西语仍保留。
保存页没有展示的字段保持空白；A+ 不冒充商品描述，多选数量不猜成单一件数。

详情完成后可用 `download-images --products products.json --out-dir images
--report image_download.json` 按 ASIN 串行补齐原图缓存。

正式业务导出如只需要两张产品表，可使用 `export --profile business`；默认
`research` 仍输出 `类目规划`、西语清单、中文清单三张表。

4500 SKU 任务配置位于 `configs/amazon_es_4500sku_categories.json`（15 个类目×300）。

阶段性 1500 SKU 配置位于 `configs/amazon_es_1500sku_categories.json`（15 个类目×100），用于先验证多类目候选覆盖和详情闭环，再扩展到 4500。

可在不联网的情况下检查候选覆盖：

```powershell
python scripts/assess_task_coverage.py --task configs/amazon_es_4500sku_categories.json `
  --manifest outputs/scale_1000/manifest_1000.json --out outputs/coverage_4500.json
```

该检查会去重 ASIN、识别跨类目冲突，并按每个类目的有效唯一 ASIN 计算缺口。
先用榜单分页生成候选并检查每类唯一 ASIN 缺口，再启动详情采集；不能仅凭配额文件宣布完成。

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

审查开始时会立即打印 SKU 数量和 HTML 证据是否启用；由于大型详情 HTML
需要离线解析，字段明细和最终统计会在整批处理完成后一次性写出。

`export` applies the same closure audit as a gate. `--details` and `--rankings`
default to `outputs/details.json` and `outputs/rankings.json`, so the gate runs
without extra flags; when no evidence is reachable, export states that the
field-closure gate did not run instead of skipping it silently.

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

## Main Consolidation V1

The consolidation inventory and module boundaries are tracked in
`docs/consolidation/`. The formal detail path remains `detail-plan` →
`detail-run`; the Translation V2 staged adapter is exposed as
`translation-production` and writes hash-bound candidates under its explicit
run directory. Its default provider is deterministic `fake`; Qwen translation
requires explicit configuration and verified pricing evidence. See
`docs/ARCHITECTURE.md` for the module handoff and `docs/consolidation/INTEGRATION_PLAN.md`
for current integration and acceptance status.

### Main Consolidation V2 candidate snapshots

`candidate-snapshot` is an offline-only replay command for SHA-256-verified
Amazon.es ranking and Customer Favorites HTML. It writes append-only candidate
groups and per-source observations under the requested output directory. The
snapshot remains `UNVERIFIED`; observed ASINs are hints, and candidate URLs do
not establish buyable product identity or authorize `detail-plan`.

```powershell
amazon-es --offline candidate-snapshot --manifest saved-pages.json `
  --out-dir outputs/candidate-evidence
```

The formal detail flow remains `detail-plan` → `detail-run`, keyed by confirmed
ASIN. When an authoritative ranking record carries candidate group and
observation IDs, the detail plan and per-run manifest retain them as provenance;
they do not alter canonical product identity or detail state. Multi-lane
Detail5500 and V4 live runners remain experimental. For individually confirmed
candidates from saved evidence, use `candidate-identity-verify`; it emits a
verified selection without claiming that the entire ranking snapshot is
authoritative or complete:

```powershell
amazon-es candidate-identity-verify --snapshot-dir outputs/candidate_snapshots/<snapshot-id> `
  --detail-html-dir outputs/details/html --candidate-id <candidate-id> `
  --out-dir outputs/candidate_selections/<selection-id>
amazon-es --offline detail-plan --snapshot outputs/candidate_selections/<selection-id> `
  --out-dir outputs/detail_plans/<plan-id>
```

`source-evidence-audit --run-dir <detail-run> --rankings <rankings.json> --out <report.json>`
grades actual request evidence, hash-verified saved HTML and insufficient
evidence, then runs the selected checks from the mainline Quality Gate. It does
not manufacture Source Audit receipts or a formal Source Audit PASS. The legacy
receipt/repair workflow and multi-lane runners remain experimental. Offline
cross-module acceptance is documented in `docs/consolidation/V2_CLOSURE_REPORT.md`.
## 隔离多榜单快照 V1.1

新模块保留生产 ASIN、翻译和 Excel 契约，支持五榜单适配、证据注册、内部商品身份审核、不可变快照、恢复、预算、日期比较和报告。四个新增榜单已通过离线夹具测试，实际官网来源及 75 组合仍待验证；P0、30,000 实时采集和真实14天观察均未自动启动。

以下 PowerShell 入口已在独立 worktree 核验；脚本打印实际导入位置，避免使用本机旧 editable 安装。新的空目录或已标记目录可以重复恢复运行。

```powershell
$snapshotWorktree = (Get-Location).Path  # 当前代码检出目录
$snapshotPython = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
Set-Location -LiteralPath $snapshotWorktree
$snapshotStore = Join-Path $snapshotWorktree 'outputs\multisource_user_check'
& '.\scripts\Invoke-Multisource.ps1' -Python $snapshotPython sources --root $snapshotStore
& '.\scripts\Invoke-Multisource.ps1' -Python $snapshotPython replay --root $snapshotStore --plan '.\configs\multisource\offline_five_lists.json'
& '.\scripts\Invoke-Multisource.ps1' -Python $snapshotPython qa --root $snapshotStore --run-id offline_five_lists
```

完整的指定榜单／类目、历史 HTML、暂停恢复、日期／期间比较、报告、人工身份审核和 Windows 外部调度命令见 [多榜单模块说明](docs/MULTISOURCE_SNAPSHOT_V1_1.md)；实时运行前必须完成 [P0 审核计划](docs/MULTISOURCE_P0_PLAN_V1_1.md) 的独立授权。

### 当前三榜单 PAGE 快照与审查

当前快照任务使用独立、显式授权的串行 PAGE 流程，复用保存页面和断点，保留
访问门禁、类目冷却及累计预算。任务参数由已审核计划决定，CLI 帮助和默认测试
均不启动采集。当前 20,000 候选／喜爱榜至少 3,000 的任务说明见
[快照任务契约](docs/SNAPSHOT_MAIN_PRIORITY.md)。

快照的全量离线审查和数量验收使用自己的合约，参见
[快照审查状态](docs/SNAPSHOT_REVIEW_STATUS.md)。`PASSED` 仅表示候选快照审查通过，
不代表正式 ASIN 身份或生产验收通过；不会自动部署、迁移数据或启动采集。
