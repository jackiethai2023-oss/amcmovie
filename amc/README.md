# AMC电影院周末排片展示

一个使用GitHub Pages + GitHub Actions自动化部署的AMC电影院排片查询网站，零成本运行。

## 功能特性

🎬 实时自动爬取AMC电影院排片信息
📅 显示未来2周的周末排片（周六+周日）
🎯 支持3个影厅：Century City IMAX、Century City Dolby Cinema、Universal CityWalk IMAX
⏰ 每天洛杉矶上午9点自动更新数据
🚀 纯静态前端，无需后端服务
🎨 深色主题UI，AMC品牌色设计
📱 完全响应式布局，支持移动设备

## 项目结构

```
amcmovie/
├── crawler/
│   └── scraper.py              # Python爬虫脚本（requests + BeautifulSoup）
├── data/
│   ├── showtimes.json          # 排片数据（自动更新）
│   └── last_updated.json       # 最后更新时间（自动更新）
├── .github/
│   └── workflows/
│       └── crawl.yml           # GitHub Actions工作流配置
├── index.html                  # 前端页面（纯静态HTML）
├── requirements.txt            # Python依赖
└── README.md                   # 项目文档
```

## 快速开始

### 1. Fork或创建新仓库

```bash
# 创建新仓库 amcmovie（或fork此仓库）
git clone https://github.com/YOUR_USERNAME/amcmovie.git
cd amcmovie
```

### 2. 配置GitHub Pages

在仓库设置中：
1. 进入 **Settings** → **Pages**
2. 选择 **Source**: Deploy from a branch
3. 选择分支: **main** 和目录 **/ (root)**
4. 点击 **Save**

### 3. 启用GitHub Actions

1. 进入 **Settings** → **Actions** → **General**
2. 确保 **Actions permissions** 已启用
3. 允许 **Read and write permissions** for GITHUB_TOKEN

### 4. 手动触发爬虫（可选）

1. 进入 **Actions** 选项卡
2. 选择 **Crawl AMC Showtimes** workflow
3. 点击 **Run workflow** → **Run workflow**

等待爬虫运行完成，刷新主页面即可看到最新数据。

## 工作原理

### 自动更新流程

```
每天洛杉矶上午9点（UTC 16:00）
    ↓
GitHub Actions 触发 crawl.yml
    ↓
运行 Python 爬虫脚本
    ↓
抓取 AMC 网站排片数据
    ↓
生成 data/showtimes.json + data/last_updated.json
    ↓
自动 commit & push 到仓库
    ↓
GitHub Pages 自动更新页面
```

### 前端加载流程

```
用户访问 GitHub Pages URL
    ↓
加载 index.html 静态页面
    ↓
JavaScript 通过 fetch 读取 data/showtimes.json
    ↓
渲染排片信息到页面
    ↓
显示最后更新时间
```

## 爬虫详解

### 数据抓取

爬虫脚本（`crawler/scraper.py`）功能：

- **自动计算周末日期**：从今天起，获取未来14天内所有周六和周日
- **并行抓取**：为3个影厅分别拉取排片数据
- **智能解析**：使用BeautifulSoup解析HTML，提取电影标题和场次时间
- **备选方案**：如果HTML结构变化，自动降级到文本解析

### 影厅映射

| 影厅名称 | URL | 筛选参数 |
|---------|-----|---------|
| Century City IMAX | amc-century-city-15 | imax |
| Century City Dolby Cinema | amc-century-city-15 | dolbycinemaatamcprime |
| Universal CityWalk IMAX | universal-cinema-amc-at-citywalk-hollywood | imax |

### 输出格式

`data/showtimes.json` 结构：

```json
{
  "Century City IMAX": {
    "name": "Century City IMAX",
    "dates": {
      "2026-03-28": {
        "day": "Saturday",
        "movies": [
          {
            "title": "Dune Part Two",
            "showtimes": ["10:00 AM", "1:30 PM", "5:00 PM", "8:30 PM"]
          }
        ]
      }
    }
  }
}
```

## GitHub Actions 配置

### Cron 时间表

```yaml
schedule:
  - cron: '0 16 * * *'  # 每天 UTC 16:00 运行
```

**时区换算：**
- UTC 16:00 = 洛杉矶 PDT 上午9:00（夏令时）
- UTC 17:00 = 洛杉矶 PST 上午9:00（冬令时）

当前默认为 UTC 16:00，如需调整可修改 `.github/workflows/crawl.yml`。

### 手动触发

支持 `workflow_dispatch`，允许在 GitHub Actions 界面手动运行爬虫。

## 前端特性

### UI 设计
- 深色主题（#0a0e27 背景）
- AMC品牌色（#E4002B 红色）
- 现代card卡片设计
- 平滑过渡动画

### 交互
- 支持实时刷新按钮
- 时间转换为洛杉矶本地时间显示
- 错误处理和加载状态
- 完全响应式布局

### 加载方式
```javascript
// 通过 fetch 读取 JSON 文件（相对路径）
const response = await fetch('./data/showtimes.json');
const data = await response.json();
```

## 常见问题

### Q: 为什么我的仓库中看不到爬虫结果？

**A:** GitHub Actions 需要写权限。检查仓库 Settings → Actions → Permissions，确保启用了 "Read and write permissions"。

### Q: 如何修改爬虫运行时间？

**A:** 编辑 `.github/workflows/crawl.yml`，修改 `cron` 表达式。例如：

```yaml
schedule:
  - cron: '0 14 * * *'  # 改为 UTC 14:00
```

### Q: 爬虫失败了怎么办？

**A:** 检查 GitHub Actions 日志：
1. 进入 **Actions** 选项卡
2. 点击最新的 workflow run
3. 查看 **Run crawler** 步骤的输出
4. 常见问题：AMC网站结构变化、网络超时

### Q: 如何本地测试爬虫？

**A:**
```bash
# 安装依赖
pip install -r requirements.txt

# 运行爬虫
python crawler/scraper.py

# 检查输出
cat data/showtimes.json
```

### Q: 前端如何调试？

**A:** 本地开启HTTP服务器：
```bash
# Python 3
python -m http.server 8000

# 访问 http://localhost:8000
```

## 技术栈

- **后端爬虫**：Python 3.11 + requests + BeautifulSoup4
- **前端**：纯HTML5 + CSS3 + Vanilla JavaScript
- **部署**：GitHub Pages + GitHub Actions
- **数据存储**：JSON 文件（存储在git仓库中）

## 成本分析

| 项目 | 成本 |
|------|------|
| GitHub 账户 | 免费 |
| Public 仓库 | 免费 |
| GitHub Pages | 免费 |
| GitHub Actions | 免费（公开仓库）|
| 域名 | 可选（默认使用github.io） |
| **总计** | **$0** |

## 性能指标

- **爬虫运行时间**：约 30~60 秒（取决于AMC网站响应）
- **前端加载时间**：<1 秒（纯静态）
- **数据新鲜度**：每天1次（可根据需要增加频率）
- **存储空间**：<1MB（JSON数据极小）

## 缺陷和限制

1. **依赖网站结构**：如果AMC网站HTML结构变化，爬虫可能需要更新
2. **不支持订票**：仅显示排片，不能直接购票
3. **无用户账户**：不支持个人偏好保存
4. **速率限制**：如果频率过高可能被AMC网站限制（建议每天1次）

## 改进方向

- [ ] 添加更多影厅支持
- [ ] 实现电影详情页（IMDb评分等）
- [ ] 添加通知功能（邮件/Slack）
- [ ] 支持特定影片追踪
- [ ] 数据库存储历史数据
- [ ] Docker 化爬虫
- [ ] 前端优化（SSG/静态生成）

## 许可证

MIT License - 自由使用和修改

## 提示

- 此项目仅供学习和个人使用
- 遵守AMC网站的爬虫协议，合理控制请求频率
- 如遇到网站反爬，建议等待几小时后重试

---

有任何问题或建议，欢迎提交 Issue 或 Pull Request！
