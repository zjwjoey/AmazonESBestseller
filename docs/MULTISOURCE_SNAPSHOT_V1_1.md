> 历史参考：旧 LIVE 执行程序和 Invoke 包装脚本已移除。当前使用 `python scripts/amazon_es.py snapshot --help`；历史数据仅离线审查。以下保留历史命令记录，不能执行旧 LIVE 任务。

# Multi-Source Snapshot Test V1.1

隔离的榜单观察与身份审核模块。生产 ASIN 主键、详情、翻译缓存、图片及三表／26 列 Excel 契约不迁移。五个适配器分别识别页面 URL、标题、排名及分页；当前新增四榜单只通过合成离线夹具，没有本轮官网验证。所有 75 个逻辑组合仍需来源证据，不能用路径替换制造入口。

## 存储与身份

独立空目录初始化后写入 `.multisource_store.json`。每个运行保留不可变 `run_spec.json`、原始 HTML 内容哈希、请求回执、页面日志；可恢复 `checkpoint.json` 与暂停控制分开保存。冻结目录 `runs/<run_id>/snapshots/v1` 含 manifest、观察 JSONL、页面、身份映射、QA、执行日志及阶段 Gate。manifest 对冻结文件逐一存 SHA256。重解析生成 v2 等新目录，保留最初采集时间及 HTTP 状态。

观察 ID 根据 snapshot/source/page/slot 生成；商品 ID 经独立详情证据或人工确认后才分配。链接 ASIN 与重建 URL 属同源线索；卡片与链接一致仍可以待确认。`DETAIL_PAGE` 证据必须有实际保存的 HTML、SHA256、requested/final URL、带时区时间和 NORMAL 状态，并验证实际主商品身份。Parent ASIN 只记录家族；不同子商品不因此合并。

重解析改变身份字段时，独立 `identity_view_id` 接受审核，不继承旧槽位的商品 ID。审核和附加证据使用该 ID；原始 `observation_id` 保持不变，视图不增加榜单观察数量。审核事件、合并及撤销有哈希链，原始观察不删除。旧版兼容映射只导出确认且一对一的 ASIN／商品关系。

## 模式、完整性与预算

`OFFLINE_FIXTURE`、`HISTORICAL_REPLAY`、`LIVE_CAPTURE` 分开统计。历史回放要求 `capture_metadata_path` 指向原始元数据 JSON，含 `observed_at`（带时区）、`page_url`、`html_sha256`，并与输入吻合。缺少真实采集时间的旧 HTML 只能用明确的夹具时钟测试解析能力；示例 `offline_archived_best_sellers.json` 正是这种情况，不能当作新日期采集。

COMPLETE 要求原始页面中的明确数量元数据、有效页面类型、证据哈希及完整实际槽位。未知数量为 UNVERIFIED；未续载 ACP、排名／分页／来源冲突阻止晋级。完整性针对经过审核的计划页面覆盖；不会据此声称整类目或整站已完整采集。只有实际提供连续名次的页面才核查区间连续性。Movers 的销售排名提升百分比不是销量增长率。

累计观察上限 30,000，五榜单预算为 9,000／7,500／6,000／3,750／3,750；阶段累计上限为 1,500／5,000／15,000／30,000。另有请求、页面和 Europe/Madrid 每日请求上限。恢复不增加已提交槽位数；网络请求先保留额度，崩溃后不静默删除不确定尝试。报告区分保留额度、已保存请求回执、页面快照、观察、候选线索和确认商品。

补位方案先同榜单同类目，再同榜单其他类目；跨榜单需要明确批准。`SamplingPlanner.replacement_plan` 只生成经过验证来源的计划，不自动访问网站；`budget-adjust` 仅接受带来源、数量、页预算、证据、理由及审核人信息的方案，保留调整前后与缺口。

LIVE 默认关闭。P0 也要独立授权；每次请求复核有效期、来源、预算和 Access Gate。403／429／Challenge／未知访问状态立即停止新请求。浏览器不自动续滚页面，ACP 经显式计数串行访问；文档跳转受已审核 URL 范围限制。跨研究类目默认冷却 1,800 秒，持久化 deadline，每 30 秒显示剩余时间；零冷却只能由任务授权明确指定。没有代理、账号、Cookie 或验证码绕过。

## PowerShell：已核验的离线命令

在新 worktree 执行。辅助脚本明确设置本次源码路径并打印实际导入位置，避免本机旧 editable 安装被错误使用；不修改全局 Python 安装。

```powershell
$snapshotWorktree = 'C:\Users\Administrator\.codex\worktrees\multisource-snapshot-v1-1\AmazonESBestseller'
$snapshotPython = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
Set-Location -LiteralPath $snapshotWorktree
$snapshotRoot = Join-Path $snapshotWorktree 'outputs\multisource_user_check'
$invokeSnapshot = Join-Path $snapshotWorktree 'scripts\Invoke-Multisource.ps1'

& $invokeSnapshot -Python $snapshotPython sources --root $snapshotRoot
& $invokeSnapshot -Python $snapshotPython replay --root $snapshotRoot --plan 'configs\multisource\offline_five_lists.json'
& $invokeSnapshot -Python $snapshotPython run --root $snapshotRoot --plan 'configs\multisource\offline_five_lists.json' --source-type MOVERS_SHAKERS --run-id 'offline_movers'
& $invokeSnapshot -Python $snapshotPython run --root $snapshotRoot --plan 'configs\multisource\offline_five_lists.json' --research-category '厨房用品' --run-id 'offline_kitchen'
& $invokeSnapshot -Python $snapshotPython replay --root $snapshotRoot --plan 'configs\multisource\offline_archived_best_sellers.json'
& $invokeSnapshot -Python $snapshotPython status --root $snapshotRoot --run-id offline_five_lists
& $invokeSnapshot -Python $snapshotPython pause --root $snapshotRoot --run-id offline_five_lists
& $invokeSnapshot -Python $snapshotPython resume --root $snapshotRoot --run-id offline_five_lists --plan 'configs\multisource\offline_five_lists.json'
& $invokeSnapshot -Python $snapshotPython verify --root $snapshotRoot --run-id offline_five_lists
& $invokeSnapshot -Python $snapshotPython qa --root $snapshotRoot --run-id offline_five_lists
& $invokeSnapshot -Python $snapshotPython reparse --root $snapshotRoot --run-id offline_five_lists --version v2

# 两个合成日期，保持 OFFLINE_FIXTURE 标识。
& $invokeSnapshot -Python $snapshotPython daily --root $snapshotRoot --plan 'configs\multisource\offline_14day_simulation.json' --date 2026-10-12
& $invokeSnapshot -Python $snapshotPython daily --root $snapshotRoot --plan 'configs\multisource\offline_14day_simulation.json' --date 2026-10-13
& $invokeSnapshot -Python $snapshotPython compare --root $snapshotRoot --left fixture_days_20261012 --right fixture_days_20261013
& $invokeSnapshot -Python $snapshotPython compare --root $snapshotRoot --left-date 2026-10-12 --right-date 2026-10-13 --source-type MOVERS_SHAKERS
& $invokeSnapshot -Python $snapshotPython compare --root $snapshotRoot --left-start 2026-10-12 --left-end 2026-10-12 --right-start 2026-10-13 --right-end 2026-10-13
& $invokeSnapshot -Python $snapshotPython compare --root $snapshotRoot --history
& $invokeSnapshot -Python $snapshotPython report --root $snapshotRoot --run-id offline_five_lists --out 'F:\AmazonESBestseller\交付文件\20261013\MultiSourceSnapshotTestV1_1\用户运行报告'
& $invokeSnapshot -Python $snapshotPython exceptions --root $snapshotRoot --run-id archived_html_fixture --out 'F:\AmazonESBestseller\交付文件\20261013\MultiSourceSnapshotTestV1_1\用户运行报告'
```

原有 HTML 夹具可能 PARTIAL 或 UNVERIFIED，这是实际异常记录，不是命令执行失败。身份待确认时不输出确定的新增／退出，比较返回不足原因；不同来源、范围、日期或模式不直接比成销量。报告同时给出当前快照候选与经完整 QA 的同模式历史候选池，5,500 配额只计确认 Product ID。

`discover` 使用保存 HTML 与显式映射文件，禁止猜测研究类目；示例格式：

```powershell
& $invokeSnapshot -Python $snapshotPython discover --root $snapshotRoot --html 'tests\fixtures\multisource\best_sellers.html' --source-type BEST_SELLERS --page-url 'https://www.amazon.es/gp/bestsellers/kitchen/12345' --observed-at '2026-10-12T08:00:00Z' --origin OFFLINE_FIXTURE
```

人工审核使用 `identity-review --action same|different|family|pending --observation-id <ID>`，并提供 `--actor` 与 `--evidence`；重复 `--observation-id` 可传多个 ID。详情证明 JSON 数组通过 `identity-attach --evidence <proofs.json> --reason <理由>` 附加；撤销用 `identity-undo --event-id <事件ID>`。重新生成报告即可更新映射与统计，冻结文件不覆盖。

## 外部调度及阶段授权

`daily --plan live_14day.template.json` 是外部调度入口。先在独立副本填写审核后的起始日期、启用状态、来源计划和阶段授权，再调用；每天 run ID 根据 Madrid 日期生成。不会自动从 P0 升级到 P1，也不会将历史回放日期改成当天。

下面仅是待审核配置的 Windows 任务计划程序示例，没有在本机注册任务。必须先完成 [P0 计划](MULTISOURCE_P0_PLAN_V1_1.md) 的明确授权。调度命令与实时浏览器未在本轮联网执行。

```powershell
$dailyScript = Join-Path $snapshotWorktree 'scripts\Invoke-Multisource.ps1'
$dailyConfig = 'F:\AmazonESBestseller\runtime\approved_multisource\live_14day.json'
$dailyAuthorization = 'F:\AmazonESBestseller\runtime\approved_multisource\phase_authorization.json'
$dailyStore = 'F:\AmazonESBestseller\runtime\approved_multisource\store'
$taskArgs = '-NoProfile -File "' + $dailyScript + '" -Python "' + $snapshotPython + '" daily --root "' + $dailyStore + '" --plan "' + $dailyConfig + '" --authorization "' + $dailyAuthorization + '" --live'
$dailyAction = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument $taskArgs -WorkingDirectory $snapshotWorktree
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '09:00'
$dailySettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew
# 得到调度授权后才执行注册；09:00 是 Windows 主机当地时间，程序自行按 Madrid 日期编号。
# Register-ScheduledTask -TaskName 'AmazonES-Multisource-Reviewed' -Action $dailyAction -Trigger $dailyTrigger -Settings $dailySettings
```

## 开发验收和回退

`python -m pytest` 默认全离线；CI 执行原有帮助、新 CLI 帮助、compileall 及完整离线测试。真实14天和 P0 未执行。迁移只允许独立存储中的 schema0 包装转 schema1，原件哈希备份及迁移记录保留；`migrate --state <旧文件>`、`rollback --state <迁移记录>` 不触及生产。代码回退用独立分支的阶段提交；生产分支不合并。未来正式主键迁移需另立项目，当前只能通过已确认的一对一兼容映射读取旧业务数据。
