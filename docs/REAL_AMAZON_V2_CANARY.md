# Real Amazon V2 Canary

受限的 Amazon.es V2 Canary 运行说明。所有产物写入 `runtime/canary/<日期>/<run_id>/`，不会写生产状态、Excel、翻译缓存或图片。

## 零网络 dry-run

```powershell
py -3.12 -m amazon_es_bestseller.cli canary `
  --config configs/canary/amazon_es_v2_canary.json `
  --source-url "https://www.amazon.es/Best-Sellers-Hogar-y-cocina/zgbs/1293659031" `
  --pages-per-source 2 `
  --dry-run
```

dry-run 必须显示 `actual_requests.total_requests=0`；超过 1 个来源、2 页或 5 个详情 ASIN，即使 dry-run 也必须输出 `CANARY_SCOPE_EXCEEDED` 并非零退出。

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

本任务不执行该命令。Stage A 是一来源一页并做在线/离线五元组 replay；通过后才进入 Stage B 两页；通过后 Stage C 从 Stage B 权威快照采样 5 个 ASIN。variation 未观察到时必须记录 `VARIATION_CASE_NOT_OBSERVED`。

## 停止与回滚

403、429、CAPTCHA、Robot Check、Access Denied、Challenge、BOT_BLOCK、INTERSTITIAL 或同类访问限制立即停止，manifest 使用 `CANARY_BLOCKED_BY_ACCESS_GATE`。不得代理、IP/账号/cookie 轮换或验证码/隐身绕过。失败时只保留独立 evidence，重新从 Stage A 开始；不提升 V2 默认路径。

只有 A/B/C 全部 PASS、replay 相等、详情身份无待复核且 Python 3.11/3.12 离线回归均通过，才可报告 `READY_TO_EXECUTE_REAL_AMAZON_V2_CANARY`；这不等于生产就绪。
