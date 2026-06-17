# 微信读书生僻字渲染机制技术文档

## 概述

微信读书 Web 阅读器对无法用标准字体编码的生僻字（如古汉语人名/地名/物名用字）采用**图片替代**方案：在渲染流中以一张小尺寸位图替换该字符，并通过绝对定位把图片精确嵌入到文本行的对应位置。

该机制与 WRPA 反爬虫系统**共存但独立**——生僻字图本身不被 WRPA 加密，而是与 WRPA 的 canvas 文本层叠加显示。本文档记录在《史记·五帝本纪第一》("熊罴貔貅〔图〕虎"段)上的实测结论。

研究样本：`https://weread.qq.com/web/reader/7cb324e0727ab1f17cbf4c1k77432e103e16774b0e07772`，WRPA 版本 `1.0.5`。

---

## 1. 双图层叠加架构

阅读器在 `renderTargetContainer` 下维护**两条并行图层**，两者共享同一个章节级坐标系（原点在章节内容左上角）：

```
renderTargetContainer
├── wr_canvasContainer          ← 文本层（WRPA 绘制汉字）
│   └── canvas (1458 × ~7800)   fillText 逐行绘制
└── renderTargetContent         ← 图片叠加层
    └── passage-wrapper
        └── passage-content     ← 仅含生僻字 <img>，无文本
            └── img.h-pic.wr_absolute
```

要点：

- **文本层**：一个 `<canvas>`，WRPA 通过 `fillText` 把整页汉字画上去。这也是 `hook.js` 拦截的目标。
- **图片层**：`.passage-content` 是一个高度为 0、仅承载绝对定位图片的透明容器。它本身**不含任何文本**，只放生僻字 `<img>`。
- 两个图层用 `transform: translate(x, y)` 在同一坐标系内定位，最终在屏幕上重合成完整版面。

实测中出现过两种渲染形态，二者会随阅读器状态切换：

| 形态 | passage-content 数量 | 说明 |
|------|---------------------|------|
| 翻页模式 | 2（当前页 + 预渲染页） | canvas 仅对接近视口的页面绘制 |
| 滚动模式 | 12（整章一次性铺开） | 所有生僻字图都在 DOM 中，坐标 y 单调递增 |

**抓取启示**：无论哪种模式，`.passage-content img.h-pic` 都能枚举到本页/本章全部生僻字图，且 `transform` 坐标即其在文本流中的插入位置。

---

## 2. 生僻字图片的 DOM 结构

单张生僻字图的完整标记（取自样本，`epub_41595377_3` = "熊罴貔貅〔图〕虎" 中那个缺失的动物名）：

```html
<img alt="alt"
     class="h-pic wr_absolute wr_readerImage_opacity"
     style="transform: translate(129px, 3086px);
            max-width: 85px; max-height: 80px;
            width: 26.7778px; height: 25.1979px;"
     data-wr-id="wrqsm9qrxc61"
     src="https://res.weread.qq.com/wrepub/epub_41595377_3"
     data-src="https://res.weread.qq.com/wrepub/epub_41595377_3"
     data-w="85px"
     data-ratio="0.941"
     data-w-new="80px">
```

### 2.1 关键属性

| 属性 / 类 | 含义 |
|-----------|------|
| `class="h-pic"` | 生僻字图的统一标识（与水印二维码 `content_body_qrcode` 不同） |
| `wr_absolute` | 绝对定位，脱离文本流 |
| `wr_readerImage_opacity` | 阅读器图片透明度控制类 |
| `transform: translate(x, y)` | **该字在章节坐标系中的插入位置**，y 单位 px |
| `data-wr-id="wr..."` | EPUB 内部对该生僻字的引用 ID（每次出现都不同，即使 src 相同） |
| `src` / `data-src` | 图片资源 URL，形如 `https://res.weread.qq.com/wrepub/epub_{bookId}_{n}` |
| `data-w` | 原始宽度（如 `85px`），即生僻字在原文中的设计字号 |
| `data-ratio` | 图片宽高比（如 `0.941`），高度 = 宽度 / ratio |
| `data-w-new` | 当前缩放后的宽度（随阅读器字号设置变化） |
| `style.width/height` | 实际渲染尺寸，与周围正文字号一致（样本中约 27×25px） |

### 2.2 同一图片可被复用

同一 `src`（如 `epub_41595377_4`、`epub_41595377_9`）在章内可出现多次，但**每次出现的 `data-wr-id` 不同**，且 `transform` 坐标不同——即"同一个生僻字在文中多处使用，各处独立定位"。因此去重应以 `(src, data-wr-id, 坐标)` 联合判断，不能仅凭 `src`。

样本《五帝本纪第一》整章共统计到 **10 张生僻字图**，分布在 passage 1–7，y 坐标 3086→28352 单调递增：

| # | src | data-wr-id | x | y |
|---|-----|------------|---|---|
| 1 | epub_41595377_3 | wrqsm9qrxc61 | 129 | 3086 |
| 2 | epub_41595377_4 | wrle1batcvv8 | 458 | 11616 |
| 3 | epub_41595377_4 | wr9rgknra1o3w | 387 | 11616 |
| 4 | epub_41595377_4 | wrfr4uw68kser | 371 | 11657 |
| 5 | epub_41595377_7 | wr80b02sesf9 | 300 | 16057 |
| 6 | epub_41595377_8 | wrbfmj08qffkt | 39 | 18392 |
| 7 | epub_41595377_9 | wrnlpcc05yyu | 175 | 19359 |
| 8 | epub_41595377_10 | wryw4d4x30s3h | 27 | 20068 |
| 9 | epub_41595377_9 | wryp1vgi5a18 | 0 | 28164 |
| 10 | epub_41595377_9 | wrd0g26pkah2 | 176 | 28352 |

注意 #2/#3/#4 三张 `_4` 图 y 坐标几乎相同（11616/11616/11657）——它们是**同一行内的多个生僻字**，横向并排排列。

---

## 3. 与 WRPA 加密的关系：**不加密**

通过抓取图片字节验证：

- 图片 URL 直连 `res.weread.qq.com/wrepub/...`，HTTP 响应即为**标准 PNG**（文件头 `89 50 4E 47`）。
- `window.__WRPA__.decode()` **未被调用**——该图本就是有效 PNG，无需解密。
- WRPA 的 `decode()` 仅用于加密的正文图片（见 `wrpa-anti-crawl.md`），与生僻字图无关。

**结论**：生僻字图可直接通过 HTTP 下载使用，无需任何解密步骤。这与水印二维码（`data:` URI 内嵌）和加密正文图（需 `decode()`）是三类完全不同的资源，`images.py` 的 `ImageFilter` 应分别处理。

---

## 4. 文本与图片的对位关系（留空机制）

这是最关键的一点：canvas 文本如何"给图片腾位置"。

通过 `hook.js` 捕获样本页的 `fillText` 文本，在 "熊罴貔貅〔图〕虎" 段得到：

```
…，抚万民，度四方
[8]
教熊罴貔貅
[9]
虎，以与炎帝战于阪泉
[10]
之野。三战，然后得其…
```

对照生僻字图坐标 `translate(129, 3086)`、尺寸约 27×25px，可还原排版逻辑：

1. canvas 用 `fillText` 绘制到 "教熊罴貔貅" 时，**本行在该字位置停止**——不会画出生僻字本身（字体里没有这个字）。
2. 生僻字图通过绝对定位叠到 `(129, 3086)`，正好覆盖 "貔貅" 之后、"虎" 之前那个字符位。
3. canvas 下一行从 "虎，以与炎帝战于阪泉" 续接——即文本流在图片占用的横向区间**截断并换行绕开**，而非留空白字符占位。

因此 **canvas 文本中不存在与生僻字对应的任何字符**（既不是 `?`、不是 `□`、也不是空格），图片是纯粹的覆盖层。这带来两个直接后果：

- **仅靠 canvas hook 抓到的 markdown 会丢失生僻字**：`教熊罴貔貅` 和 `虎，以与炎帝战于阪泉` 之间没有任何占位符，直接拼接会变成 "教熊罴貔貅虎"（语义错误，丢失了一个字）。
- **必须把图片信息回插**：按 y 坐标把生僻字图插入到 canvas 文本流的对应位置，才能还原原文。

---

## 5. 抓取与还原方案

### 5.1 提取生僻字图清单

```javascript
const rareChars = Array.from(
  document.querySelectorAll('.passage-content img.h-pic')
).filter(img => (img.src || '').includes('res.weread.qq.com/wrepub/'))
 .map(img => {
   const m = (img.style.transform || '').match(
     /translate\(\s*([-\d.]+)px\s*,\s*([-\d.]+)px\s*\)/);
   return {
     src: img.src,
     dataWrId: img.getAttribute('data-wr-id'),
     x: m ? parseFloat(m[1]) : null,
     y: m ? parseFloat(m[2]) : null,
     width: img.getAttribute('data-w'),
     ratio: img.getAttribute('data-ratio'),
   };
 });
```

### 5.2 与 canvas 文本对位（已实现，行内精确定位）

**坐标空间的关键发现**：canvas 的 `fillText` 坐标是 canvas 内部像素（devicePixelRatio 缩放，实测 scale=3），而生僻字 `<img>` 的 `translate` 是 CSS 像素。两者不在同一空间，直接比较会错位 3 倍——这是 v1"行末追加"虽能工作但无法做行内定位的根因。

**统一坐标空间**：两者都换算到 **canvas-CSS 空间**（即 canvas 内部坐标 ÷ scale）：
- 片段坐标：hook 在 `flushLine` 时把每个 `fillText` 的 `x` ÷ scale 存为 `xCss`，行 y ÷ scale 存为 `y`。
- 图片坐标：`fetcher._rare_char_images()` 把 `translate` 的 x/y ÷ scale（通过 `wrpaHandler.getCanvasScale()` 取 scale）。
- 换算后两者在同一空间，实测 y 对齐误差 <1.5px，x 落在行内片段间隙中。

**回插规则**（实现于 `extractor.merge_rare_chars`）：

1. 收集 canvas 文本行 `{fragments: [{text, xCss}], y, prefix}`（来自 `wrpaHandler.getLinesWithCoords()`）与生僻字图 `{local_path, x, y}`（÷scale 后的 canvas-CSS 坐标）。
2. 对每张生僻字图，找 y 绝对差最小的文本行（容差默认 20px）。
3. **行内插入**：在该行的 `fragments` 序列里，找到第一个 `xCss ≥ 图片.x` 的片段，把 `![](local_path)` 插在它**前面**；若无此片段（图片在行末），追加到行末。这样图落在 canvas 留空的间隙里，精确定位。
4. 同行多图按 x 升序依次插入，保持阅读顺序。
5. 输出 markdown 占位标记为 `![](local_path)`，`local_path` 形如 `../images/{data-wr-id}.png`（相对 `content/{N}.md`）。

`hook.js` 新增 `wrpaHandler.getCanvasScale()` 返回 scale；`getLinesWithCoords()` 每行记录新增 `fragments` 字段（保留 `text` 向后兼容）。

### 5.3 与 `images.py` 的协作（已实现）

`ImageFilter.markdown_lines` 新增 `exclude: set[str]` 参数：已被坐标合并内联的生僻字 src 不再重复 append 到文末。其余图（插图/封面等非生僻字）仍按原规则 append。分类：

- **生僻字**（坐标内联）：`src` 含 `res.weread.qq.com/wrepub/` 且 class 含 `h-pic`。
- **排除**：`data:` URI → 水印；URL 含 `/loading_dark.` → 占位图。
- **另处理**：加密正文图（`res.weread.qq.com` 非 `wrepub/` 路径，需 `__WRPA__.decode()`）—— 与生僻字图区分，仍属未实现范围。

---

## 6. 实现状态与遗留风险

### 已实现

1. **hook.js 暴露片段级坐标**：`lineRecords` 数组与 `wrpaHandler.getLinesWithCoords()`，返回 `[{text, fragments: [{text, xCss}], y, fontSize, minFontSize, prefix}]`。`flushLine()` 把每个 `fillText` 片段的 x ÷ scale 存为 `xCss`，行 y ÷ scale。新增 `wrpaHandler.getCanvasScale()`。`clearMarkdown()` 同步清空。
2. **生僻字采集**：`fetcher._rare_char_images()` 读取 `.passage-content img.h-pic`（src 含 `wrepub/`），解析 `translate(x,y)` 并 ÷ scale（取自 `getCanvasScale()`）转成 canvas-CSS 坐标，与片段同空间。
3. **生僻字下载**：`fetcher._download_rare_chars()` 用 `page.request.get` 下载为 `images/{data-wr-id}.png`，已存在则跳过（resume-safe），补 `local_path` 字段。
4. **坐标合并（行内插入）**：`extractor.merge_rare_chars(lines, rare_chars)` 纯函数，按 y 匹配行后，在行 `fragments` 序列里按 x 间隙插入 `![](local_path)`——找第一个 `xCss ≥ 图片.x` 的片段插其前，无则行末。无 `fragments` 字段的旧记录回退到行末插入（向后兼容）。
5. **ImageFilter exclude**：`markdown_lines(..., exclude=)` 跳过已内联 src，避免重复。
6. **路径与传参**：`WeReadCrawlerPaths` 新增 `images_dir`，`_crawl` 创建之并传给 `extract_chapter_content(images_dir=...)`；`go_next` 同步透传。

**实测结果**（CLI 抓《史记》，行内精确定位）：

```
教熊罴貔貅[]![](../images/wrksu7y7ih50s.png)虎，以与炎帝战于阪泉
[7]![](../images/wr971ml6xrvti.png)（jú）​：特制的爬山鞋。底下钉有锥形器物…
```

生僻字图精确插在 canvas 留空的间隙里（`[]` 后、`虎` 前；`[7]` 脚注后、`（jú）` 释义前），不再追加行末。`images/` 下 PNG 全部下载成功。

**坐标空间验证**（canvas scale=3）：图片 `translate(129, 3086)`（CSS）÷ 3 = `(43, 1028.67)`，与该行片段 `y=1029.89`（误差 1.2px）、间隙位置 `](41) < 43 < 虎(51)` 完全吻合。

测试：`python -m unittest discover -s tests` 26 项全绿（含 9 项 `RareCharMergeTests`，覆盖行内插入、行末回退、同行多图、脚注间隙等场景）。

### 遗留风险

1. **行内插入依赖片段坐标**：若某行 `fillText` 把多个字合并成一次调用（而非逐字），`fragments` 会粗粒度，图片可能落到行末而非精确间隙。当前样本逐字绘制，未触发；若遇粗粒度行，图片退化为行末，仍可读但不精确。
2. **滚动模式整章捕获**：滚动模式下 12 个 passage 全在 DOM，canvas 仍按页懒绘制；逐章翻页提取，每章独立采集本章生僻字，已满足需求。
3. **跨页生僻字**：y 坐标在章节系内唯一（实测确认），按章 y 排序合并即可，不依赖页边界。
4. **DOM 分支**：非 WRPA 书的 DOM 模式暂不处理生僻字（innerText 里同样缺失），留 TODO。
5. **`data-wr-id` → Unicode 还原**：仍属远期，当前直接用图片保真。

---

## 附：研究方法复盘

- 浏览器：playwright-mcp，注入 `cache/auth/weread-storage-state.json` 的 cookie + localStorage 登录。
- 文本捕获：复用项目 `crawler/hook.js`，通过 `page.addInitScript` 在 `reload` 前注入，确保在 canvas `getContext` 之前打补丁（时序关键，事后注入无效）。
- 图片字节验证：`fetch(src).arrayBuffer()` 取前 4 字节判 PNG 头，确认未加密。
- 不依赖任何视觉读图——所有结论均来自 DOM 属性、canvas 文本、字节文件头三类可机读证据。
