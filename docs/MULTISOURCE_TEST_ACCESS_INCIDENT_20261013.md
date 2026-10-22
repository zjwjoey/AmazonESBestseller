# 离线验收中的旧联网测试误触记录

日期：2026-10-13。本次任务明确没有授权 P0 或其他真实 Amazon 采集。

完整回归首次运行时，执行环境把 `RUN_LIVE` 设为字符串 `0`，意图关闭旧联网测试。旧文件 `tests/test_collection_live.py` 的跳过条件实际为 `not os.getenv("RUN_LIVE")`，因此任意非空值都开启联网测试，与文件声明“仅 RUN_LIVE=1”不一致。两项旧测试进入真实 BrowserSession：

1. `test_collect_rankings_live` 调用 `ensure_spain_delivery`，访问 Amazon.es 首页与配送地点弹窗后，因找不到西班牙邮编输入框而失败。
2. `test_collect_details_live` 同样进入首页配送地点检查，因未找到邮编确认控件而失败。

这属于执行错误，不是已经授权的测试。不能把“新模块的 LIVE 请求数为零”扩展成“本次从未访问 Amazon”。两个测试都在实际榜单／商品详情收集之前失败，临时输出目录检查为空；没有产生新榜单记录或详情证据，不能用作任何来源可用性证明。没有依据声称总 HTTP 请求只有两个，因为浏览器页面和弹窗也会请求资源。

首次完整回归进程已结束，未继续旧测试或启动 P0。错误日志保留于：

`F:/AmazonESBestseller/runtime/multisource_snapshot_test_v1_1_20261013/full_offline_tests.xml`

修复仅发生在隔离开发分支：

- 将旧测试条件改为 `os.getenv("RUN_LIVE") != "1"`；新增七个离线回归案例，只检查 marker，不调用浏览器。
- 清除环境变量后重新运行完整离线回归；CI 显式保持 `RUN_LIVE` 为空。
- 新快照层仍要求实时开关、阶段授权文件、精确来源／预算、有效期和 Access Gate。其授权检查以及全局 `--offline` 禁止联网规则保持不变。
- P0、真实30,000记录和真实14天观察仍未执行。所有合成规模和时间序列结果继续标为 OFFLINE_FIXTURE。

生产 main、采集数据、HTML、翻译缓存和 Excel 没有因这次误触被改写。最终交接报告必须保留这一执行例外，并引用重新执行后的离线验收结果；不能仅列最终绿色测试而隐去首次误触。
