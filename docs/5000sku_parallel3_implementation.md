# Amazon.es 5,000 SKU 任务落地说明

本任务使用 `task-collect`，与旧版 `batch-collect`（31–50 补采）保持独立。

## 模式

- `parallel3`：最多三个研究类目并行；每个类目内部串行；类目完成后该工作槽冷却 600 秒。
- `serial`：单类目串行备用；默认类目间冷却 1,800 秒。
- 任一工作槽遇到 Challenge、403、429、Robot Check、CAPTCHA 或 Access Denied，停止所有新请求并保留证据。

## 运行前置

1. 通过 `discover-tree` 保存当前 Amazon.es Bestseller 类目树快照。
2. 人工审核研究类目与原始 Bestseller 榜单的对应关系。
3. 使用 `scripts/build_5000_task_plan.py` 生成可执行计划。
4. 计划必须包含 15 类、总计 5,000、真实 Amazon.es 来源 URL，
   `discovery_required=false`、`sources_reviewed=true` 和当前快照路径；空来源模板不能直接运行。
   正式 plan 中的 `source_snapshot` 使用相对项目根目录的 POSIX 路径（例如
   `outputs/.../category_tree_snapshot.json`），不会写入开发机盘符。运行时由
   `resolve_task_path` 以当前 worktree 项目根目录解析；快照必须位于项目根目录内。
5. 任务目标为 1–80 排名时，每个来源默认抓取两页（`pages_per_url=2`），覆盖第一页和第二页排名。

来源映射支持 `primary` 和同类目的 `reserve` 两组。主来源不足以满足类目
唯一 ASIN 或来源分散度要求时，程序自动启用 reserve；若全局配额仍短缺，
状态会保留为 `QUOTA_UNIQUE_SHORTFALL`，下一次续跑会启用尚未使用的 reserve 来源。

## 发现类目

```powershell
python -m amazon_es_bestseller.cli discover-tree `
  --urls https://www.amazon.es/gp/bestsellers/kitchen/ `
  --out-dir outputs/amazon_es_bestseller_5000_202610/discovery `
  --headful
```

## 生成计划

```powershell
python scripts/build_5000_task_plan.py `
  --template configs/tasks/amazon_es_bestseller_5000_202610_plan.template.json `
  --snapshot outputs/amazon_es_bestseller_5000_202610/discovery/category_tree_snapshot.json `
  --mapping configs/tasks/amazon_es_bestseller_5000_202610_mapping.template.json `
  --out configs/tasks/amazon_es_bestseller_5000_202610_plan.json
```

脚本默认以仓库根目录为 `--project-root`。如果在脚本外部调用，可显式传入
`--project-root`；快照不在项目根目录内时脚本会拒绝生成计划，避免把本机绝对路径
写入可提交配置。

## 正式运行

```powershell
python -m amazon_es_bestseller.cli task-collect `
  --plan configs/tasks/amazon_es_bestseller_5000_202610_plan.json `
  --out-dir outputs/amazon_es_bestseller_5000_202610 `
  --mode parallel3 `
  --headful
```

并行模式发生兼容性或访问问题时，使用同一计划和输出目录切换备用模式：

```powershell
python -m amazon_es_bestseller.cli task-collect `
  --plan configs/tasks/amazon_es_bestseller_5000_202610_plan.json `
  --out-dir outputs/amazon_es_bestseller_5000_202610 `
  --mode serial `
  --headful
```

## 任务结果整理

采集通过 Gate 后，使用最终唯一-ASIN manifest 做离线规范化；不会把未入选
候选混入内部研究表：

```powershell
amazon-es enrich `
  --rankings outputs/amazon_es_bestseller_5000_202610/final_manifest.json `
  --details outputs/amazon_es_bestseller_5000_202610/details.json `
  --out outputs/amazon_es_bestseller_5000_202610/products.json

amazon-es export `
  --products outputs/amazon_es_bestseller_5000_202610/products.json `
  --details outputs/amazon_es_bestseller_5000_202610/details.json `
  --rankings outputs/amazon_es_bestseller_5000_202610/final_manifest.json `
  --profile task `
  --out outputs/amazon_es_bestseller_5000_202610/amazon_es_bestseller_5000_internal_research.xlsx
```

`task` profile 不改变默认三张核心表和中文 26 列，只额外增加独立的
`采集任务元数据` provenance 表；原始 Spanish evidence 仍保留在 JSON/西语表。

## 完成条件

只有 `run_report.json` 中 `run_status=COMPLETE` 且 `final_unique_asins=5000`，同时 15 类分别达到计划配额时，任务才算完成。`QUOTA_UNIQUE_SHORTFALL` 不得被当作成功。

详情失败会保存在类目状态的 `pending_detail_asins` 队列中并优先续采；
只要仍有详情缺失，`final_manifest.json` 保持为空，候选结果另存为
`candidate_manifest.json`，不能提前进入翻译或 Excel 正式导出。

历史 7,365 SKU 文件不会作为本轮候选，也不会被覆盖。Action 映射和中文翻译属于后续独立阶段。
