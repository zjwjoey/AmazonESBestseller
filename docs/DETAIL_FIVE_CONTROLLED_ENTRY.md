# V2 五线详情受控入口

本入口是独立、显式授权的 `amazon-es detail-five-run`，不改变原 `detail-plan` → `detail-run` 的默认 V1 含义。全部新任务使用当前 `parse_detail_evidence_v2`；历史 V1/V2 成功记录保持原格式和原证据。

本轮只完成离线开发与验证，没有签发生产采集授权或部署。不要把下面的参数说明当成现成授权。

## 预检与执行

```powershell
amazon-es detail-five-run --plan <frozen-plan.json> --approval <reviewed-approval.json> --out-dir <original-run-root> --profile-dir <original-profile> --validate-only
```

`--validate-only` 只读校验授权、当前干净 Git HEAD 和两个共享 Gate，不打开浏览器，也不创建运行状态。历史 checkpoint/HTML 的逐项校验发生在正式入口创建浏览器之前；通过只读预检不等于所有历史数据已通过。

移除 `--validate-only` 才可能创建可见浏览器，必须另有对应任务的有效运行授权。入口要求如下：

- `approved=true`、批准人和明确用户指令；`scope=FIVE_LANES_GROUPED_3_PLUS_2`，`lanes=5`、`processing_groups=[3,2]`、`max_uncommitted_tasks=5`、`parser_contract=2`。
- 当前干净 `code_sha`、原 `run_id`、原 `runtime_root`、原 `browser_profile`、冻结计划的规范化 `plan_sha256`。计划任务保留 task/candidate/selection ID、ASIN、精确 HTTPS Amazon.es URL；任务 ID 和 ASIN 不重复。
- `base_authorization_reference` 和 `cumulative_budget_reference` 各有路径与文件 SHA256。基准授权绑定原根目录、计划和 profile；累计账本绑定原 run 和基准授权 SHA。
- 原 `starts_at`、`deadline`、`request_deadline`、`max_requests`、`max_page_requests`、`max_other_requests`、`max_attempts_per_task=1`、`required_postal_code` 必须继承，不能重置。
- 初始已付请求 `paid_requests` 与保守占额 `budget_holds` 继承账本；新 reservation 继续占额，未观察到网络的 reservation 也不自动退还。新执行累计占额的上限为原总量与 PAGE 上限的较小值。
- 全局 `request_start_interval_seconds >= 3.6`，也不得小于原授权间隔。不是每个窗口各自每 3.6 秒发送。
- `access_gate_paths` 指向两个原共享 sealed Gate，读取的是 payload 顶层状态，必须均为 NORMAL。缺失、损坏或非 NORMAL 拒绝执行。

授权引用和冻结计划必须由实际任务审核产生。本入口没有扩大旧任务范围、预算、时钟、身份或访问恢复批准的权限。

## 状态、持久化和恢复

统一队列按冻结顺序领取，最多五个未提交任务；每个窗口对应独立 parser 进程。所有 terminal outcome 经父进程的统一写入锁落盘，parser 不写 checkpoint。原始 HTML、收据、任务绑定与哈希先落盘，再写不可变 outcome。

新状态存入原 run 下独立的 `parallel_*` 命名空间；不会覆盖原 `html/`、`checkpoints/` 或 `details.json`。原正式 `checkpoints/<ASIN>.json` 和 `html/<ASIN>.html` 中可验证的成功记录只导入引用与哈希，不重新请求、不升级成新版本，也不伪造新的 HTTP 收据。导入成功数不得大于继承的已付请求数。

|结果|行为|
|---|---|
|SUCCESS|保留成功记录，恢复时跳过|
|IDENTITY_REVIEW / PARSE_FAILED / DETAIL_INVALID|失败仍失败；证据持久化后处理其他任务，恢复时不重试|
|NETWORK_FAILED|只有明确服务器错误或严格有原始正常响应证据的单品 DOM 就绪超时才隔离；不算成功|
|ACCESS_STOP|停止全部新商品请求，排空在途证据，保留可见页面；无自动恢复|
|系统持久化、状态或 parser 进程故障|停止新领取，保留未决 reservation；不自动重试|
|UNSENT|已占额但因全局停止未发起导航；单列，不冒充实际请求|

终端 HTTP 为 null 的超时不回填成 200。仅当原任务起始事件已观察、原 GET URL 与最终 URL 相同、main Document 响应全部为同 URL 200、确为 DOMContentLoaded 超时、完整保存页无限制信号且配送地点与原授权一致、共享 STOP 尚未触发时，才允许记录失败并继续。其他 UNKNOWN 停止。

403/429 在响应回调立即停止；页面内 Challenge/Robot Check 等在首个检出的完整快照立即停止，不等待剩余采样。正常页保持六次完整 HTML 读取和最终至少三秒的渲染观察。检测器、配送与身份标准没有放宽。

不完整 reservation、历史失败、只有 HTML 没有成功 checkpoint、哈希变化或已持久化全局停止，均要求离线证据复核或合法恢复处理，入口不清 Gate、不探测、不重试。不要通过更换根目录、profile 或账本避开这些状态。

## 离线复现

`scripts/verify_parallel_detail_local.py` 使用真实保存 HTML、临时 profile/store、可见 Chromium 与仅 loopback 的 HTTP 服务，支持 1/3/5 线同源比较和 `--inject-parser-error`。安装项目依赖、Playwright Chromium 及 `psutil` 后可使用。它的始终正常 Gate 回调是测试夹具，正式授权、双 Gate 与历史恢复由独立测试覆盖；性能数据不包含生产授权文件校验成本。

`scripts/verify_parallel_detail_redirect_local.py` 另外验证真实 302 链以及 `--challenge-first` 的 HTTP 200 挑战页停止。临时浏览器不使用生产 profile。输出必须使用新文件名；测试证据另存，旧失败结果保留。

这些结果只证明受控本地行为。没有真实 Amazon 五线压力测试、生产 wire 并发测量或持续吞吐承诺。
