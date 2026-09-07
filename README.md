# AMC 周末排片

网站：<https://jackiethai2023-oss.github.io/amcmovie/>

查询未来 12 周的周末及美国节假日排片，按三个厅型展示：

- Century City IMAX
- Century City Dolby Cinema
- Universal CityWalk IMAX

## 数据与更新

默认通过普通 Playwright 浏览器访问 Fandango 的公开影院页面，读取页面自身加载的结构化排片。影院、请求日期、返回日期及每个场次的厅型都会校验。Century City 的 IMAX 和 Dolby 复用同一次影院/日期请求，避免重复抓取。

- [AMC Century City 15 — Fandango](https://www.fandango.com/amc-century-city-15-aaaoz/theater-page)
- [Universal Cinema, an AMC Theatre — Fandango](https://www.fandango.com/universal-cinema-an-amc-theatre-aaawx/theater-page)

页面保留 AMC 官方购票链接，并标明排片来源。只列出来源中可以购票且未过期的场次；售罄或不可购买的场次不会被误标为“即将开售”。没有来源评分时不显示猜测的评分。远期日期可能尚未列出场次。影院将午夜后的场次归入前一营业日时，网站用 `(+1 day)` 标明实际时间在次日。

GitHub Actions 按 UTC `00:00–06:00`、`13:00–23:00` 每小时计划运行。实际启动时间可能被 GitHub 延迟；网站显示最后一次成功抓取时间。`RELOAD` 按钮重新读取已发布数据，不会启动爬虫。

## 失败保护

抓取失败与成功但没有场次是两个不同状态。HTTP 错误、访问拦截、超时、日期/影院不匹配、未知响应结构均作为失败处理，不会直接转换为“尚未公布”。访问拦截会立即终止当前运行，不进行绕过或反复重试。

只有所有目标查询均通过校验时，才发布新的排片快照和成功时间。失败时：

1. 保留上一次成功的 `showtimes.json` 和 `last_updated.json`。
2. 单独更新 `crawl_status.json`，记录失败和上次成功时间。
3. Actions 显示失败；网页提示正在展示旧数据或当前无法获取数据。
4. 超过 48 小时没有成功抓取时，网页也会提示数据可能过期。

## 文件

```text
crawler/scraper.py        日期、发布保护与抓取入口；保留旧 AMC 解析器供诊断
crawler/fandango.py       默认排片来源与严格字段校验
.github/workflows/crawl.yml
index.html
requirements.txt
tests/test_scraper.py
tests/test_fandango.py
tests/frontend_health.test.cjs
data/showtimes.json
data/last_updated.json
data/crawl_status.json
```

`showtimes.json` 保留原有 `影厅 → dates → 日期 → movies` 结构，影厅对象附有 `source` 和 `source_url`。

`crawl_status.json` 的主要字段为 `status`（`ok`、`partial`、`error`）、`attempted_at`、`last_successful_at`、`stale`、`message`、`source`、`successful_requests` 和 `failed_requests`。计数按影厅/日期查询统计，已缓存查询不会重复访问来源。

## 本地运行和验证

使用 Python 3.11 或 3.12：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python -m unittest discover -s tests -v
node --test tests/frontend_health.test.cjs
python crawler/scraper.py
python -m http.server 8000
```

浏览器打开 `http://localhost:8000`。爬虫返回码 `0` 表示整次抓取通过；非零表示本次没有替换排片数据。

## 部署

GitHub Pages 使用 `main` 分支根目录。Actions 需要 `contents: write` 和 `pages: write`（已在工作流声明）。提交数据或失败状态后，工作流会明确请求 Pages 重建，并确认网站发布的提交与本次更新一致。手动更新：进入 Actions → **Crawl AMC Showtimes** → **Run workflow**。

发布后应同时检查 Actions 结果、线上 `crawl_status.json` 的成功时间，以及网站实际显示的电影/厅型/日期。不能仅凭页面 HTTP 200 或工作流绿灯判定数据正常。
