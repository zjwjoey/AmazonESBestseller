# 增量审查、证据与缓存

新任务使用同一 Task Workspace。快照计划和详情计划带有任务 ID 与工作区路径，冻结后不得改写。详情可引用外部历史快照，任务清单记录来源路径与 SHA-256。事先核对来源、访问授权和现有门禁。

## 标准入口

在 PowerShell 中运行，路径参数替换成任务实际路径。

```powershell
amazon-es snapshot profile --out <config.json>
amazon-es snapshot prepare-page --base-plan <reviewed-plan.json> --base-authorization <reviewed-authorization.json> --config <config.json> --out <plans> --runtime-root <runtime> --task <task.json>
python -m amazon_es_bestseller.highspeed.v4_cli check --root <task-root>/store --plan <plan.json> --authorization <authorization.json>
python -m amazon_es_bestseller.highspeed.v4_cli run --root <task-root>/store --plan <plan.json> --authorization <authorization.json> --live --headful
amazon-es snapshot status --root <task-root>/store --run-id <run-id>
amazon-es detail-plan --task-root <task-root> --snapshot <rankings.json> --html-dir <saved-html>
amazon-es detail-run --offline --plan <task-root>/detail/plan/detail_plan.json --html-dir <saved-html>
amazon-es task-finalize --task-root <task-root>
```

上面的快照准备生成待审计划。另行完成既有授权、预检和访问门禁后，才可启动在线运行。快照续跑使用同一计划、授权和工作区，把 `run` 改成 `resume`。有主动暂停时，按快照文档完成确认。遇到访问限制继续采用原有人工恢复规则。详情的 `--offline` 只接受离线动作。已获授权的详情联网动作由冻结计划决定。详情中断后提交同一计划，保留原有 checkpoint。

## 工作区与自动审查

- `task_manifest.json` 保存任务 ID、阶段关联、规则版本、工作区范围、容量及质量阈值。
- `store/` 保存快照计划及调度索引。PAGE 和关联 ACP、渲染证据提交后，在该 PAGE 范围内核对保存的原件、回执、哈希及结构。基于同证据和规则的已审页面直接复用账本。候选链接异常记入警告，证据或来源绑定错误停止派发。
- 快照进度分别记录通过、警告、失败页面数及各榜单观测。截至当前 PAGE 的商品统计只从 SQLite 读取新增观测及其来源页。恢复时从账本重建页面计数。
- `detail/` 保存计划、详情数据和 checkpoint。单条 checkpoint 落盘后立即检查页面身份、标题和关键字段。默认每 50 个终态结果提交详情状态及完整性报告，最后不足 50 条也会提交。访问暂停时保存已取得的部分批次。身份不符、缺标题及受限记录按审查结果记录，商品数据仅从验收的 checkpoint 发布。
- `audit/workspace.sqlite3` 保存快照和详情的审查账本及证据索引。当前结果与历史尝试分开保存；输入内容、解析和审查版本变化会影响复用。批次报告在 `audit/batches/`，任务字段统计在 `audit/field_completeness.json`，异常在 `audit/anomaly_asins.json`。
- 每个重点字段报告来源可用、来源缺失、程序漏抓、低质量和未知。有效提取率与程序漏抓率采用来源实际可用的数量作为分母。有效数据覆盖率采用适用的正常商品页面作为分母。分母为零时返回 `null`。缺少原文和程序漏抓分别计数。来源可用样本至少 10 条后，漏抓率达到 5% 预警，达到 10% 暂停；关键品名另有批次和全任务阈值。
- 访问门禁、请求限速、排名口径及既有 Excel 交付契约继续按原规范执行。

## 容量与证据

`cache/active/` 与 `cache/temporary/` 共用一个 2 GiB 上限。约 1.6 GiB 进入预警，约 1.8 GiB 触发归档，空间不足时停止新的写入和派发。容量按字节核算。临时压缩文件和快照只读审计的数据库副本均计入上限。快照读副本在复制前检查所需空间，读取结束后释放空间，源快照数据库继续保持只读。归档文件不计入活动容量。

审查结果入库后，压缩原件、解压核对原件哈希，再发布 gzip 和对应索引。验证成功才清退活动副本。压缩前中断保留活动原件。归档文件和索引不同步时，使用原件与校验信息恢复识别。没有审查依据或来源不明的文件保留供核对。历史归档只读，按原始 SHA-256 验证；原普通 HTML、已有证据引用及压缩 HTML 均可用于重放。

## 最终对账

`task-finalize` 生成 `reports/task_quality_summary.json`，检查计划覆盖、当前账本、详情批次、有效输出、归档哈希和缓存峰值。状态依次区分 `COLLECTION_COMPLETE`、`QUALITY_REVIEW_COMPLETE` 和 `RESEARCH_READY`。有待核字段或身份记录时不能视作研究准入通过。历史独立审查命令仅用于诊断旧产物。
