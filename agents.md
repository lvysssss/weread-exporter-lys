# weread-exporter-lys — Project Guide for AI Agents

## Overview

A Python CLI tool that exports books from WeRead (微信读书) and other reading platforms.
It consists of a **user layer** (CLI + interactive wizard), a **platform abstraction layer**, a **crawler layer** (Playwright-based, with WRPA anti-crawl bypass via Canvas hook), and a planned **processing layer** (format conversion: epub/pdf/mobi/etc.).

## Architecture

```
weread_exporter_lys/
├── cli.py              # argparse CLI + interactive wizard
├── config.py           # AppConfig dataclass, JSON config loading
├── __main__.py         # python -m entry point
├── platforms/
│   ├── base.py         # BookPlatform ABC, ExportRequest, ExportResult
│   ├── weread.py       # WeReadPlatform (book-id validation, delegates to crawler)
│   └── __init__.py     # Platform registry: available_platforms(), get_platform()
└── crawler/
    ├── fetcher.py      # WeReadPageFetcher: Playwright browser, page ops, login wait, content extraction
    ├── weread.py       # WeReadCrawler: orchestration (cover → toc → chapters loop)
    ├── state.py        # CrawlState: resume/checkpoint (saved to cache/{id}/state.json)
    ├── extractor.py    # CSS-filtered DOM text extraction, markdown helpers
    ├── images.py       # ImageFilter: watermark removal, dedup, placeholder filtering
    ├── hook.js         # WRPA Canvas hook: intercepts fillText, reconstructs text lines
    └── __init__.py
```

### Data flow

```
CLI --ExportRequest--> WeReadPlatform.export()
  --> WeReadCrawler.crawl()
    --> WeReadPageFetcher (Playwright)
      1. goto reader → ensure_logged_in (or wait for user to scan QR)
      2. save cover → cache/{book_id}/封面.jpg
      3. extract toc → cache/{book_id}/toc.json
      4. for each chapter in toc:
         a. click catalog item → wait for WRPA canvas to render
         b. extract text via wrpaHandler.getMarkdown()
         c. save → cache/{book_id}/content/{N}.md
         d. update state → cache/{book_id}/state.json
    --> ExportResult back to CLI
```

## Key details

### Login & Auth

- Uses Playwright `storage_state` (cookies + localStorage). Default path: `{cache_dir}/auth/weread-storage-state.json`.
- `ensure_logged_in()` detects login by scanning DOM for visible text "登录" (top-bar link). Only `--headless` + missing auth → hard fail. Non-headless → opens browser, prints prompt, waits for login link to disappear.
- **Important**: Auth state may carry a last-read chapter cookie. On first goto, the page may auto-navigate to the wrong chapter. This will need a solution before the first chapter can be reliably the flyleaf.

### WRPA (Canvas anti-crawl)

- Detected via `window.__WRPA__` global. Version is typically `1.0.5`.
- `hook.js` intercepts `HTMLCanvasElement.prototype.getContext('2d')` and `CanvasRenderingContext2D.fillText()`, reassembles text by y-coordinate grouping, exposes `window.wrpaHandler.getMarkdown()` and `getAntiCrawlStatus()`.
- Font-probe lines (`abcdefghijklmnopqrstuvwxyz...`) are filtered out.
- **Critical**: When WRPA is active, DOM `.innerText` contains CSS garbage (from `.preRenderContainer`). Always prefer `wrpaHandler.getMarkdown()` for WRPA-protected books. `extract_chapter_content()` does this: WRPA → hook, non-WRPA → DOM with CSS filtering.
- The hook accumulates text across page renders. Call `wrpaHandler.clearMarkdown()` between chapters to isolate each chapter's text.

### Catalog extraction

- Button: `button.readerControls_item.catalog` or `get_by_role("button", name="目录")`.
- Items: `.readerCatalog .readerCatalog_list_item`.
- Title: `.readerCatalog_list_item_title_text`.
- Level: `readerCatalog_list_item_inner.readerCatalog_list_item_level_N`.
- **Important**: Playwright's `wait_for_selector(state="visible")` may fail for catalog items (off-screen panel). Use JS `querySelector` polling instead.

### Cache structure

```
cache/{book_id}/
├── 封面.jpg
├── toc.json            # [{index, title, level}]
├── state.json          # CrawlState: resume checkpoint
└── content/
    ├── 1.md
    ├── 2.md
    └── ...
```

### Image filtering

- Exclude `data:` URIs (watermarks).
- Exclude URLs containing `/loading_dark.` (placeholders).
- Deduplicate within each chapter (first occurrence kept).
- Output format: `![](url)`.

## CLI

```
python -m weread_exporter_lys \
  --platform weread \
  --url https://weread.qq.com/web/reader/{book_id} \
  --format md|txt|pdf|epub|mobi|azw3 \
  [--cache-dir cache] [--output-dir output] [--delay 1.0] \
  [--auth-state path/to/storage-state.json] \
  [--headless] [--interactive] [--config config.json]
```

## Dependencies

- Python ≥ 3.10
- `playwright` (runtime)
- No other runtime deps (stdlib only for user layer)
- Planned processing layer: `beautifulsoup4`, `markdown`, `weasyprint`

## Testing

```bash
python -m unittest discover -s tests
```

16 tests across `tests/test_user_layer.py` and `tests/test_crawler_layer.py`. Tests use mocks for Playwright; no real browser required.

## What's NOT yet done

1. **Section headers** (分册标题 in catalog, e.g. "史记（第一册）"): These are skipped because clicking them doesn't navigate to a new page. They may contain cover images or metadata. Need DOM + image extraction for these pages. See user request: "分册封面/标题，往往是有内容的，或者图片的。我要求你把内容读下来，放在正确的位置。"

2. **First-chapter ordering**: Auth state cookies may cause the initial page to load at the wrong chapter. Need to force navigation to the flyleaf before starting the crawl loop.

3. **Processing layer**: Merge, pre-process, post-process, convert to epub/pdf/mobi/azw3 (per `设计文档.md` lines 31-50).

4. **WRPA image decryption**: Encrypted images (`res.weread.qq.com`) can be decoded via `window.__WRPA__.decode()`. Currently only filtering and URL capture is implemented (per `wrpa-anti-crawl.md`).

## Design documents

- `设计文档.md` — full architecture and requirements (Chinese)
- `wrpa-anti-crawl.md` — WRPA technical reference (Chinese)
