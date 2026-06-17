# 微信读书 WRPA 反爬虫机制技术文档

## 概述

WRPA（WeRead Page Anti-crawl）是微信读书 Web 阅读器使用的反爬虫保护系统，由 `wpa-1.0.5.js` 脚本提供。它对页面内容（文本和图片）实施加密/混淆处理，使直接抓取无法获得可读数据。

当前项目通过 Canvas Hook 和 `__WRPA__.decode()` 两种方式绕过 WRPA 保护。

---

## 1. WRPA 检测

### 1.1 检测方法

WRPA 加载后会在 `window` 上注册 `__WRPA__` 全局对象。检测方式：

```javascript
// 检测 WRPA 是否存在
const hasWRPA = !!window.__WRPA__;

// 获取版本号
const version = window.__WRPA__ ? window.__WRPA__.version : null;
```

当前项目中 `hook.js` 的 `wrpaHandler` 封装了此检测：

```javascript
const wrpaHandler = {
  detect() { return !!window.__WRPA__; },
  getVersion() { return window.__WRPA__ ? window.__WRPA__.version : null; },
  // ...
};
```

### 1.2 Python 端检测

在 `webpage.py` 的 `goto_chapter()` 中，加载章节页面后调用：

```python
anti_crawl_status = await self._detect_anti_crawl()
self._has_anti_crawl = anti_crawl_status.get("hasWRPA", False)
```

`_detect_anti_crawl()` 执行 `window.wrpaHandler.getAntiCrawlStatus()`，返回：

| 字段 | 类型 | 含义 |
|------|------|------|
| `hasWRPA` | bool | 是否检测到 WRPA |
| `wrpaVersion` | string\|null | WRPA 版本号（当前为 `"1.0.5"`） |
| `hasPaywall` | bool | 是否有付费墙 |
| `hasCanvasContent` | bool | Canvas 是否有绘制内容 |
| `hasCanvasHandler` | bool | Canvas Hook 是否已安装 |
| `hasCanvasMarkdown` | bool | Canvas Hook 是否已提取到 markdown |

### 1.3 WRPA 激活条件

WRPA 并非对所有书/所有章节都激活。激活取决于：
- 书籍出版商是否启用了 WRPA 保护
- 用户是否为付费会员（付费会员可能不触发）
- 书籍的 DRM 级别

**判断技巧**：如果页面中存在 `<canvas>` 元素且文本通过逐字 `fillText` 绘制（而非普通 DOM 文本），则说明 WRPA 文字加密已激活。

---

## 2. WRPA 文本加密与解密

### 2.1 加密方式

WRPA 对文字内容的保护方式是**不渲染为 DOM 文本**，而是通过 Canvas 2D API 逐字绘制：

```javascript
// WRPA 反爬模式下，每个字符单独调用 fillText
ctx.fillText('史', x1, y);
ctx.fillText('记', x2, y);
// 而非一次性绘制整行
```

这使得 DOM 中没有可选择的文本，`document.body.innerText` 无法获取内容。

### 2.2 解密方式：Canvas Hook

当前项目通过 `hook.js` 拦截 Canvas 的 `fillText` 方法，逐字捕获绘制内容并重组为 markdown：

```
Canvas fillText 调用 → hook.js 拦截 → handleFillText() → 行缓冲 flushLineBuffer() → markdown 文本
```

核心逻辑在 `hook.js` 的 `canvasContextHandler`：

1. **Hook `getContext('2d')`**：在获取 Canvas 上下文时替换关键方法
2. **Hook `fillText`**：捕获每次绘制的文字、坐标 (x, y)
3. **行重组**：通过 Y 坐标变化判断换行，累积同行的字符到 `lineBuffer`
4. **字体大小判断**：通过 `font` 属性获取字号，判断标题/正文/上标
5. **刷新时机**：在 `restore()` 调用时触发最终 `flushLineBuffer()`

字体大小与 markdown 格式的对应关系：

| 字号范围 | markdown 格式 |
|----------|---------------|
| ≥ 27px | `## 标题`（二级标题） |
| ≥ 23px | `### 标题`（三级标题） |
| ≥ 18px | 正文段落 |
| ≤ 18px（字号变化时） | `<sup>` 上标（注释编号） |

### 2.3 WRPA 模式 vs 标准模式

| 特征 | 标准模式 | WRPA 模式 |
|------|---------|-----------|
| 文本渲染 | 整段 `fillText` | 逐字 `fillText` |
| DOM 文本 | 有 `.passage-content pre` | 无 |
| Canvas 内容 | 有完整绘制 | 有逐字绘制 |
| Hook 策略 | `textBuffer` 整段收集 | `lineBuffer` 逐字按行重组 |

---

## 3. WRPA 图片加密与解密

### 3.1 两类图片

微信读书章节中存在两类图片：

| 类型 | 形式 | 示例 | 内容 |
|------|------|------|------|
| **正常图片** | URL 链接 | `https://res.weread.qq.com/wrepub/epub_xxx_N` | 标准 JPEG/PNG，可正常下载 |
| **混淆水印** | base64 内嵌 | `data:image/png;base64,...` | 200×200 黑白二维码，CSS 隐藏 |

### 3.2 base64 混淆水印

这些 base64 图片位于 `.content_body_qrcode` 容器中：

```html
<div class="content_body_qrcode">
  <img src="data:image/png;base64,iVBORw0KGgo...">
</div>
```

特征：
- 容器 CSS 宽高为 0，**不可见**
- 图片尺寸固定 200×200
- 内容为黑白二维码（~63% 白 + ~37% 黑）
- **同一本书的所有水印完全相同**（md5 一致）
- 用途：版权追踪/用户指纹，**不是真正的加密图片**
- 处理：**应当删除**，不应保留在导出结果中

### 3.3 URL 图片（`res.weread.qq.com`）

`res.weread.qq.com` 上的图片**可能被 WRPA 加密**，取决于书籍的 DRM 级别：

- **未加密**：下载后直接是标准 JPEG（`FF D8 FF` 开头），如史记的封面图
- **已加密**：下载后不是标准图片格式，需要通过 `__WRPA__.decode()` 解密

**如何判断 URL 图片是否被加密**：

```python
# 下载图片后检查文件头
data = await fetch(image_url)
if data[:2] == b'\xff\xd8':  # JPEG SOI marker
    # 正常 JPEG，未加密
elif data[:4] == b'\x89PNG':  # PNG header
    # 正常 PNG，未加密
else:
    # 可能被 WRPA 加密，需要调用 decode
```

### 3.4 图片解密：`__WRPA__.decode()`

`window.__WRPA__` 对象的 `decode` 方法用于解密 WRPA 加密的图片字节：

```javascript
// 解密流程
const response = await fetch(imageUrl);
const buffer = await response.arrayBuffer();
const encryptedBytes = new Uint8Array(buffer);

try {
    const decryptedBytes = window.__WRPA__.decode(encryptedBytes);
    // decryptedBytes 是解密后的正常图片字节
} catch (e) {
    if (e.message === 'invalid encoding') {
        // 图片未被加密，原始字节就是正常图片
    } else {
        // 其他解密错误
    }
}
```

**注意事项**：
- `decode()` 的输入必须是 `Uint8Array`（不是 base64 字符串、不是 ArrayBuffer）
- `decode()` 会检测文件头：正常 JPEG 输入会抛出 `invalid encoding` 错误
- `decode()` 内部实现被虚拟机混淆保护，无法直接阅读源码
- 解密底层使用 CryptoJS，采用 **AES-CBC 模式 + PKCS7 填充 + EvpKDF 密钥派生**

### 3.5 图片解密：Canvas drawImage 捕获

`hook.js` 还通过拦截 `ctx.drawImage()` 来捕获浏览器端已解密的图片：

```javascript
ctx.drawImage = function (...args) {
    const source = args[0];
    try {
        if (source instanceof HTMLImageElement && source.src) {
            // 将已渲染的图片绘制到临时 Canvas 提取 dataURL
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = source.naturalWidth;
            tempCanvas.height = source.naturalHeight;
            const tempCtx = tempCanvas.getContext('2d');
            tempCtx.drawImage(source, 0, 0);
            const dataURL = tempCanvas.toDataURL('image/jpeg', 0.92);
            canvasContextHandler.data.decodedImages[src] = dataURL;
        }
    } catch (e) {
        // 跨域 Canvas 无法提取（tainted canvas）
    }
    return origDrawImage(...args);
};
```

**限制**：跨域图片会污染 Canvas（tainted canvas），导致 `toDataURL()` 抛出 SecurityError。`res.weread.qq.com` 的图片存在此问题。

---

## 4. WRPA 签名机制

`__WRPA__.sr()` 用于生成请求签名，与内容解密无关，但访问某些 API 需要携带签名。

### 4.1 两种签名方式

```javascript
// 方式1：为请求体签名，生成 x-wrpa-0 请求头
const signature = await window.__WRPA__.sr({ body: requestBody });
// → 添加 headers: { 'x-wrpa-0': signature }

// 方式2：为查询参数签名
const signature = await window.__WRPA__.sr({ query: queryString });
// → 添加 headers: { 'x-wrpa-0': signature }
```

### 4.2 回退签名

当 `__WRPA__` 不可用时，使用硬编码的回退签名值：

| 场景 | 回退值 |
|------|--------|
| POST 请求（body签名） | `'2097d7b063d6f8b5'` |
| GET 请求（query签名） | `'cc803abf3df758aa'` |

---

## 5. 当前项目的处理流程

### 5.1 完整流程图

```
加载章节页面
    │
    ├── 注入 hook.js（通过 webproxy 拦截 Script 请求）
    │
    ├── _detect_anti_crawl() → 检测 WRPA 状态
    │       │
    │       ├── hasWRPA = false → 标准 Canvas Hook 模式
    │       │
    │       └── hasWRPA = true → WRPA 模式（逐字 fillText 重组）
    │
    ├── _check_next_page() → 翻页处理
    │       │
    │       ├── WRPA 模式：跳过逐页收集（内容已在首次加载中获取）
    │       └── 标准模式：逐页点击"下一页"并拼接 markdown
    │
    └── get_markdown() → 获取最终 markdown
            │
            ├── 优先：WRPA markdown（如果 _wrpa_markdown 有值）
            └── 回退：Canvas Hook markdown
```

### 5.2 后处理中的图片问题

当前 `post_process_markdown()` 中：

```python
# 问题：删除了所有图片链接，包括正常封面图
text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
```

**正确做法**应该是区分处理：
- 删除 base64 混淆水印：`![](data:image/png;base64,...)`
- 保留 URL 图片：`![](https://res.weread.qq.com/...)`

建议修改为：

```python
# 仅删除 base64 内嵌图片（混淆水印），保留 URL 图片
text = re.sub(r"!\[.*?\]\(data:image/[^)]+\)", "", text)
```

---

## 6. 加密图片的完整解密方案

### 6.1 在浏览器中解密（推荐）

利用已登录的浏览器环境调用 `__WRPA__.decode()`：

```python
async def decrypt_wrpa_image(self, image_url: str) -> bytes:
    """解密 WRPA 加密的图片"""
    # 在浏览器中 fetch 图片并尝试 decode
    result = await self._page.evaluate("""
        async (url) => {
            const resp = await fetch(url);
            const buffer = await resp.arrayBuffer();
            const bytes = new Uint8Array(buffer);
            
            // 检查是否为正常图片（未加密）
            if (bytes[0] === 0xFF && bytes[1] === 0xD8) {
                // JPEG SOI，未加密
                return { encrypted: false, data: Array.from(bytes) };
            }
            if (bytes[0] === 0x89 && bytes[1] === 0x50) {
                // PNG header，未加密
                return { encrypted: false, data: Array.from(bytes) };
            }
            
            // 尝试 WRPA 解密
            try {
                const decoded = window.__WRPA__.decode(bytes);
                return { encrypted: true, data: Array.from(decoded) };
            } catch(e) {
                // 解密失败，返回原始数据
                return { encrypted: false, data: Array.from(bytes), error: e.message };
            }
        }
    """, image_url)
    
    return bytes(result['data'])
```

### 6.2 在 Python 中解密（离线）

如果需要在无浏览器环境下解密，需要从 `wpa-1.0.5.js` 中逆向提取密钥和算法。已知信息：

- 算法：AES-CBC
- 填充：PKCS7
- 密钥派生：EvpKDF（CryptoJS 的密钥派生函数）
- 密钥和 IV：被虚拟机混淆保护，需要动态提取

**注意**：由于密钥通过混淆保护且可能随版本更新变化，Python 离线解密方案的维护成本较高，**推荐使用浏览器端解密方案**。

---

## 7. 相关文件索引

| 文件 | 职责 |
|------|------|
| `weread_exporter_lys/hook.js` | Canvas Hook + WRPA 检测 + 逐字文本重组 + drawImage 图片捕获 |
| `weread_exporter_lys/webpage.py` | 页面加载、WRPA 检测、翻页控制、markdown 获取 |
| `weread_exporter_lys/webproxy.py` | 请求拦截（注入 hook.js、阻止日志上报） |
| `weread_exporter_lys/export.py` | markdown 后处理、图片下载、格式转换 |

## 8. 版本历史

| 日期 | 变更 |
|------|------|
| 当前 | WRPA 版本 1.0.5，脚本地址 `https://cdn.weread.qq.com/web/wpa-1.0.5.js` |
