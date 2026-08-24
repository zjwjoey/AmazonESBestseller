# AmazonESBestseller

Amazon.es Best Sellers 第一阶段侦察工具。项目只进行低频、串行的页面探测与有限样本解析，用于判断 `Hogar y cocina` 是否适合作为后续数据源。

## 安装

```powershell
python -m pip install -e ".[test]"
python -m playwright install chromium
```

## 运行

在本项目根目录执行：

```powershell
python -m amazon_es_bestseller.cli run --config config/settings.yaml
```

运行会依次探测首页、Best Sellers 根页和厨房入口页；只有三页均为 `NORMAL` 才会继续最多 3 个真实发现的二级类目，每类最多保留页面自然呈现的 50 条商品，并最多保存 5 个详情页样本。

每次运行产物位于 `runs/YYYYMMDD_HHMMSS/`，包括 HTML、截图、访问事件、类目树、榜单记录、按 ASIN 聚合的商品表、字段可用率和 `report.md`。

## 访问边界

工具不处理验证码、不点击挑战、不轮换代理、Cookie、账号或 User-Agent，不调用私有接口，不并发请求。遇到 403、429、Robot Check、Captcha、访问拒绝或登录要求会立即停止本次网页访问并保存失败证据。

## 测试

```powershell
python -m pytest -v
```

测试使用保存的 HTML fixture，不会访问 Amazon。根目录中已有的历史 CSV、JSON 和脚本不属于本项目，也不会被运行或覆盖。
