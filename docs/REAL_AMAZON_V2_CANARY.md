# Real Amazon V2 Canary

受限的 Amazon.es V2 Canary 运行说明。所有产物写入 `runtime/canary/<日期>/<run_id>/`，不会写生产状态、Excel、翻译缓存或图片。

## Prerequisites

使用 Python 3.12，依赖和 Playwright 浏览器已安装；来源 URL 必须经过人工审核。真实入口需要 Owner 在同一次指令中明确授权。本任务本身保持 `REAL AMAZON REQUESTS = 0`。

## 零网络 dry-run

```powershell
py -3.12 -m amazon_es_bestseller.cli canary `
  --config configs/canary/amazon_es_v2_canary.json `
  --source-url "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs/1293659031" `
  --pages-per-source 2 `
  --dry-run
```

dry-run 必须显示 `actual_requests.total_requests=0`；超过 1 个来源、2 页或 5 个详情 ASIN，即使 dry-run 也必须输出 `CANARY_SCOPE_EXCEEDED` 并非零退出。

## Stage A

Stage A 只访问 1 个来源的第 1 页，使用 ranking parser V2 和 Playwright。必须通过 Access Gate、expected count、ranking/identity/slot completeness、冲突/排名缺口/重复排名、Unified Authority，并保存 HTML、page status、client-recs metadata、ACP 证据、identity/ranking audit 和 manifest。在线结果与离线 replay 必须按 `(ASIN, rank, ranking_source_url, ranking_page_url, ranking_page_number)` 完全相等。

## Stage B

只有 Stage A PASS 才进入同一来源的第 1、2 页。每页报告 `page_instance_id`、来源 URL、页 URL、页码、server count、expected count、ACP count、final count 和 page authority；全局还必须 `ranking_slot_complete=true`、`identity_complete=true`、`final_authoritative=true`。跨页重复 ASIN 允许。两页在线/离线五元组必须完全相等。

## Stage C

只有 Stage B PASS 才从其 AUTHORITATIVE snapshot 采样 5 个 ASIN，不能手填。采样覆盖低、中、高排名，并尽量覆盖 variation/parent 与 standalone；无 variation 时写入 `VARIATION_CASE_NOT_OBSERVED`。详情使用 V2，核验 identity、parent/family、ordered evidence、duplicate attributes、category provenance、saved HTML 及离线重解析。

## 真实入口

确认 dry-run、离线回归和人工前置条件都通过后，唯一真实入口是：

```powershell
py -3.12 -m amazon_es_bestseller.cli canary `
  --config configs/canary/amazon_es_v2_canary.json `
  --source-url "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs/1293659031" `
  --pages-per-source 2 `
  --out-dir runtime/canary `
  --execute-real-amazon
```

本任务不执行该命令；它只是明确的人工授权入口。

## Stop Conditions

403、429、CAPTCHA、Robot Check、Access Denied、Challenge、BOT_BLOCK、INTERSTITIAL 或同类访问限制立即停止，manifest 使用 `CANARY_BLOCKED_BY_ACCESS_GATE`。不得代理、IP/账号/cookie 轮换或验证码/隐身绕过。失败时只保留独立 evidence，重新从 Stage A 开始；不提升 V2 默认路径。

## Artifacts

查看 `canary_manifest.json`、各 stage 的 `manifest.json`、`page_statuses.json`、保存的 HTML、client-recs/ACP 原始响应、identity/ranking audit、详情 checkpoints 和 replay 比对。`actual_requests` 精确记录 ranking page、ACP、detail page 和 total。

## Interpretation

阶段状态为 A=`PASS/FAIL/BLOCKED_BY_ACCESS`，B/C=`PASS/FAIL/NOT_RUN`；总状态只有全部计划阶段通过才是 `CANARY_PASS`，访问限制是 `CANARY_BLOCKED_BY_ACCESS`，其他情况是 `CANARY_FAILED`。这不等于生产就绪，不提升 V2 默认 parser。

## Rollback / No-Promotion

失败时保留独立证据目录并从 Stage A 重跑；不更新 production pointer，不写正式详情/翻译/Excel/图片，不合并默认分支。只有 A/B/C 全部 PASS、replay 相等、详情身份无待复核且 Python 3.11/3.12 离线回归均通过，才可报告 `READY_TO_EXECUTE_REAL_AMAZON_V2_CANARY`。
