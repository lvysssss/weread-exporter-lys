# XHTML 源直取法 — 技术文档

## 概述

微信读书阅读器加载章节时，浏览器会发 `POST /web/book/chapter/e_{N}` 请求。响应体是**结构化 EPUB XHTML 源**——生僻字 `<img class="h-pic">` 精确内联在文本流位置，脚注 `<sup><a href="#a_N" id="b_N">[n]</a></sup>` 已是完整双向锚点。拦截这些响应即可获得**零误差文本**，彻底绕开 canvas 坐标合并。

## 响应体格式

```
每个响应体 = <32位hex哈希><1个标志字符><base64流切片>
```

一个章节对应 **多个 `e_N` 分片**（如五帝本纪 = e_0..e_3 共 4 个）。将所有分片的 base64 切片（去掉 32+1 前缀）**按 N 升序拼接**，整体 base64 解码即得完整 XHTML。

示例（五帝本纪第一，4 个分片）：

| 分片 | 32hex hash（示例） | flag char | base64 长 |
|------|-------------------|-----------|-----------|
| e_0 | `D8E5...9590` | `P` | 50903 |
| e_1 | `CCDA...1AF6` | `E` | 50903 |
| e_2 | `6D59...FA47` | `P` | 56293 |
| e_3 | `3165...5715` | `j` | 50903 |

解码后得到约 103KB XHTML，含 14 个 `<?xml` 文档（每个对应一段【原文】或【注释】）。

## XHTML 结构

```xml
<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
<h2 class="secondTitle-1">第一卷 五帝本纪第一</h2>
<h3 class="thirdTitle-1">【原文】</h3>
<p>教熊罴貔貅<sup><a href="#a15" id="b15">[9]</a></sup>
<img alt="alt" class="h-pic" src="https://res.weread.qq.com/wrepub/epub_41595377_3"
     data-w="85px" data-ratio="0.941" data-w-new="80px"/>
虎，以与炎帝战于阪泉<sup><a href="#a16" id="b16">[10]</a></sup>之野。</p>
<h3 class="thirdTitle-1">【注释】</h3>
<p class="note"><a href="#b15" id="a15">[9]</a>貔（pí xiū）：传说中的一种猛兽…</p>
</body>
</html>
```

关键特征：
- 生僻字 `<img>` 在 `<p>` 文本流中精确位置——不需要坐标匹配
- 脚注 `href="#a_N" id="b_N"` 是双向锚点——正文点 `b_N` 跳注释 `a_N`，注释点 `a_N` 跳回 `b_N`
- `【原文】`/`【注释】`/`【译文】` 由 `<h3>` 标记
- 一个 XHTML 文档 = 一段 【原文】+ 其 【注释】

## 实现模块

### `crawler/xhtml_source.py`

纯函数模块，无 Playwright 依赖，可独立单元测试。

#### `decode_chapter_responses(responses: dict[int, str]) -> str | None`

输入 `{batch_index: response_body}` 字典（由 fetcher 的 response 监听器收集），输出拼接解码后的 XHTML 文本。返回 `None` 表示无有效响应。

算法：
1. 按 N 升序遍历
2. 每个 body 去掉前 `_HASH_LEN + _FLAG_LEN` (= 33) 个字符，得到 base64 切片
3. 拼接所有切片
4. 补 padding → base64 解码 → UTF-8 解码 → 返回

#### `collect_rare_char_srcs(xhtml: str) -> dict[str, str]`

扫描 XHTML 中所有 `<img class="h-pic">`（或 `src` 含 `wrepub/`），返回 `{src: filename_stem}` 映射。同 src 去重（只保留一个 stem），供下载用。

#### `rare_char_name(src: str) -> str`

从 URL 末段（如 `epub_41595377_3`）推导文件名 stem = `wr{stem}`。保持 `wr` 前缀与 canvas 路径一致，`postprocess.inline_rare_char_images` 的正则 `images/wr[^)]+\.png` 能同时匹配。

#### `xhtml_to_markdown(xhtml: str, *, page_url: str) -> tuple[str, set[str]]`

BeautifulSoup 解析 XHTML → markdown。

转换规则：
- `<?xml` 切分多文档，按序处理
- `<h2>` → `## `, `<h3>` → `### `
- `<p>` → 段落，内联 `<sup><a>[n]</a></sup>` → `[n]`
- `<img class="h-pic">` → `![](../images/{stem}.png)`（位置不动）
- 普通 `<img>` → 末尾按 `ImageFilter` 追加（去重、过滤水印）
- 返回 `(markdown, rare_char_srcs_inlined)`

### `crawler/fetcher.py` — 集成点

#### response 监听器（`__aenter__`）

```python
self._page.on("response", self._on_chapter_response)
```

Playwright 网络层监听，捕获所有 `POST /book/chapter/e_N` 响应（status=200、非空、非 `{}`），存入 `self._chapter_responses: dict[int, str]`。

#### `_extract_via_xhtml(images_dir, anti_crawl_status) -> ChapterContent | None`

每章提取时调用：
1. `drain_chapter_responses()` → 获取并清空缓存的 e_N 响应
2. `decode_chapter_responses()` → 解码
3. `xhtml_to_markdown()` → 转 markdown
4. `collect_rare_char_srcs()` + `_download_image()` → 下载生僻字图
5. 返回 `ChapterContent(source="xhtml")`
6. 任何步骤失败返回 `None`

#### `extract_chapter_content` 分岔逻辑

```python
has_wrpa = detect_anti_crawl()
if has_wrpa:
    if crawl_method == "xhtml":
        try _extract_via_xhtml → success → return
    // fallback: canvas 坐标合并
else:
    // DOM 直接提取
```

### CLI 与配置

```bash
python -m weread_exporter_lys --crawl-method xhtml   # 默认
python -m weread_exporter_lys --crawl-method canvas  # 回退旧法
```

交互模式也会提示选择。

### 缓存隔离

```
cache/{book_id}/
├── 封面.jpg              # 共享
├── toc.json              # 共享
├── meta.json             # 共享
├── auth/                 # 共享登录态
├── xhtml/                # ← 新方法
│   ├── state.json
│   ├── content/{N}.md
│   ├── images/           # 生僻字图: wrepub_X.png
│   └── {书名}.md
└── canvas/               # ← 旧方法
    ├── state.json
    ├── content/{N}.md
    ├── images/           # 生僻字图: wr{data-wr-id}.png
    └── {书名}.md
```

## 已验证结果

### 端到端：五帝本纪第一（第 3 章）

| 指标 | 结果 |
|------|------|
| 爬取方法 | `source: xhtml` |
| 生僻字位置 | `教熊罴貔貅[9]![](../images/wrepub_41595377_3.png)虎` — **零误差** |
| 生僻字数 | 8 个，6 个唯一文件（去重） |
| 脚注 | 259 个 `[n]`，13 对【原文】/【注释】 |
| XHTML 文档 | 完整 14 个 `<?xml` 全在 |
| 单元测试 | 51 项全绿 |

### 对照 canvas 方法

| 维度 | xhtml 方法 | canvas 方法 |
|------|-----------|-------------|
| 生僻字误差 | **0 字符** | 0-5 字符 |
| 生僻字定位方式 | XHTML 内联 | canvas fillText 坐标匹配 (`merge_rare_chars`) |
| 脚注锚点 | XHTML 已配对 | 需后处理 `add_footnote_links` 重新配对 |
| 依赖 | Playwright 网络监听 | WRPA hook.js + canvas 渲染 |
| 非 WRPA 章 | 退到 DOM | DOM 直接提取 |

## 当前未完成任务

### 1. 第 2 章（司马迁的生平和著作）获取失败

**现象**：导航到第 2 章后，阅读器不发送 `chapter/e_N` 请求。WRPA 库已加载（`window.__WRPA__` 存在），canvas 存在但可能没绘制文字，DOM 里无可见正文。

**影响**：第 2 章的 `source` 可能回退到 `dom` 或 `wrpa`，质量不如 xhtml。

**可能原因**：
- 第 2 章是导读/前言，微信读书对其使用了不同的渲染机制
- 第 2 章内容被包含在首次加载的 e_0..e_3 某一片中（待验证：解码 e_1/e_2/e_3 看是否含第 2 章内容）
- 第 2 章可能需要其他 endpoint（如 `chapter/未编号`）

**诊断方向**：
1. 解码 e_1/e_2/e_3 确认是否含第 2 章内容
2. 检查阅读器导航到第 2 章时的 JS 行为（为什么没触发 e_N 请求）
3. 如果第 2 章内容在首次加载的 e_N 中，需要实现「从 e_0..e_3 中按标题匹配到第 2 章」的逻辑

### 2. 第 1 章（史记第一册/封面）获取

与第 2 章类似，可能面临同样的 `chapter/e_N` 不发请求的问题。

### 3. `chapter/e_N` 触发条件待确认

- 什么条件触发阅读器发 `chapter/e_N`？只有正文章节（五帝、夏本等），还是所有章节？
- `e_N` 的 N 是如何分配的？是全局文件索引还是章节内序号？
- 首次加载预取的 4 个 e_N 是否覆盖了当前章节前后的内容？

### 4. canvas 路径的 `_first_text` textContent fallback

**已修复**（`fetcher.py:541-565`）：`_first_text` 在 `innerText` 为空时不再直接 fallback 到 `textContent`（会包含后代 `<style>` 的 CSS 垃圾），而是先 clone 节点移除 `<style>/<script>` 后代再取 `textContent`。

**验证方式**：运行现有 51 项测试全绿。

### 5. 多章节爬取稳定性测试

只在单章（五帝本纪第一）上做了端到端验证。需要：
- 完整爬取《史记》所有 144 章，验证全部通过
- 确认 `_chapter_responses` 不会跨章污染（`drain_chapter_responses` 每章清空）
- 确认批量爬取性能（response 监听器开销可忽略）

## 文件改动清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `platforms/base.py` | 修改 | `ExportRequest` 加 `crawl_method` 字段 |
| `cli.py` | 修改 | `--crawl-method` 参数 + 交互选择 |
| `config.py` | 修改 | `AppConfig` 加 `crawl_method` |
| `crawler/xhtml_source.py` | **新建** | 解码 + XHTML→markdown 核心逻辑 |
| `crawler/fetcher.py` | 修改 | response 监听 + `_extract_via_xhtml` + 分岔逻辑 + `_first_text` 修复 |
| `crawler/weread.py` | 修改 | `paths_for` 缓存隔离；透传 `crawl_method` |
| `processing/pipeline.py` | 修改 | `method_dir` property，路径隔离 |
| `tests/test_crawler_layer.py` | 修改 | +XhtmlSourceTests + FetcherXhtmlDispatchTests + 隔离测试 |
| `tests/test_user_layer.py` | 修改 | +crawl_method 透传测试 |

## 依赖

- `beautifulsoup4` + `lxml` — XHTML 解析（`xhtml_to_markdown`）
- `playwright` — 浏览器控制 + response 监听

`beautifulsoup4` 和 `lxml` 在 `processing/convert.py` 已有 import，不新增依赖。
