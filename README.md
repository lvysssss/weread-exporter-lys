# weread-exporter-lys

受 [drunkdream/weread-exporter](https://github.com/drunkdream/weread-exporter) 项目启发，在其基础上重写的微信读书导出工具。支持导出为 Markdown / TXT / HTML / PDF / EPUB / MOBI / AZW3 等多种格式。

## 特色

- **双路径 WRPA 反爬虫绕过**：
  - **XHTML 源文件直提**（默认）：拦截 `e_N` 响应，解码还原章节原始 EPUB XHTML，从结构化源码直接提取文本和生僻字图片，零偏移精度。
  - **Canvas Hook 回退**（旧方案）：`hook.js` 拦截 `fillText` 调用，按坐标重组文本行，生僻字通过坐标对齐回插。
- **生僻字图片处理**：古籍/文言文中无法用标准字体编码的生僻字，自动下载图片并以内联 HTML 嵌入文本流，不影响阅读。
- **完整处理管线**：预处理（去水印/图片本地化）→ 章节合并 → 后处理（字符清洗/脚注格式化/生僻字解析）→ 格式转换。
- **多格式输出**：Markdown → TXT / 自包含 HTML / PDF（WeasyPrint）/ EPUB / MOBI / AZW3（Calibre）。
- **断点续爬**：每章完成后保存状态，中断后自动从上次断点继续。
- **书籍元信息提取**：自动获取书名、作者、简介、出版社、ISBN、版权信息（通过 `/web/book/info` API）。
- **CLI 交互模式**：`--interactive` 进入交互式向导，逐步选择平台、输入书链、设置格式。

## 安装

```bash
# 克隆仓库
git clone https://github.com/lvysssss/weread-exporter-lys.git
cd weread-exporter-lys

# 安装核心依赖
pip install -e .
python -m playwright install chromium

# 安装可选依赖（按需）
pip install beautifulsoup4 lxml        # TXT 转换 + XHTML 解析
pip install markdown                   # Markdown → HTML
pip install weasyprint                 # PDF 输出（Windows 需额外安装 GTK+）
pip install ebooklib                   # EPUB 输出
# MOBI/AZW3 需要安装 Calibre 命令行工具
```

## 使用

```bash
# 交互模式（推荐首次使用）
python -m weread_exporter_lys --interactive

# 命令行模式 — 导出 EPUB
python -m weread_exporter_lys \
  --platform weread \
  --url https://weread.qq.com/web/reader/xxx \
  --book-id 123456 \
  --format epub \
  --headless

# 命令行模式 — 导出 Markdown（默认 XHTML 爬取方式）
python -m weread_exporter_lys \
  --platform weread \
  --url https://weread.qq.com/web/reader/xxx \
  --format md \
  --headless

# 使用 Canvas 方式爬取（旧方案）
python -m weread_exporter_lys \
  --platform weread \
  --url https://weread.qq.com/web/reader/xxx \
  --format pdf \
  --crawl-method canvas

# 限制章节数 + 调试模式
python -m weread_exporter_lys \
  --platform weread \
  --url https://weread.qq.com/web/reader/xxx \
  --format md \
  --max-chapters 10 \
  --debug
```

首次使用会打开 Chrome 窗口等待扫码登录，登录态保存在 `cache/auth/` 下，之后可加 `--headless` 无头运行。

## CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--platform` | 平台名称 | `weread` |
| `--url` | 书籍阅读页 URL | — |
| `--book-id` | 书籍 ID（可从 URL 提取） | — |
| `--format` | 输出格式：`md` / `txt` / `html` / `pdf` / `epub` / `mobi` / `azw3` | `md` |
| `--crawl-method` | 爬取方式：`xhtml`（默认，源文件直提）/ `canvas`（旧方案） | `xhtml` |
| `--max-chapters` | 最大爬取章节数（0 = 全部） | `0` |
| `--cache-dir` | 缓存目录 | `cache` |
| `--output-dir` | 输出目录 | `output` |
| `--delay` | 页面操作间隔（秒） | `1.0` |
| `--auth-state` | 登录态文件路径 | `cache/auth/weread-storage-state.json` |
| `--headless` | 无头模式运行浏览器 | `false` |
| `--debug` | 调试模式（输出每步诊断信息） | `false` |
| `--interactive` | 交互式向导模式 | `false` |
| `--config` | JSON 配置文件路径 | — |

## 架构

```
weread_exporter_lys/
├── __main__.py            # python -m 入口
├── cli.py                 # argparse CLI + 交互式向导 + execute_request()
├── config.py              # AppConfig dataclass + JSON 配置加载
├── progress.py            # 进度系统（ProgressEvent / ProgressRenderer）
│
├── platforms/
│   ├── base.py            # BookPlatform ABC, ExportRequest, ExportResult
│   ├── weread.py          # 微信读书平台（校验书链 → 爬虫 → 管线 → 输出）
│   └── __init__.py        # 平台注册表
│
├── crawler/
│   ├── fetcher.py         # Playwright 浏览器控制、登录、双路径内容提取
│   ├── weread.py          # 爬虫编排（封面 → 元信息 → 目录 → 逐章循环）
│   ├── state.py           # CrawlState 断点续爬 + 警告去重
│   ├── extractor.py       # DOM 文本提取、markdown 辅助、生僻字坐标合并
│   ├── xhtml_source.py    # XHTML 源文件解码与转换（e_N 响应 → markdown）
│   ├── images.py          # ImageFilter（水印/占位图过滤、去重、生僻字排除）
│   └── hook.js            # WRPA Canvas hook（fillText 拦截 + 行重组 + 坐标暴露）
│
└── processing/
    ├── pipeline.py        # 处理管线编排（预处理 → 合并 → 后处理 → 转换）
    ├── merge.py           # 章节合并（<div class="page-break"> 分隔）
    ├── convert.py         # Markdown → TXT/HTML/PDF/EPUB/MOBI/AZW3
    ├── preprocess.py      # 预处理包装器（去水印/格式规范化/图片本地化）
    └── postprocess.py     # 后处理包装器（字符清洗/脚注/生僻字解析/内联图片）
```

### 数据流

```
CLI ──ExportRequest──▶ WeReadPlatform.export()
  │
  ├── WeReadCrawler.crawl()
  │     └── WeReadPageFetcher (Playwright)
  │          1. 打开阅读页 → 确保登录（或等待扫码）
  │          2. 检测付费墙 → 付费书籍提前失败
  │          3. 保存封面 → cache/{book_id}/封面.jpg
  │          4. 提取目录 → cache/{book_id}/toc.json
  │          5. 提取元信息 → cache/{book_id}/meta.json
  │          6. 逐章循环：
  │             a. 清 WRPA 缓冲 → 点击目录项 → 等待渲染
  │             b. extract_full_chapter() 逐页翻页提取
  │                - xhtml 路径：拦截 e_N 响应 → 解码 XHTML → 零偏移提取
  │                - canvas 路径：hook 文本 + 生僻字坐标合并
  │                - DOM 回退：WRPA 无文本时使用 DOM（过滤 CSS 垃圾）
  │             c. 保存 → cache/{book_id}/{method}/content/{N}.md
  │             d. 持久化原始 XHTML → cache/{book_id}/{method}/xhtml_src/（断点复用）
  │             e. 更新状态 → cache/{book_id}/{method}/state.json
  │          7. 保存登录态
  │
  └── ProcessingPipeline.run()
       1. 加载目录 + 提取书名/作者（从 meta.json）
       2. 逐章预处理：去水印 → 规范化 → 下载图片 → images/
       3. 合并章节（page-break 分隔）
       4. 后处理：字符清洗 → 脚注格式化 → 生僻字解析 → 内联图片
       5. 保存处理后 .md → cache/{book_id}/{书名}.md
       6. 转换为目标格式 → output/{书名}.{fmt}
```

## 缓存结构

```
cache/{book_id}/
├── 封面.jpg
├── toc.json                  # [{index, title, level}]
├── meta.json                 # {title, author, intro, publisher, publishTime, isbn, copyright}
├── {method}/                 # 爬取方式隔离：xhtml 或 canvas
│   ├── state.json            # CrawlState 断点状态 + 警告记录
│   ├── content/
│   │   ├── 1.md
│   │   ├── 2.md
│   │   └── ...
│   ├── images/
│   │   ├── wrksu7y7ih50s.png  # 生僻字图片
│   │   ├── abc123.jpg         # 插图
│   │   └── ...
│   └── xhtml_src/            # 原始 XHTML（仅 xhtml 方式，断点复用）
│       ├── 1.xhtml
│       ├── 2.xhtml
│       └── ...
└── {书名}.md                 # 处理后的合并 markdown
```

## WRPA 反爬虫原理

微信读书使用 `window.__WRPA__`（v1.0.5）对文本内容进行 Canvas 加密渲染，DOM 中为 CSS 乱码。本工具提供两种绕过方式：

### XHTML 源文件直提（`--crawl-method xhtml`，默认）

微信读书阅读器通过 `POST /web/book/chapter/e_{N}` 接口分批获取章节的原始 EPUB XHTML 数据。每个响应体格式为 `<32-hex-hash><1 flag><base64 chunk>`。工具拦截这些响应，按 N 排序拼接、base64 解码，还原为完整的 XHTML 文档，然后解析提取结构化文本和生僻字图片。生僻字在 XHTML 中位于精确的文本流位置，无需坐标合并。

详见 `xhtml-crawl-method.md`（中文）。

### Canvas Hook 回退（`--crawl-method canvas`，旧方案）

通过 `hook.js` 拦截 `CanvasRenderingContext2D.fillText()` 调用，按 y 坐标分组重组文本行、按 x 坐标排序行内文字片段。生僻字通过采集 `.passage-content img.h-pic` 的 CSS `translate` 坐标，与 canvas 文本行的 x 坐标间隙对齐后回插。

当 `canvas` 方式检测到生僻字且已捕获 `e_N` 响应时，会自动切换到 XHTML 路径以获得零偏移精度。

详见 `wrpa-anti-crawl.md`（中文）。

## 依赖

### 核心（必装）

- Python ≥ 3.10
- `playwright`（浏览器自动化）

### 可选（按需安装）

| 用途 | 依赖 |
|------|------|
| TXT 转换 + XHTML 解析 | `beautifulsoup4`, `lxml` |
| Markdown → HTML | `markdown` |
| PDF 输出 | `weasyprint`（Windows 需额外安装 GTK+） |
| EPUB 输出 | `ebooklib` |
| MOBI / AZW3 输出 | Calibre `ebook-convert`（外部命令行工具） |

## 测试

```bash
python -m unittest discover -s tests
```

51 个测试用例，覆盖用户层（10）和爬虫层（41）。测试使用 mock，无需真实浏览器。

## 设计文档

- `设计文档.md` — 完整架构与需求（中文）
- `wrpa-anti-crawl.md` — WRPA 反爬虫技术参考（中文）
- `xhtml-crawl-method.md` — XHTML 源文件直提：原理、格式、实现、待解决问题（中文）
- `rare-char-rendering.md` — 生僻字渲染参考

## 待完成

1. **分册标题/封面提取**：多册书籍的封面页结构化提取
2. **首章排序修正**：登录态 cookie 可能导致初始页面加载错误章节
3. **WRPA 图片解密**：加密图片（`res.weread.qq.com`）可通过 `window.__WRPA__.decode()` 解码
4. **处理层单元测试**：merge / convert / preprocess / postprocess 暂无测试覆盖
5. **多平台支持**：扩展支持微信读书以外的阅读平台
6. **e_N 响应等待超时调优**：当前 2s 超时可能导致 XHTML 路径回退到 canvas/DOM

## License

MIT
