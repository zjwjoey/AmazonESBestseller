# 5500 正式采集：Ranking First

`amazon_es_bestseller_5500_202610` 是一个明确分两阶段的正式采集任务。它只可使用 worktree 中已审核的正式计划：

`configs/tasks/amazon_es_bestseller_5500_202610_plan.json`

启动前，程序会计算计划的 canonical JSON SHA-256，并与
`configs/tasks/formal_plan_registry.json` 中锁定的指纹比较。指纹不一致时会在创建浏览器会话前以
`FORMAL_5500_PLAN_HASH_MISMATCH` 失败。

## 1. Ranking phase

```powershell
amazon-es task-collect `
  --plan configs/tasks/amazon_es_bestseller_5500_202610_plan.json `
  --out-dir outputs/amazon_es_bestseller_5500_202610 `
  --phase ranking `
  --previous-details outputs/amazon_es_bestseller_5000_202610/details.json
```

此阶段仅请求已审核的榜单来源：每个研究类目内部仍串行，`parallel3`、冷却、Access Gate、保存的原始 HTML 和 checkpoint 均保持有效。它绝不调用详情采集。

程序按 primary 后 reserve 的顺序收集榜单；只有 primary 不足以达到类目唯一 ASIN/来源分散度要求时才会访问 reserve。所有排名完成后，程序选择全局唯一的 5,500 个候选，并写入：

- `ranking_snapshot/<date>/ranking_<plan-hash>/`：含 `manifest.json`、排名记录、逐页状态及可离线重放的 `html/ranking_*.html`；
- `candidate_manifest.json`：冻结的候选及其完整排名上下文；
- `candidate_manifest.sha256`；
- `ranking_run_report.json`。

如提供 `--previous-details`，Ranking 完成冻结后还会离线对账上一轮详情：

- `detail_reconciliation.json`：每个候选的复用、补采、身份复核或阻断动作；
- `detail_reextract_queue.json`：只有需要网络补采的 ASIN；
- `detail_backfill_queue.json`：所有非直接复用动作；
- `detail_plan/detail_plan.json`：Detail Phase 使用的不可变计划。

有效详情不会再次请求；下一阶段只请求补采队列中的 ASIN，并把历史有效详情合并进新的详情状态。

如果无法精确选出 5,500 个全局唯一 ASIN，阶段以 `QUOTA_UNIQUE_SHORTFALL` 结束，且不会产生可用于详情阶段的候选清单。

**Operator Gate：** 仅在 `ranking_run_report.json` 为 `COMPLETE`、快照为 `AUTHORITATIVE`、候选数为 5,500 且访问状态正常时，才可进入下一阶段。

## 2. Detail phase

```powershell
amazon-es task-collect `
  --plan configs/tasks/amazon_es_bestseller_5500_202610_plan.json `
  --out-dir outputs/amazon_es_bestseller_5500_202610 `
  --phase detail `
  --resume
```

详情阶段不会访问任何 Ranking URL，也不会再次运行配额选择。它验证 ranking report、authoritative snapshot、候选清单及其 SHA-256，然后仅按 `research_category` 分组请求：

```text
冻结候选 ASIN - 已成功详情 ASIN
```

候选以外的详情请求会以 `DETAIL_ASIN_OUTSIDE_FROZEN_CANDIDATES` 失败。详情失败保持在原候选集合中并报告 `DETAIL_INCOMPLETE`；本流程不自动替补 SKU。

## 3. Resume 和禁止项

- Ranking resume 只恢复未完成榜单页，永远不进入详情；
- Detail resume 只恢复候选清单中的未成功详情，永远不重新 Ranking；
- phase 指纹绑定 plan hash、candidate hash（详情）以及可观测的 code head/schema；
- 对正式 5500 运行 `--phase all` 会被拒绝：`FORMAL_5500_REQUIRES_EXPLICIT_PHASE`。

本流程不包含翻译、Qwen 请求、代理轮换、验证码绕过或自动替换候选。
