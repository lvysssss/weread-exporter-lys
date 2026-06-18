# weread-exporter-lys

受 [drunkdream/weread-exporter](https://github.com/drunkdream/weread-exporter) 项目启发，在其基础上重写。

## 特色

- **WRPA 反爬虫绕过**：通过 Canvas hook 逐字拦截 `fillText` 调用，将 WRPA 加密的 canvas 文本重组为可读 markdown。支持章节内翻页分页的逐页采集。
- **生僻字图片处理**：文言文/古籍中无法用标准字体编码的生僻字（如 "熊罴貔貅〔图〕虎"），通过坐标对位将图片精确回插到文本流中，不影响阅读。
- **CLI 交互模式**：支持 `--interactive` 进入交互式向导，一步步选择平台、输入书链、设置格式，比纯命令行更直观易用。
- **断点续爬**：每章完成后保存状态，中断后自动从上次断点继续。
- **多平台架构**：基于 `BookPlatform` 抽象基类，微信读书为已实现平台，可扩展支持其他阅读平台。

## 架构

```
weread_exporter_lys/
├── __main__.py            # python -m 入口
├── cli.py                 # argparse CLI + 交互式向导
├── config.py              # AppConfig + JSON 配置加载
├── progress.py            # 进度回调（ProgressEvent / ProgressRenderer）
│
├── platforms/
│   ├── base.py            # BookPlatform ABC, ExportRequest, ExportResult
│   ├── weread.py          # 微信读书平台（校验书链 → 委托爬虫）
│   └── __init__.py        # 平台注册表
│
└── crawler/
    ├── fetcher.py         # Playwright 浏览器、登录、翻页、内容提取
    ├── weread.py          # 爬虫编排（封面 → 目录 → 逐章/逐页提取）
    ├── state.py           # CrawlState 断点续爬
    ├── extractor.py       # CSS 过滤 DOM 提取、markdown 辅助、生僻字合并
    ├── images.py          # ImageFilter（水印去重/占位图过滤/生僻字排重）
    └── hook.js            # WRPA Canvas hook（fillText 拦截 + 行重组 + 坐标暴露）
```

```
                          ┌──────────────────────────┐
                          │          CLI             │
                          │  (argparse + interactive)│
                          └────────────┬─────────────┘
                                       │ ExportRequest
                          ┌────────────▼─────────────┐
                          │   WeReadPlatform.export() │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │    WeReadCrawler.crawl()  │
                          │    (封面→目录→逐章循环)    │
                          └──────┬──────────────┬────┘
                                 │              │
                    ┌────────────▼──┐   ┌───────▼──────┐
                    │ WeReadPage-   │   │  CrawlState   │
                    │ Fetcher       │   │  (断点续爬)    │
                    │ ┌───────────┐ │   └──────────────┘
                    │ │ Playwright│ │
                    │ │ 浏览器    │ │
                    │ └─────┬─────┘ │
                    │       │       │
                    │ ┌─────▼─────┐ │
                    │ │  hook.js  │ │  ← Canvas fillText 拦截
                    │ │  WRPA绕过 │ │    逐字重组文本行
                    │ └───────────┘ │
                    │ ┌───────────┐ │
                    │ │ extractor │ │  ← 生僻字坐标合并
                    │ │ images.py │ │    图片过滤/去重
                    │ └───────────┘ │
                    └──────────────┘
```

```
用户层                         爬虫层
  CLI ──ExportRequest──▶ Platform ──▶ Crawler ──▶ Fetcher(Playwright)
                                                    │
                                          ┌─────────┴──────────┐
                                          │  hook.js 拦截 fillText │
                                          │  extract_full_chapter  │
                                          │    ├─ 逐页翻页         │
                                          │    ├─ 生僻字采集+合并  │
                                          │    └─ 各页 markdown 拼接│
                                          └──────────────────────┘
```

## 安装

```bash
# 克隆仓库
git clone https://github.com/lvysssss/weread-exporter-lys.git
cd weread-exporter-lys

# 安装依赖
pip install -e .
python -m playwright install chromium
```

## 使用

```bash
# 交互模式（推荐）
python -m weread_exporter_lys --interactive

# 命令行模式
python -m weread_exporter_lys \
  --platform weread \
  --url https://weread.qq.com/web/reader/xxx \
  --format md \
  --headless
```

首次使用会打开 Chrome 窗口等待扫码登录。登录成功后会在 `cache/auth/` 下保存登录态，之后可加 `--headless` 无头运行。

## 缓存结构

```
cache/{book_id}/
├── 封面.jpg
├── toc.json            # [{index, title, level}]
├── state.json          # 爬虫断点状态
├── content/
│   ├── 1.md
│   ├── 2.md
│   └── ...
└── images/             # 生僻字图片
    ├── wr*.png
    └── ...
```

## 依赖

- Python ≥ 3.10
- `playwright`
- 标准库（无其他运行时依赖）
