# 程序待修复与验收清单：10月20日至25日运行问题回看

更新日期：2026-10-27，时间口径为北京时间。

本清单把用户要求登记的8项程序缺陷，以及2026-10-20 00:00至10-26 00:00期间的运行问题放在一起管理。它是后续修复与验收台账，不表示所有项目现在都未修，也不把来源异常、正常访问停止或单个商品失败都算成程序bug。

共37个台账条目：8项已有修复记录、26组快照/详情/预清洗历史问题、3项运行收尾核实事项。分组可能共享底层原因，37不是独立根因数量、发生次数或受影响SKU数量。

## 当前基线与状态含义

- 本次通过`git ls-remote origin refs/heads/main`确认远端main为`ce74a968b16a9168656494b50bfd9907b99fb667`，与本地origin/main一致。
- 当前独立工作树HEAD为`521b35c468ff2beaf0d8932d0e5c3e1a950c2fa0`，已有未提交修改全部保留。
- `d4ad1a3d`复杂容量后续修复、`521b35c4`翻译来源绑定修复均不在当前远端main祖先链中。
- 详情任务脚本目前显式导入`.releases/action5500-main6c1cf342-compat`；该发布包与最新main、当前独立工作树不能视为同一版本。没有执行入口切换或部署。
- **本地已修待整合**：有实现、fixture和测试，但尚需把对应变更及回归完整纳入main并验证运行入口。
- **main已有修复**：Git历史证明修复进入main；不自动等于某个历史任务私有发布包已更新。
- **运行包有验证**：有指定发布包、指定任务的部署或恢复记录；其结论仅覆盖该版本与当时范围。
- **待根因核对/待对账**：现象和证据已确认，尚不能宣称根因、代码修复或恢复已经验收。
- P1用于数据正确性、任务可靠性和发布阻塞；P2用于误报、可观测性和性能。目前没有证据据此宣布新的P0，也没有宣布生产验收通过。

## 一、登记刚才的8项程序缺陷

其中T01—T04在10月25日复核记录中已有证据；T05—T08在10月26日的后续复核中确认。后四项针对此前形成的数据，但不能冒称它们在10月20—25日的现场日志中就已被发现。

这8个对应测试文件目前都不在远端main中。本次在独立工作树重新运行8组定向测试，**48项通过，0失败、0错误、0跳过**。测试文件缺失说明回归尚未完整纳入main，不能仅凭文件名断言main绝无其他等效实现。

| ID | 优先级 | 程序缺陷及后果 | 当前状态 | 后续验收 |
|---|---|---|---|---|
| T01 | P1 | 混合尺寸解析：`140 cm x 22 mm`被后续数量/规格回退误读，丢失每轴单位或把22当件数 | 本地已修待整合；10/25记录 | 保留轴顺序与各轴单位；数量不得从尺寸尾轴生成 |
| T02 | P1 | 否定材质标签：`Sin tipo de material`曾映射为正向材质，反转“不含”含义 | 本地已修待整合；10/25记录 | 正向材质、不含材质、未知编码分开；原西语不变 |
| T03 | P1 | 混合单位QA只取最后一个单位，可能漏掉`140厘米×22毫米`被译成`140毫米×22毫米` | 本地已修待整合；10/25记录 | 逐轴校验；共享尾部单位的合法写法仍可通过 |
| T04 | P2 | 制造商/品牌原名含de、para等词，被审查误报为未翻译 | 本地已修待整合；10/25记录 | 只豁免同ASIN明确来源身份值；普通未译句仍拦截 |
| T05 | P1 | `40x70 Pack 6`曾把尺寸尾轴70识别为70包，而不是来源明确的6条/包数 | 本地已修待整合；10/26确认 | 先识别尺寸边界；保留选中包装证据优先级，不给缺单位尺寸猜单位 |
| T06 | P1 | 单数`Número de pieza`零件编号与复数件数混用，型号可能变成几百万件 | 本地已修待整合；10/26确认 | 编号保持字符串、前导零不丢；不覆盖真实件数 |
| T07 | P1 | 尺寸轴缩写与单位混淆：f./Grosor映射、保护层、中文轴向QA衔接不一致 | 本地已修待整合；10/26确认 | 数值、轴向、显式单位同时匹配才补译；不把深/厚当厘米或升 |
| T08 | P1 | `Resistente al agua`被强化为“防水”，字典与后处理边界不一致 | 本地已修待整合；10/26确认 | 只修对应属性的耐水等级；保留其他明确Impermeable声明，不整品投票覆盖 |

对应回归文件：

- `tests/test_specification_mixed_axis_units.py`
- `tests/test_translation_material_exclusion_label.py`
- `tests/test_translation_mixed_axis_validator.py`
- `tests/test_field_closure_identity_residual.py`
- `tests/test_specification_dimension_pack_boundary.py`
- `tests/test_specification_part_number_count.py`
- `tests/test_translation_dimension_axis_display.py`
- `tests/test_translation_water_resistance.py`

来源报告：

- [10/25待核标记复核](F:/AmazonESBestseller/runtime/translation_v2/luna/review1288-20261025_194127/reports/REVIEW1288_COMPLETION.md)
- [10/25联合复核](F:/AmazonESBestseller/runtime/translation_v2/luna/joint4646-20261025_221155/reports/JOINT_REVIEW_COMPLETION.md)
- [10/26数量与零件号复核](F:/AmazonESBestseller/runtime/translation_v2/luna/continue1136-20261026_031243/reports/REMAINING1136_REVIEW_REPORT.md)
- [10/26尺寸轴向复核](F:/AmazonESBestseller/runtime/translation_v2/luna/review1088-20261026_072658/reports/REVIEW1088_REPORT.md)
- [10/26耐水复核](F:/AmazonESBestseller/runtime/translation_v2/luna/closure1074-20261026_084053/reports/CLOSURE1074_REPORT.md)

## 二、10/20—25快照阶段的问题

10/20以主线整合和离线闭环为主；10/21—22存在本轮新快照运行证据。以下不能与更早的旧30,000计划或10/18旧任务混用。

| ID | 优先级 | 当时的问题 | 已有证据/处理状态 | 仍需做什么 |
|---|---|---|---|---|
| S01 | P1 | 配送目的地不是西班牙/无法确认时，邮编UI与会话成功缓存导致门禁行为不符合预期 | 快照逐页配送证据及#17的重新检查修复已进入main；详情弹窗延迟另见D05 | 用当前页目的地和正常邮编UI验证；不能只信历史会话成功或页脚España |
| S02 | P1 | URL查询参数换序被误当新/非法请求，造成停止或重复请求风险 | `91029087`修复；随#16进入main | 保存页重放证明只接受精确等价查询；不重复已完成来源页 |
| S03 | P1 | 喜爱榜早期数量很少，调度与页面预算不能保证最低3000 | `c3fece53`防饥饿、`42544953`明确预算追加；#16包含修复 | 分来源最低量与全局目标共同验收；预算变更不能重置累计请求和旧授权 |
| S04 | P1 | 正常空榜单页被当异常页面，整批提前停下 | `19ee79ae`识别有证据的官方空页；#16进入main | 空页需真实DOM/收据支持；未知页、Challenge、403/429不可当空页放行 |
| S05 | P1 | 旧版阶段门禁套到新PAGE快照，已审查结果仍显示不准确 | `1ac784dd`及#16；独立snapshot_review/acceptance契约已存在 | 审查覆盖与数量门槛同时通过；快照通过不能变成商品身份或生产验收通过 |
| S06 | P2 | 断点目标、已完成恢复、进度/耗时监控使用旧目标或状态，可能多请求一页或结束后误报警 | `c5cca41b`及#16；相关监控说明已进入main | 完成任务恢复不发新请求；重新采集前作废旧审查结果；用真实目标和持久化时钟计算 |

收尾证据：20k运行保存的最终保守候选组为20,018，喜爱榜3,013；后续10k运行最终12,265组、喜爱榜3,008。后者为三个最低量同时达标后的实际停止结果，不能单凭超过10,000判为重复提取或程序失控。两者均标记商品身份未验证、生产验收未评估。

来源：[`SNAPSHOT_MAIN_INTEGRATION.md`](../SNAPSHOT_MAIN_INTEGRATION.md)、[`SNAPSHOT_REVIEW_STATUS.md`](../SNAPSHOT_REVIEW_STATUS.md)、[`SNAPSHOT_MAIN_PRIORITY.md`](../SNAPSHOT_MAIN_PRIORITY.md)，以及两个运行目录的`store/snapshot_progress.json`。

## 三、详情采集、字段漏提及审查问题

| ID | 优先级 | 当时的问题 | 已有处理/证据 | 后续验收 |
|---|---|---|---|---|
| D01 | P1 | URL身份标准化误报；500件详情试跑中已有保存页被错误隔离 | `668d6122`及#19；57页离线复核，1件无需请求恢复 | 等价URL可通过，主商品强身份证据冲突仍必须拦截；成功项不重采 |
| D02 | P1 | 推荐/其他商品ASIN被当成当前商品强身份，产生身份误报 | #20明确主商品强证据与弱观察；候选交接共享规则 | 不能把历史63条asin_mismatch全部认定误报；按原页强证据逐项验证当前运行包 |
| D03 | P1 | 详情布局覆盖不足，动态属性、A+、desktop面包屑等可能漏提/丢来源 | #17、#20、#22进入main；保留全量原属性及分区证据 | 页面明确有的要保留；推荐区/跨商品A+不得串入；822页中725含desktop区域不等于725件都漏提 |
| D04 | P2 | 不同section的局部position重复，被审查成重复编号/重复采集 | `668d6122`及#19；后续698复核对233件、364组重复事实核对到不同DOM行 | position按section解释；来源真实重复与程序重复提取分开，不删原始行 |
| D05 | P1 | 配送弹窗延迟加载失败；1165条喜爱榜候选缺少详情计划认可的身份交接 | `3f1db326`及任务私有修复包；73+11项历史回归，候选链接身份核验 | 弹窗按实际加载检查；候选不伪造AUTHORITATIVE/IDENTITY_COMPLETE；正式详情仍ASIN键控 |
| D06 | P2 | 三窗口变两窗口/空闲：按类目独占分配不满足本轮同类目多窗口领取要求 | 明确用户授权后改为共享ASIN队列；三路继续、重复领取与已成功重采均为0的恢复记录 | 这是本轮授权模式，不把零类目冷却和同类目并行设为全局默认；保留请求间隔及受限窗口保留 |
| D07 | P1 | 单商品正常失败、超时或已确认商品404导致不必要的整批停止 | #20单商品失败边界；`43fb4410`区分有充分证据的商品不存在404，三路恢复记录 | 404不能算成功/无限重试；与403、429、Challenge、证据落盘失败严格分开 |
| D08 | P1 | 卖家漏提：TurboCheckout脚本和CRLF/LF差异导致BuyBox被吞入脚本文本 | #24已进入main；698页复核及`main_6c1cf342_20261024.json`有运行版更新证据 | 两种换行都测试；卖家只从当前报价的明确控件恢复，配送方不能冒充卖家 |
| D09 | P1 | Ring比较表标准属性为空，当前型号的5条规格漏提 | #24已进入main，同上运行版有兼容验证 | 唯一current列且同ASIN才恢复；相邻型号/图标未知值不能猜填 |
| D10 | P1 | 核心规格交接丢容量/包装数，被通用技术字段覆盖 | #24已进入main，涉及0.75L被4500ml覆盖、15件未保留等真实案例 | 选中规格、精确标题与技术属性分清；保留原始属性，不用泛化数量覆盖明确选中值 |
| D11 | P1 | 114条无效划线原价来自其他变体swatch的价格区域 | #25已进入main；114份保存页均复现；当前价保持，错误原价/折扣移除 | 仅当前商品有效划线价可用；无原价时留空，不重建原价 |
| D12 | P1 | 页面异常容量单位/1×1×1占位尺寸进入核心规格；纯数字选中值被冲突标题覆盖 | #25及#26进入main；原始异常不删除 | 正确质量容量仍保留；纯数字选中值只可由相同单值标题补单位，不用不同值/范围/多轴替换 |
| D13 | P1 | 详情刚结束的来源审查误查中文/后处理字段；局部审查被当整阶段通过；缺失与漏提混同 | #19、#24及POST_DETAIL_AUDIT分source/translation/export与PARTIAL_REVIEW | 来源缺失不猜填，部分检查不得宣称全阶段READY；新品/喜爱榜不能拿Detail BSR补畅销榜排名 |
| D14 | P2 | 同一HTML重复建DOM/身份扫描增加处理耗时 | #20/21及`f5ffbf95`已有优化；44页总解析91.169→62.584秒，任务6页两轮41.322→36.645秒 | 两个基准范围不同，不能相加或当线上提速；后续按真实主耗时再决定优化 |

状态依据优先级：当前远端main提交历史 > 指定发布包验证 > 当时报告中的PR草稿状态。例：`latest_remaining_field_review.json`仍记PR #25未合并，但Git主线已有`875ea2c8`，不能继续沿用旧指针宣称尚未合并。

详细来源：

- [`DETAIL_FIELDS_698_REVIEW_20261024.md`](DETAIL_FIELDS_698_REVIEW_20261024.md)
- [`REMAINING_FIELDS_20261024.md`](REMAINING_FIELDS_20261024.md)
- [`POST_DETAIL_AUDIT.md`](../POST_DETAIL_AUDIT.md)
- [`DETAIL_EXTRACTION_OPTIMIZATION_P1_P4.md`](../DETAIL_EXTRACTION_OPTIMIZATION_P1_P4.md)
- [500件详情离线恢复记录](F:/AmazonESBestseller/runtime/detail500_live_20261022_192705_0564/repair_applied.json)
- [5500任务交接修复包](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/fix_release.json)
- [三路共享领取验证](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/shared_queue_verification.json)
- [404恢复验证](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/404_resume_verification.json)
- [main6c运行版离线更新凭据](F:/AmazonESBestseller/runtime/deployments/main_6c1cf342_20261024.json)

## 四、预清洗及翻译入口的程序问题

| ID | 优先级 | 程序问题 | 当前代码状态 | 后续验收 |
|---|---|---|---|---|
| P01 | P2 | 普通西语B开头词、分类/顾客描述被误判ASIN或元数据 | #27已进入main | 只认实际身份语法与明确元数据标签，不能按单词片段删正常卖点/描述 |
| P02 | P1 | l./L×A×A被当升；单位等值、轴向、年龄单复数、形状等价处理不一致 | #27/#28已有修复；10/25更窄规则仍须核实纳入main | 按类型与对象比较，不重排轴、不用容差掩盖差值；吞吐率不是容器容量 |
| P03 | P1 | 多部件容量、单件/总量、上下水箱、最低/推荐/最大灌装量混比；紧凑3x320ml等计数丢操作数 | #27/#28覆盖部分；`e556cdc2`、`d4ad1a3d`后续不在当前main祖先链 | 只接受来源明确角色、数量及精确Decimal算式；3×933不当2800，700+310不当1L；不凭规则补造总量 |
| P04 | P2 | 精确重复卖点清理后仍阻止准入；重复发生次数与涉及SKU数混计 | #27已进入main | 规范化派生列表、保留重复事实来源；仍拦截空/损坏内容；分开报告次数和商品数 |
| P05 | P1 | 展平属性标签内冒号丢边界；通用尖括号正则误删<300、>399及标识符 | #27已进入main | 只在完整标签与原属性绑定时恢复；只删真实HTML元素，不删比较符或标识符 |
| P06 | P1 | Pre-Clean重新选旧别名，或原文hash/清洗文本hash、商品/字段绑定混淆，可能拒绝合法结果或错误接纳不匹配结果 | 修复提交`521b35c4`及本地增量存在，未进入当前远端main | 保留原文hash和清洗输入hash；重放选定来源；同文本hash不得授权另一ASIN/字段；不得自行改READY绕过校验 |

来源：[`PRECLEAN_1988_REVIEW_20261024.md`](PRECLEAN_1988_REVIEW_20261024.md)、[`PRECLEAN_TYPED_CONTEXT_20261024.md`](PRECLEAN_TYPED_CONTEXT_20261024.md)、[`PRECLEAN_REMAINING1117_20261025.md`](../qa/PRECLEAN_REMAINING1117_20261025.md)、[`PRECLEAN_CAPACITY816_20261025.md`](../qa/PRECLEAN_CAPACITY816_20261025.md)，以及`521b35c4`差异与`tests/test_translation_production_source_binding.py`。

## 五、本次回看新增的三个优先核实事项

### R01 / P1：Windows文件读写拒绝导致工作窗口退出

**已确认现象，不先猜根因。** 5500任务日志出现：

- 读取`plans/0029/detail_plan.json`与`plans/0520/detail_plan.json`触发PermissionError，worker进入PROGRAM_STOP，时间为10/23北京时间18:40、18:35附近。
- 多份stderr中，`worker_status.json.tmp → worker_status.json`的`os.replace`触发WinError 5；异常处理期间再次写状态也失败。
- 目前任务`run.py`的write仍使用固定`.tmp`文件名及直接`os.replace`；没有在该函数中看到有限重试或唯一临时名。

**待办**：区分同名临时文件竞争、文件被其他进程打开及其他权限/共享锁原因；保存最小复现。只有已确认的暂时共享冲突才可做有界处理，真实持久化失败仍须停止新任务领取，不得吞错、把checkpoint算成功或无限重试。

**验收**：注入被锁定读取、replace sharing violation、同时状态写入和持久化失败；证明已成功ASIN不重采、异常可见、临时文件不互相覆盖、真正落盘失败仍安全停止。

证据：[原parallel_fixed stderr](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/parallel_fixed_stderr.log)、[最终progress保留历史停止](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/progress.json)、任务`run.py:34—42`及`run_shared.py`。

### R02 / P1：两条缓存复用任务未进入最终分项，88件需闭合对账

最终记录为5,412成功、88未成功，状态PARTIAL；其中outcomes只有22 failed、63 asin_mismatch、1 access_stop，合计86。

另两条已定位：`B07B8D8RC5`与`B0G46PL7RN`在frozen_tasks中动作为`REUSE_VALID_CACHE`，在shared_held_items中为`OFFLINE_OR_REVIEW_ACTION`，当前shared checkpoint目录没有对应文件。共享runner先将非网络动作且未被success判定的任务归入held，而progress只统计outcomes，因此未计入这两条分项。

这证明**收尾分项有覆盖缺口**，并定位了缓存复用交接的检查点；不证明两条缓存一定有效，也不证明采集数据已经丢失。不得将它们直接补算SUCCESS或重发网络请求。

**待办**：离线核验两条原计划的缓存位置、ASIN、schema、保存页与缓存hash；可复用则通过正式离线交接形成有证据的结果，否则明确记录缓存不可复用/待核的原因。进度与最终清单应覆盖全部5500个已冻结任务。

**验收**：5500 = 成功 + 各类非成功终态 + 明确待处理；每个ASIN仅出现一次；无checkpoint的REUSE任务也可被诊断；保留原状态及原计划。

证据：[parallel_result](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/parallel_result.json)、[held items](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/shared_held_items.json)、[冻结任务](F:/AmazonESBestseller/runtime/action_detail5500_live_20261023_161301_8b4e/shared_queue/frozen_tasks.json)，以及`run_shared.py`的held集合和progress计数。

### R03 / P1：修表、修工作树、合并main、更新运行包的状态要逐项闭合

已确认存在不同版本并行：最新main为ce74a968；任务入口指向main6c兼容发布包；独立工作树HEAD为521b35c4并有额外未提交修复。旧JSON里PR #25的未合并状态也已落后于Git主线。

**待办**：为T01—T08、P03/P06以及R01/R02分别记录修复commit、相关离线回归、PR/main状态、实际入口导入路径及发布包hash。已有修复先复核复用，不重写一套。

**验收**：被验收的测试、PR和运行包是同一版本；所用source/preclean/translation schema和缓存hash契约一致。生产入口切换须单独执行验证，不能因本清单生成而自动部署。

## 六、不是直接改程序就能解决的事项

- Challenge、403、429、Robot Check、CAPTCHA仍应按当前授权的访问停止范围处理。500件试跑的ACCESS_STOP、5500任务中的access_stop不能仅因停止而认定为bug；不得增加绕过手段。
- 296件榜单路径与详情路径不同，曾经属于业务展示口径选择。用户已选详情页类目，榜单路径另保留；不能把这296件当296个parser bug。
- 来源容量栏写厘米、异常1×1×1尺寸、相反的可微波/不可微波等事实，不能猜值修平。可修的是错误晋升、错误映射或错误审查，不是改写原始资料。
- 63条历史身份不符、22条失败仍需按保存证据判定，不能因已存在一类身份误报就全解除。
- 三路并行、零类目冷却和同类目共享领取来自本轮明确授权；旧调度默认行为与新任务偏好不同，不全部算程序缺陷。
- 手工处理窗口脚本中还留有IndentationError和TargetClosedError历史stderr。前者属于临时辅助脚本失败；后者可能来自窗口被关闭。没有把它们未经复现就算成正式采集器根因或加入上述37条程序修复条目。

## 执行顺序与本次验证

1. R01与R02优先：确认工作窗口不会因可处理的文件竞争退出，并闭合全部任务的缓存/终态对账。
2. R03、T01—T08、P06：收拢已修代码与测试，完成main和实际运行包的一致性核验；来源绑定通过前不得宣称翻译生产链已正式验收。
3. P03及D类数据正确性回归：检查已合并修复是否出现在实际任务发布包；以保存页复现，真实来源冲突保持待核。
4. P2性能和可观测性项最后处理。已有31.4%及11.3%离线基准不能当作新的全量线上性能保证，不启动新一轮性能重构。

本次仅新增台账、只读核查与8组定向离线测试。48项回归凭据：`C:/Users/Administrator/.codex/tmp/amazon-program-backlog-20261027-tests.xml`。没有重新运行全仓库CI；历史报告中的测试数量不相加冒充本次测试结果。当前Git/审查上下文及原有本地文件哈希保存在`C:/Users/Administrator/.codex/tmp/amazon-backlog-20261027/`。

没有启动Amazon采集、真实翻译API、生产任务或迁移；原始HTML、数据库、checkpoint、冻结队列、缓存、人工备注、当前交付表格及图片均未修改。没有把生成清单视为上述待办已经修复或生产验收通过。

## 10/27后续执行：R01/R02已修，T01—T08完成版本核对

本节是上述初始盘点之后的新进展，不覆盖历史判断。

- **R01：代码已修、离线验证通过，待PR/运行版更新。** 唯一临时文件、原子替换、Windows共享错误有界重试、读取权限失败不作缓存缺失；移除直接截断写回退。真实Win32文件锁与并发写、持续失败保全回归均通过。原任务脚本只生成修订副本，没有覆盖或启动。
- **R02：5500全量分项对账已完成，成功数仍为5412。** 22失败、63身份不一致、1访问停止、2缓存不可复用/身份待核，共88非成功。两条缓存的计划resolved ASIN不同，指定同ASIN页面不存在；正式executor离线重放为PENDING、0请求。不能补算成功。
- **R03/T01—T08：已实际测试四个代码目录。** 修复树48/48通过，origin/main、F盘正式入口、5500私有发布包各15/48通过；每组均有失败，因此8组尚未完整纳入main和实际运行程序。生产入口保持原状。
- 用户本轮允许退出API相关程序：修复树删除Qwen/DeepSeek实际HTTP传输及CLI联网执行，保留历史缓存/账本和离线工具；未删除生产文件或数据。

完整实现、对账清单、逐项版本结果及验收凭据见：[文件读写与缓存修复报告](RUNTIME_IO_CACHE_REPAIR_20261027.md)。本节中的“已修”仅指独立工作树代码及离线验收，尚不表示已经合并main或部署生产。

## 后续主线收尾记录

本文件上文为初始盘点与前轮验收快照。后续按PR #29（容量）、#30（来源绑定）、#31（规格与QA）、#32（运行可靠性与私有包兼容）收尾；37个条目的处理归属、保全范围与最终部署凭据位置见[程序清单收尾报告](PROGRAM_BACKLOG_CLOSURE_20261027.md)。是否已合并/部署以各PR实际状态及该报告指向的本机凭据为准，不能继续用上文旧版本号代表当前运行程序。
