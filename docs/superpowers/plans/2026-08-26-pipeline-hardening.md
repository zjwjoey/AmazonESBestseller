# Amazon.es Pipeline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 修复详情身份、访问门禁、价格/月购、QA、人工备注、Excel CLI 输入和项目安装链路中的高风险缺口，同时保持三表契约与低频串行策略不变。

**Architecture:** 在现有 collection → normalization → qa → export 模块边界内做最小加固。采集层保留 HTML 证据并在解析前完成访问状态与 ASIN 一致性校验；规范化/QA 层只基于原始可见证据生成值；CLI 负责把显式图片目录和类目规划文件转换为现有 exporter 接口。

**Tech Stack:** Python 3.12、BeautifulSoup4/lxml、Playwright、openpyxl/Pillow、pytest、setuptools。

---

## 文件地图

- Modify src/amazon_es_bestseller/collection/detail.py：详情页最终 ASIN、缓存访问状态、划线原价和月购买量。
- Modify src/amazon_es_bestseller/collection/ranking.py：榜单页可见月购买量。
- Modify src/amazon_es_bestseller/access/detector.py：常见挑战/限流文案识别。
- Modify src/amazon_es_bestseller/qa/validators.py：价格关系、月购严重度和 attributes 冲突证据。
- Modify src/amazon_es_bestseller/export/excel.py：备注合并非覆盖语义。
- Modify src/amazon_es_bestseller/cli.py：图片目录和类目规划输入。
- Modify pyproject.toml、README.md 以及 docs/CURRENT_STATE.md、docs/ROADMAP.md、docs/ARCHITECTURE.md。
- Create tests/test_detail_collection.py 和三个离线 HTML fixture。
- Modify tests/test_detail_parser.py、tests/test_ranking_parser.py、tests/test_qa_validators.py、tests/test_excel_export.py、tests/test_cli.py。

### Task 1: 详情解析与采集门禁的失败测试

**Files:**
- Create: tests/test_detail_collection.py
- Create: tests/fixtures/html/product_redirect.html
- Create: tests/fixtures/html/product_blocked.html
- Create: tests/fixtures/html/product_monthly_bought.html
- Modify: tests/test_detail_parser.py

- [ ] **Step 1: 写解析回归测试**

增加以下测试，先让它们失败：

~~~python
def test_struck_price_excludes_unit_price():
    html = '''
    <div id="corePrice_feature_div">
      <div class="a-price"><span class="a-offscreen">14,99 €</span></div>
      <span class="a-text-price"><span class="a-offscreen">3,04 EUR/kg</span></span>
    </div>
    <div id="corePriceDisplay_desktop_feature_div">
      <span class="a-text-price" data-a-strike="true">
        <span class="a-offscreen">19,99 €</span>
      </span>
    </div>'''
    parsed = parse_detail_page(html, "B078C6QR1C")
    assert parsed["current_price_raw"] == "14,99 €"
    assert parsed["original_price_raw"] == "19,99 €"

def test_monthly_bought_is_preserved():
    html = '<div id="social-proofing-faceout">1,5 mil+ comprados el mes pasado</div>'
    assert parse_detail_page(html, "B078C6QR1C")["monthly_bought_raw"] == "1,5 mil+"
~~~

- [ ] **Step 2: 写采集门禁测试**

定义 FakeSession，提供 goto、page.content、page.url、wait_for_product_page、wait_for_price_text、wait_between_requests。增加两个断言：

~~~python
def test_collect_details_stops_on_final_asin_mismatch(tmp_path, redirect_html):
    session = FakeSession(200, redirect_html,
                          "https://www.amazon.es/dp/B075JJRFVV")
    with pytest.raises(AccessStopError, match="ASIN"):
        collect_details(["B078C6QR1C"], session, str(tmp_path))
    assert (tmp_path / "html" / "B078C6QR1C.html").exists()
    assert not (tmp_path / "details.json").exists()

def test_collect_details_rechecks_cached_blocked_page(tmp_path, blocked_html):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "B078C6QR1C.html").write_text(blocked_html, encoding="utf-8")
    with pytest.raises(AccessStopError):
        collect_details(["B078C6QR1C"], FakeSession(), str(tmp_path))
~~~

redirect fixture 的最终页面 ASIN 是 B075JJRFVV；blocked fixture 不含 CAPTCHA，但含 Sorry, we just need to make sure you're not a robot。

- [ ] **Step 3: 运行失败测试**

~~~powershell
$py='C:\Users\zhongzhong\.codex\venvs\amazon-es-bestseller-py312\Scripts\python.exe'
& $py -m pytest tests/test_detail_parser.py tests/test_detail_collection.py -q -o addopts=''
~~~

预期：新断言失败，原因是当前没有严格划线价、monthly_bought_raw、最终 URL 和缓存门禁。

- [ ] **Step 4: 提交测试与 fixture**

~~~powershell
git add tests/test_detail_parser.py tests/test_detail_collection.py tests/fixtures/html
git commit -m "test: lock detail identity price and monthly evidence"
~~~

### Task 2: 实现详情/榜单证据提取和访问状态

**Files:** src/amazon_es_bestseller/access/detector.py、collection/detail.py、collection/ranking.py；Tests: tests/test_detail_parser.py、tests/test_detail_collection.py、tests/test_ranking_parser.py。

- [ ] **Step 1: 扩展 detector 文案规则**

在前 300 字符检查中加入 robot check、access denied、unusual traffic、rate exceeded 等大小写不敏感信号，命中仍返回 CHALLENGE，不增加绕过或重试。

- [ ] **Step 2: 实现两个纯解析 helper**

detail.py 增加 _monthly_bought_raw(soup) → str：从 #social-proofing-faceout、.social-proofing-faceout 和页面文本提取“数字[mil|k]?+ comprados el mes pasado”，只返回数字、单位和加号。增加 _struck_price(soup) → str：只接受自身或祖先带 data-a-strike="true" 的 a-text-price 中的 a-offscreen。

- [ ] **Step 3: 接入返回值**

parse_detail_page 返回 monthly_bought_raw；parse_bestsellers_page 对每个 gridItemRoot 记录同名字段，缺失时为空字符串。

- [ ] **Step 4: 统一网络和缓存门禁**

网络路径写 HTML 后执行 detect_access_status → require_normal_access，再用 session.page.url 调用 verify_asin_on_page；不一致抛 AccessStopError，HTML 保留。写 html/<ASIN>.meta.json，包含 status_code、final_url、access_state。缓存路径优先读取 sidecar；旧缓存没有 sidecar 时按未知证据重新联网，不直接当正常页。缓存 HTML 同样执行完整 detector 和最终 ASIN 校验。只有通过两项校验后才 parse_detail_page。

- [ ] **Step 5: 运行测试并提交**

~~~powershell
& $py -m pytest tests/test_detail_parser.py tests/test_detail_collection.py tests/test_ranking_parser.py -q -o addopts=''
git add src/amazon_es_bestseller/access/detector.py src/amazon_es_bestseller/collection/detail.py src/amazon_es_bestseller/collection/ranking.py tests
git commit -m "fix: enforce detail identity and capture monthly evidence"
~~~

### Task 3: 规范化与 QA

**Files:** src/amazon_es_bestseller/qa/validators.py；Tests: tests/test_qa_validators.py、tests/test_pipeline.py。

- [ ] **Step 1: 写失败测试**

~~~python
def test_validate_price_rejects_present_equal_original():
    status, issues = validate_price(14.99, 14.99, "EUR", None)
    assert status == QAStatus.FAIL
    assert any(i.code == "PRICE_INVALID" for i in issues)

def test_validate_monthly_bought_inconsistency_is_warning():
    status, issues = validate_monthly_bought({
        "monthly_bought_raw": "100+", "monthly_bought_min": 50})
    assert status == QAStatus.WARN
    assert issues[0].severity == "P2"

def test_validate_source_conflict_reads_attributes():
    status, issues = validate_source_conflict({
        "title_es_raw": "Fiambrera reutilizable",
        "attributes": [{"label_raw": "Tipo", "value_raw": "Tamper"}],
        "product_type": "reusable_container"})
    assert status == QAStatus.SOURCE_CONFLICT
    assert issues[0].code == "SOURCE_CONFLICT"
~~~

- [ ] **Step 2: 运行失败测试**

~~~powershell
& $py -m pytest tests/test_qa_validators.py -k "equal_original or monthly_bought_inconsistency or source_conflict_reads_attributes" -q -o addopts=''
~~~

- [ ] **Step 3: 实现最小修复**

validate_price 在 original_price 非空且可解析时要求 orig > cur，否则添加 P1 PRICE_INVALID。validate_monthly_bought 对 P2 问题返回 _worst_status(issues)，结果为 WARN。validate_source_conflict 在 details_json、summary_v2、spec_v2 都为空时序列化 attributes 作为证据。

- [ ] **Step 4: 运行回归并提交**

~~~powershell
& $py -m pytest tests/test_qa_validators.py tests/test_pipeline.py tests/test_regressions.py -q -o addopts=''
git add src/amazon_es_bestseller/qa/validators.py tests/test_qa_validators.py tests/test_pipeline.py
git commit -m "fix: align price and monthly QA severity"
~~~

### Task 4: 人工备注和导出输入

**Files:** src/amazon_es_bestseller/export/excel.py、src/amazon_es_bestseller/cli.py；Tests: tests/test_excel_export.py、tests/test_cli.py。

- [ ] **Step 1: 写备注冲突测试**

~~~python
def test_merge_manual_fields_keeps_new_nonempty_note(export_records):
    prev = export_workbook(export_records)
    new_records = [dict(r) for r in export_records]
    new_records[0]["备注"] = "本次人工复核"
    merged = merge_manual_fields(new_records, prev)
    assert merged[0]["备注"] == "本次人工复核"
~~~

- [ ] **Step 2: 修复备注合并**

将合并条件改为只在 _notes_of(rec) == "" 时回填旧值；已有新备注永不覆盖。

- [ ] **Step 3: 写 CLI 图片/类目规划测试**

创建合法 PNG、images/<ASIN>.png 和 planning.json，调用 export --images-dir --category-planning --force，断言类目规划有数据、中文表有一张图片、西语表无图片。不存在的图片目录仍应成功导出并保留图片链接。

- [ ] **Step 4: 实现 CLI 输入**

增加 _load_category_planning(path)，要求顶层 list；增加 _load_images_by_asin(directory, records)，按 ASIN 查找 png/jpg/jpeg，读取为 BytesIO、70×70，缺失文件跳过并提示。export 子命令注册两个可选参数，并将结果传给 export_workbook 的 images_by_asin/category_planning。

- [ ] **Step 5: 运行回归并提交**

~~~powershell
& $py -m pytest tests/test_excel_export.py tests/test_cli.py -q -o addopts=''
git add src/amazon_es_bestseller/export/excel.py src/amazon_es_bestseller/cli.py tests/test_excel_export.py tests/test_cli.py
git commit -m "fix: preserve notes and support export inputs"
~~~

### Task 5: 包元数据和文档

**Files:** pyproject.toml、README.md、docs/CURRENT_STATE.md、docs/ROADMAP.md、docs/ARCHITECTURE.md。

- [ ] **Step 1: 补齐 pyproject.toml**

加入 setuptools build-system、project name/version/description/requires-python、运行依赖 beautifulsoup4、lxml、openpyxl、Pillow、playwright，测试可选依赖 pytest，以及 project.scripts：

~~~toml
[project.scripts]
amazon-es = "amazon_es_bestseller.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
~~~

保留现有 pytest 配置与 tests 路径。

- [ ] **Step 2: 更新 README**

记录 pip install -e .[test]、显式 python -m playwright install chromium、离线链路、RUN_LIVE=1 门禁和不提供验证码/代理绕过。

- [ ] **Step 3: 对齐三份状态文档**

删除“CLI 尚未统一”“正式回归最薄弱”等过期陈述；明确历史采集产物不等于可复现实验；统一备注字段为 备注；不能同时声称家居验证已完成和未完成。

- [ ] **Step 4: 安装和测试验收**

~~~powershell
$venv=Join-Path $env:TEMP 'amazon-es-package-check'
py -3.12 -m venv $venv
& "$venv\Scripts\python.exe" -m pip install -e . --no-deps
& "$venv\Scripts\amazon-es.exe" --help
& $py -m pytest -q -rs -o addopts=''
& $py -m amazon_es_bestseller.cli export --help
~~~

预期：入口帮助成功，离线测试全部通过，live 测试仅因未设置 RUN_LIVE=1 跳过。

- [ ] **Step 5: 提交文档和元数据**

~~~powershell
git diff --check
git status --short
git add pyproject.toml README.md docs/CURRENT_STATE.md docs/ROADMAP.md docs/ARCHITECTURE.md
git commit -m "build: package pipeline and reconcile documentation"
~~~

### 最终验收清单

- [ ] git status --short 为空，且未修改 .worktrees/reconnaissance 中的采集产物。
- [ ] 全量离线 pytest 通过；默认不联网。
- [ ] pip install -e .[test] 后 amazon-es --help 可用。
- [ ] 重定向、缓存挑战、单位价、月购、价格关系、备注冲突和图片/类目导出均有回归覆盖。
- [ ] 三张工作表名称、顺序和固定列顺序不变；图片只嵌入中文表。
- [ ] 未执行真实 Amazon 访问。
