# 文件读写、缓存终态对账与8项修复版本核对

日期：2026-10-27，北京时间。范围：离线修复、历史证据只读对账、运行版本核验及退出翻译API调用。未部署、未启动采集、未调用翻译API。

## 结论

- R01已实现读写修复，并通过真实Windows文件锁、并发替换、有界失败和原文件保全回归。修复位于独立工作树；历史任务脚本生成了可复核副本，未替换原入口。
- R02已闭合5500个冻结ASIN的分项覆盖。成功仍为5412；两条原先漏出统计的缓存任务明确留置，不能补算成功。
- T01—T08在当前修复树全部48项通过；最新远端main、生产目录入口、5500任务私有发布包均为15通过/33失败，每组都有失败。因此8组均尚未完整进入main或实际运行版。本轮只核对，不替用户执行合并或生产入口切换。
- Qwen/DeepSeek的内置HTTP传输代码及CLI实际执行逻辑已从修复工作树移除；旧命令返回`TRANSLATION_API_REMOVED`。保留离线字典、模型翻译结果接入、fake集成、缓存/hash/schema兼容工具与历史账本。没有删除缓存、凭证文件、账本或任何生产数据。

## R01：保留原文件的有界恢复

实现：`collection/runtime_io.py`，接入正式checkpoint、DetailState、detail executor输出、任务JSON、CLI JSON、详情计划和详情summary写入；历史run.py/run_shared.py/resume_control.py的修订副本也复用该实现。

1. 使用系统唯一临时文件，在同目录写完整JSON并flush/fsync，关闭句柄后原子替换。同进程同路径替换串行化。
2. 最多6次尝试，等待0.05/0.15/0.35/0.75/1.5秒，不无限重试。替换的WinError 5/32/33允许有界重试；WinError5已在本机并发替换复现。
3. 读取只对实际共享/锁定32/33重试。CPython有时将Windows共享冲突降为errno13而丢失winerror；此时用只读CreateFileW确认具体共享错误。一般权限拒绝不当作缓存缺失。
4. 删除原task writer的非原子“直接截断写回退”。持续拒绝、磁盘/落盘失败继续抛错，保留旧目标与唯一暂存文件，不冒称checkpoint成功。
5. 修订任务副本在持久化失败后锁存停止信号，停止新领取，并在真正goto前再次检查；已停止的访问窗口仍按原授权保留，不转移受限任务。

真实Windows回归使用CreateFileW持有独占读/禁止删除共享的句柄，首次读/replace实际被系统拒绝，释放句柄后恢复。另有40次并发写、持久拒绝、损坏JSON、checkpoint读失败不得转成cache miss、成功断点不重请求回归。

这不证明历史所有WinError5来自同一根因；真实权限错误仍需要修复环境，不会用更大权限或非原子覆盖掩盖。

## R02：两条缓存任务及5500全量对账

读取原任务6086份checkpoint及相关原计划、DetailState和最终记录；共6091个读取源文件在处理前后逐一比对SHA-256一致。原始progress、parallel_result、held、冻结队列和checkpoint均不改写；对账结果写在独立输出目录。

| 请求ASIN | 原计划身份 | 原计划resolved ASIN | 指定同ASIN保存页 | 离线executor结果 | 对账处理 |
|---|---|---|---|---|---|
| B07B8D8RC5 | VARIATION_RELATED | B09WMV88VY | 不存在 | PENDING，0请求 | CACHE_UNAVAILABLE_IDENTITY_REVIEW |
| B0G46PL7RN | VARIATION_RELATED | B0G46N2ZLT | 不存在 | PENDING，0请求 | CACHE_UNAVAILABLE_IDENTITY_REVIEW |

这是“原计划有变体关联提示，但目前无法离线核验同ASIN缓存”的证据，不能宣布两件商品绝对错配，也不能拿另一变体当本商品或伪造成功。

| 对账分项 | 数量 |
|---|---:|
| 新采成功 | 5385 |
| 既有有效复用 | 27 |
| failed | 22 |
| asin_mismatch | 63 |
| access_stop | 1 |
| 缓存不可复用／身份待核 | 2 |
| 合计 | 5500 |

成功5412，非成功88，状态仍为PARTIAL；不把“分项对账完成”当作“5500采集全部完成”。

修复的`reconcile_outcomes`以冻结任务集合为基准，每个ASIN一条；遗漏的网络任务显示PENDING，缓存动作显示CACHE_REVIEW_REQUIRED，其他留置显示HELD_REVIEW_REQUIRED；重复冻结身份、集合之外结果直接报错。原有成功优先于旧held标签。统计器不授予身份或缓存成功。

`scripts/repair_detail_runtime.py`只允许在生产项目之外的新目录输出：复制隔离计划，调用原正式executor的offline路径，生成独立对账报告及任务脚本副本。缓存正向回归使用真实保存的B07QHCNQ7M页面，验证仍可复用且原HTML hash不变；负向回归保留上述两条真实计划事实，证明另一个子ASIN或缺页不能晋升成功。

## T01—T08：同一套测试核对实际代码

核实远端main为`ce74a968b16a9168656494b50bfd9907b99fb667`。通过git archive导出该确切提交，不修改任何checkout或生产安装。测试和fixture复制到独立目录，pytest使用独立配置、显式PYTHONPATH，验证实际import路径，禁止仓库配置把测试偷偷导回修复树。

| 项目 | 当前修复树 | origin/main | F盘正式入口代码 | 5500私有运行包 |
|---|---:|---:|---:|---:|
| T01 混合轴单位解析 | 8/8 | 3/8 | 3/8 | 3/8 |
| T02 否定材质 | 3/3 | 2/3 | 2/3 | 2/3 |
| T03 混合轴QA | 7/7 | 5/7 | 5/7 | 5/7 |
| T04 品牌/制造商原名误报 | 4/4 | 0/4 | 0/4 | 0/4 |
| T05 尺寸与Pack数量边界 | 11/11 | 3/11 | 3/11 | 3/11 |
| T06 零件编号与件数 | 3/3 | 0/3 | 0/3 | 0/3 |
| T07 尺寸轴向显示 | 6/6 | 0/6 | 0/6 | 0/6 |
| T08 耐水与防水等级 | 6/6 | 2/6 | 2/6 | 2/6 |
| 合计 | 48/48 | 15/48 | 15/48 | 15/48 |

正式入口为`F:/AmazonESBestseller/运行程序.ps1 → scripts/amazon_es.py → F:/AmazonESBestseller/src`。历史5500任务`run.py`仍显式指向`.releases/action5500-main6c1cf342-compat/src`。核对两个实际src目录，未依赖全局editable安装的假设。

部分测试通过说明旧版本具备部分既有行为，不能说所有相关实现都不存在；每组存在真实失败，才是“未完整包含修复”的依据。

复现命令：

```powershell
python -B scripts/verify_eight_repair_versions.py --output <新的非生产输出目录>
python -B scripts/repair_detail_runtime.py --task-root F:\AmazonESBestseller\runtime\action_detail5500_live_20261023_161301_8b4e --output <新的非生产输出目录>
```

## 证据与交付状态

- 缓存离线重放和完整5500对账：`C:/Users/Administrator/.codex/tmp/amazon-runtime-repair-20261027-verified/reconciliation.json`，逐文件保全凭据`source_hashes.json`，最终验收摘要`final_verification.json`。
- 最新任务脚本副本：同目录`staged_runtime`；`reconciliation.json/staged_runtime/files`记录hash。原任务脚本不覆盖。已执行`--validate-only`验证原冻结计划/授权，未调用main采集路径。早期临时副本已被该最终目录替代。
- 四版本矩阵：`C:/Users/Administrator/.codex/tmp/eight-repair-matrix-20261027-v1/version_matrix.json`及四份JUnit/log。main和旧运行版失败为预期核查发现，不能充作修复树测试失败，也不能对外宣称已部署。
- 未提交PR、未合并main、未更新F盘生产程序。下一步应先将通过验收的独立变更纳入PR，再按明确范围更新实际运行包；不能把工作树测试通过当作main或生产程序已修好。

## 本轮最终测试结果

- **最终同一次定向验收：216通过，0失败，0跳过。** 覆盖读写与真实Win32锁、正式计划写入、checkpoint/resume、真实保存详情页、缓存正反向回归、详情执行器、任务编排、CLI/导出离线链、API停用、离线注入适配器和T01—T08。JUnit：`C:/Users/Administrator/.codex/tmp/amazon-runtime-repair-final-acceptance-20261027.xml`，同名log保存终端结果。此前136/26/48等分组测试不重复相加。
- 修订脚本的实际progress函数另以真实5500冻结任务执行：覆盖5500，成功5412；每次进度只写汇总、不把5500条记录反复追加到日志（验证快照1051字节）。执行实际paced_goto函数的失败边界，证明确认持久化失败后不进入goto，网络请求0。
- `--validate-only`原冻结计划/授权核验通过；CLI、multisource与V4帮助入口检查通过；src和两个新增工具脚本语法解析通过；`git diff --check`无错误。
- 原有45个修改/未跟踪文件逐一SHA-256比对不变，包括此前8项修复；读取的6091个生产源文件前后hash一致。新代码、测试、文档只在独立工作树，验证产物只在独立临时输出目录。
- 扩大到全仓的额外回归运行至既有高速快照测试时耗时较长，已主动终止，**全仓最终回归状态为未完成，不宣称通过**。该轮还发现一条旧CLI测试仍要求translate-ds执行API；已按停用新契约修改，并纳入上述216项最终验收通过。日志保留在`C:/Users/Administrator/.codex/tmp/amazon-runtime-repair-full-20261027.log`。
- 未运行GitHub CI；没有PR/main合并、部署或正式生产验收。本轮用户要求的两项修复及8组版本核对已完成，全面合并/部署仍是后续独立步骤。
