from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..progress import ProgressCallback, ProgressEvent, emit
from .extractor import (
    BODY_SELECTORS,
    IMAGE_SELECTORS,
    ChapterContent,
    html_text_to_markdown,
    merge_rare_chars,
)
from .images import ImageFilter
from .xhtml_source import collect_rare_char_srcs, decode_chapter_responses, xhtml_to_markdown

READER_URL = "https://weread.qq.com/web/reader/{book_id}"
HOOK_PATH = Path(__file__).with_name("hook.js")

_CHAPTER_URL_RE = re.compile(r"/book/chapter/e_(\d+)")


def _looks_like_css(text: str) -> bool:
    if not text:
        return False
    css_chars = text.count("{") + text.count("}") + text.count(";")
    return css_chars > 0 and len(text) / css_chars < 40


class PlaywrightUnavailableError(RuntimeError):
    pass


class LoginRequiredError(RuntimeError):
    pass


class WeReadPageFetcher:
    def __init__(self, *, headless: bool, delay: float, auth_state_path: Path | None = None, login_timeout: float = 180.0, on_progress: ProgressCallback | None = None, crawl_method: str = "xhtml", debug: bool = False):
        self.headless = headless
        self.delay = delay
        self.auth_state_path = auth_state_path
        self.login_timeout = login_timeout
        self.on_progress = on_progress
        self.crawl_method = crawl_method
        self.debug = debug
        self._chapter_responses: dict[int, str] = {}

    async def __aenter__(self) -> "WeReadPageFetcher":
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise PlaywrightUnavailableError(
                "未安装 Playwright。请先执行：pip install -e . && python -m playwright install chromium"
            ) from error
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._launch_browser()
            context_options = {}
            if self.auth_state_path is not None and self.auth_state_path.exists():
                context_options["storage_state"] = str(self.auth_state_path)
            self._context = await self._browser.new_context(**context_options)
            if HOOK_PATH.exists():
                await self._context.add_init_script(path=str(HOOK_PATH))
            self._page = await self._context.new_page()
            self._page.on("response", self._on_chapter_response)
        except Exception as error:
            await self._playwright.stop()
            raise PlaywrightUnavailableError(
                f"Playwright 浏览器启动失败：{error}\n请执行：python -m playwright install chromium"
            ) from error
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if hasattr(self, "_context"):
            await self._context.close()
        if hasattr(self, "_browser"):
            await self._browser.close()
        if hasattr(self, "_playwright"):
            await self._playwright.stop()

    @property
    def page(self):
        return self._page

    async def _on_chapter_response(self, response) -> None:
        try:
            url = response.url
            match = _CHAPTER_URL_RE.search(url)
            if not match:
                return
            if response.status != 200:
                return
            body = await response.text()
        except Exception:
            return
        if not body or body == "{}" or len(body) <= 100:
            return
        self._chapter_responses[int(match.group(1))] = body

    def drain_chapter_responses(self) -> dict[int, str]:
        captured = self._chapter_responses
        self._chapter_responses = {}
        return captured

    def _emit_waiting(self, message: str) -> None:
        emit(self.on_progress, ProgressEvent(kind="waiting", message=message))

    def _debug(self, message: str) -> None:
        if getattr(self, "debug", False):
            import sys
            print(f"[DEBUG] {message}", file=sys.stderr)

    async def _wait_for_chapter_responses(self, timeout: float = 2.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while (
            not self._chapter_responses
            and asyncio.get_event_loop().time() < deadline
        ):
            await asyncio.sleep(0.3)

    async def _launch_browser(self):
        try:
            return await self._playwright.chromium.launch(headless=self.headless)
        except Exception as chromium_error:
            try:
                return await self._playwright.chromium.launch(channel="chrome", headless=self.headless)
            except Exception as chrome_error:
                raise PlaywrightUnavailableError(
                    "Playwright 无法启动浏览器。已尝试 Playwright Chromium 和本地 Chrome。\n"
                    f"Playwright Chromium 错误：{chromium_error}\n"
                    f"本地 Chrome 错误：{chrome_error}\n"
                    "如果要使用 Playwright 自带浏览器，请执行：python -m playwright install chromium"
                ) from chrome_error

    async def goto_reader(self, book_id: str) -> None:
        await self.page.goto(READER_URL.format(book_id=book_id), wait_until="domcontentloaded")
        await self.page.wait_for_timeout(int(self.delay * 1000))

    async def ensure_logged_in(self) -> None:
        if not await self.needs_login():
            await self.save_auth_state()
            return
        if self.headless:
            raise LoginRequiredError(
                "当前需要登录微信读书。首次登录请去掉 --headless，让程序打开浏览器后扫码登录；"
                "登录成功后会保存登录态，后续可继续使用 --headless。"
            )
        print("检测到需要登录微信读书。请在打开的 Chrome 窗口中扫码/确认登录，登录成功后程序会自动继续。")
        self._emit_waiting("等待微信扫码登录...")
        await self.page.wait_for_function(
            """
            () => {
              const hasLogin = Array.from(document.querySelectorAll('*')).some((el) => {
                const tag = el.tagName;
                if (tag === 'BODY' || tag === 'HTML') return false;
                if (el.offsetParent === null) return false;
                return (el.innerText || el.textContent || '').trim() === '登录';
              });
              return !hasLogin;
            }
            """,
            timeout=int(self.login_timeout * 1000),
        )
        await self.page.wait_for_timeout(int(self.delay * 1000))
        await self.page.reload(wait_until="domcontentloaded")
        await self.page.wait_for_timeout(int(self.delay * 1000))
        await self.save_auth_state()

    async def needs_login(self) -> bool:
        return await self.page.evaluate(
            """
            () => {
              return Array.from(document.querySelectorAll('*')).some((el) => {
                const tag = el.tagName;
                if (tag === 'BODY' || tag === 'HTML') return false;
                if (el.offsetParent === null) return false;
                return (el.innerText || el.textContent || '').trim() === '登录';
              });
            }
            """
        )

    async def save_auth_state(self) -> None:
        if self.auth_state_path is None:
            return
        self.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(self.auth_state_path))

    async def page_text(self) -> str:
        return await self.page.evaluate("() => document.body ? document.body.innerText : ''")

    async def has_blocking_paywall(self) -> bool:
        return await self.page.evaluate(
            """
            () => {
              const nodes = Array.from(document.querySelectorAll('.wr_horizontal_reader_needPay_container'));
              return nodes.some((node) => {
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                const text = node.innerText || node.textContent || '';
                return visible && /试读结束|购买|会员|登录后获得/.test(text);
              });
            }
            """
        )

    async def save_cover(self, target: Path) -> bool:
        cover_url = await self.page.evaluate(
            """
            () => {
              const meta = document.querySelector('meta[property="og:image"], meta[name="twitter:image"]');
              if (meta && meta.content) return meta.content;
              const cover = document.querySelector('img[alt="书籍封面"], .wr_bookCover_img');
              return cover ? cover.src : null;
            }
            """
        )
        if not cover_url:
            return False
        response = await self.page.request.get(cover_url)
        if not response.ok:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await response.body())
        return True

    async def extract_toc(self) -> list[dict[str, Any]]:
        try:
            await self.page.wait_for_selector(".readerControls", timeout=10000)
        except Exception:
            pass
        clicked = False
        try:
            btn = self.page.get_by_role("button", name="目录")
            if await btn.count() > 0:
                await btn.first.click()
                clicked = True
        except Exception:
            pass
        if not clicked:
            try:
                btn = self.page.locator("button.readerControls_item.catalog")
                if await btn.count() > 0:
                    await btn.first.click()
                    clicked = True
            except Exception:
                pass
        if not clicked:
            await self.page.evaluate(
                """() => {
                  const b=document.querySelector('button.readerControls_item.catalog');
                  if(b){b.click();return;}
                  const a=Array.from(document.querySelectorAll('button'));
                  const c=a.find(x=>(x.innerText||x.title||'').trim()==='目录');
                  if(c)c.click();
                }"""
            )
        await self.page.wait_for_timeout(int(self.delay * 1000) + 3000)
        self._emit_waiting("正在展开目录...")
        for _ in range(10):
            found = await self.page.evaluate(
                "() => !!document.querySelector('.readerCatalog .readerCatalog_list_item')"
            )
            if found:
                break
            await asyncio.sleep(0.5)
        else:
            return []
        toc = await self.page.evaluate(
            """
            () => Array.from(document.querySelectorAll('.readerCatalog .readerCatalog_list_item')).map((node, index) => {
              const titleNode = node.querySelector('.readerCatalog_list_item_title_text') || node;
              const title = (titleNode.innerText || titleNode.textContent || '').trim();
              const inner = node.querySelector('.readerCatalog_list_item_inner');
              const levelMatch = inner && String(inner.className || '').match(/readerCatalog_list_item_level_(\\d+)/);
              return { index, title, level: levelMatch ? Number(levelMatch[1]) : null };
            }).filter((item) => item.title)
            """
        )
        return list(toc or [])

    async def goto_toc_item(self, index: int) -> bool:
        try:
            items = self.page.locator(".readerCatalog .readerCatalog_list_item")
            count = await items.count()
            if index >= count:
                return False
            await items.nth(index).click()
            await self.page.wait_for_timeout(800)
            return True
        except Exception:
            return False

    async def wait_for_chapter_render(self, timeout: float = 30.0) -> bool:
        deadline = asyncio.get_event_loop().time() + timeout
        self._emit_waiting("等待整章 canvas 渲染...")
        while asyncio.get_event_loop().time() < deadline:
            if await self.page.evaluate("() => window.__wrpaRenderComplete === true"):
                return True
            stable = await self.page.evaluate("() => window.__wrpaRenderStable === true")
            text = await self.page.evaluate(
                "() => window.wrpaHandler ? window.wrpaHandler.getMarkdown() : ''"
            )
            if stable and text and len(text) > 80 and not text.startswith(".readerChapterContent"):
                await asyncio.sleep(0.5)
                if await self.page.evaluate("() => window.__wrpaRenderComplete === true"):
                    return True
                return True
            await asyncio.sleep(0.3)
        return False

    async def clear_wrpa_markdown(self) -> None:
        await self.page.evaluate("() => window.wrpaHandler && window.wrpaHandler.clearMarkdown()")

    async def _download_image(self, url: str, target: Path) -> bool:
        if target.exists():
            return True
        try:
            response = await self.page.request.get(url)
            if response.ok:
                target.write_bytes(await response.body())
                return True
        except Exception:
            pass
        return False

    async def _extract_via_xhtml(
        self, *, images_dir: Path | None, anti_crawl_status: dict | None = None,
    ) -> ChapterContent | None:
        responses = self.drain_chapter_responses()
        self._debug(f"_extract_via_xhtml: drained {len(responses)} responses")
        if getattr(self, "debug", False) and responses:
            import os
            dbgdir = "cache/_debug_responses"
            os.makedirs(dbgdir, exist_ok=True)
            for k, v in responses.items():
                p = os.path.join(dbgdir, f"e_{k}.txt")
                with open(p, "w", encoding="utf-8") as fh: fh.write(v)
                self._debug(f"  saved e_{k} ({len(v)} chars) to {p}")
        if not responses:
            return None
        xhtml = decode_chapter_responses(responses)
        self._debug(f"_extract_via_xhtml: decoded xhtml len={len(xhtml) if xhtml else 0}")
        if getattr(self, "debug", False) and xhtml:
            with open("cache/_debug_responses/decoded.xhtml", "w", encoding="utf-8") as fh: fh.write(xhtml)
            self._debug("  saved decoded.xhtml")
        if not xhtml:
            return None
        markdown, rare_srcs = xhtml_to_markdown(xhtml, page_url=self.page.url)
        self._debug(f"_extract_via_xhtml: markdown len={len(markdown)}, rare_srcs={len(rare_srcs)}")
        if not markdown or len(markdown) < 30:
            return None
        if images_dir is not None:
            images_dir.mkdir(parents=True, exist_ok=True)
            src_to_stem = collect_rare_char_srcs(xhtml)
            total = len(src_to_stem)
            if total:
                self._emit_waiting(f"下载生僻字图片 0/{total}...")
            for index, (src, stem) in enumerate(src_to_stem.items()):
                target = images_dir / f"{stem}.png"
                await self._download_image(src, target)
                self._emit_waiting(f"下载生僻字图片 {index + 1}/{total}...")
        return ChapterContent(
            markdown=markdown, source="xhtml", anti_crawl_status=anti_crawl_status or {},
            xhtml_source=xhtml,
        )

    async def extract_chapter_content(self, *, images_dir: Path | None = None, chapter_index: int | None = None) -> ChapterContent:
        anti_crawl_status = await self.detect_anti_crawl()
        has_wrpa = anti_crawl_status.get("hasWRPA") or anti_crawl_status.get("hasCanvasContent")
        self._debug(f"extract: has_wrpa={has_wrpa}, crawl_method={self.crawl_method}, ch_responses={len(self._chapter_responses)}")

        # ── Cache hit: saved XHTML source already on disk ──────────────
        # When resuming a previous crawl the raw XHTML was persisted; parse
        # it directly instead of re-navigating / re-capturing.
        if chapter_index is not None and self.crawl_method == "xhtml":
            src_path = self._xhtml_src_path(images_dir, chapter_index)
            if src_path is not None and src_path.exists():
                xhtml = src_path.read_text(encoding="utf-8")
                markdown, _ = xhtml_to_markdown(xhtml, page_url=self.page.url)
                if markdown:
                    return ChapterContent(markdown=markdown, source="xhtml", anti_crawl_status=anti_crawl_status)

        if has_wrpa:
            if self.crawl_method == "xhtml":
                await self._wait_for_chapter_responses(timeout=2.0)
                self._debug(f"extract: after wait, ch_responses={len(self._chapter_responses)}")
                if self._chapter_responses:
                    try:
                        content = await self._extract_via_xhtml(
                            images_dir=images_dir, anti_crawl_status=anti_crawl_status,
                        )
                        if content is not None:
                            return content
                    except Exception as error:
                        emit(self.on_progress, ProgressEvent(
                            kind="warning",
                            message=f"XHTML 源解析失败，回退 canvas 路径：{error}",
                        ))

            # 2. Canvas coordinate-merge (legacy)
            wrpa_markdown = await self.page.evaluate(
                "() => window.wrpaHandler ? window.wrpaHandler.getMarkdown() : ''"
            )
            if not wrpa_markdown or len(wrpa_markdown) < 80:
                await self.wait_for_chapter_render()
                wrpa_markdown = await self.page.evaluate(
                    "() => window.wrpaHandler ? window.wrpaHandler.getMarkdown() : ''"
                )
            images = await self._image_urls(IMAGE_SELECTORS)

            rare_srcs: set[str] = set()
            rare_chars: list[dict] = []
            if images_dir is not None:
                raw_rare = await self._rare_char_images()
                if raw_rare:
                    rare_chars = await self._download_rare_chars(raw_rare, images_dir)
                    rare_srcs = {rc.get("src", "") for rc in raw_rare if rc.get("src")}

            if rare_chars:
                line_records = await self.page.evaluate(
                    "() => window.wrpaHandler ? window.wrpaHandler.getLinesWithCoords() : []"
                )
                body_markdown = merge_rare_chars(list(line_records or []), rare_chars)
                tail_images = ImageFilter().markdown_lines(
                    images, base_url=self.page.url, exclude=rare_srcs,
                )
                wrpa_markdown = "\n\n".join(p for p in (body_markdown, "\n".join(tail_images)) if p).strip()
            else:
                wrpa_markdown = html_text_to_markdown(wrpa_markdown, images, base_url=self.page.url)

            if not wrpa_markdown or len(wrpa_markdown) < 30:
                self._debug(f"extract: wrpa_markdown too short, trying DOM")
                text = await self._first_text(BODY_SELECTORS)
                self._debug(f"extract: _first_text len={len(text) if text else 0}, looks_css={_looks_like_css(text) if text else False}")
                if text and len(text) > 100 and not _looks_like_css(text):
                    markdown = html_text_to_markdown(text, images, base_url=self.page.url)
                    return ChapterContent(markdown=markdown, source="dom", anti_crawl_status=anti_crawl_status)

            return ChapterContent(markdown=wrpa_markdown, source="wrpa", anti_crawl_status=anti_crawl_status)

        # Non-WRPA page — standard DOM
        text = await self._first_text(BODY_SELECTORS)
        images = await self._image_urls(IMAGE_SELECTORS)
        if text and _looks_like_css(text):
            text = ""
        markdown = html_text_to_markdown(text, images, base_url=self.page.url)
        if not markdown or len(markdown) < 30:
            wrpa_markdown = await self.page.evaluate(
                "() => window.wrpaHandler ? window.wrpaHandler.getMarkdown() : ''"
            )
            wrpa_markdown = html_text_to_markdown(wrpa_markdown, images, base_url=self.page.url)
            return ChapterContent(markdown=wrpa_markdown, source="wrpa", anti_crawl_status=anti_crawl_status)
        return ChapterContent(markdown=markdown, source="dom", anti_crawl_status=anti_crawl_status)

    def _xhtml_src_path(self, images_dir: Path | None, chapter_index: int) -> Path | None:
        """Path to a persisted raw XHTML source file for *chapter_index*.

        Stored under ``{method_dir}/xhtml_src/{N}.xhtml`` so resume runs can
        detect cache hits and skip re-capture.
        """
        if images_dir is None:
            return None
        return images_dir.parent / "xhtml_src" / f"{chapter_index}.xhtml"

    async def detect_anti_crawl(self) -> dict[str, Any]:
        return await self.page.evaluate(
            """
            () => window.wrpaHandler
              ? window.wrpaHandler.getAntiCrawlStatus()
              : { hasWRPA: !!window.__WRPA__, hasCanvasHandler: false }
            """
        )

    async def detect_page_button(self) -> str | None:
        return await self.page.evaluate(
            """
            () => {
              const texts = ['下一页', '下一章', '下章'];
              const nodes = Array.from(document.querySelectorAll('button'));
              const target = nodes.find((node) =>
                texts.some((text) => (node.innerText || node.title || '').trim() === text)
              );
              return target ? (target.innerText || target.title || '').trim() : null;
            }
            """
        )

    async def extract_full_chapter(self, *, images_dir: Path | None = None, chapter_index: int | None = None) -> ChapterContent:
        first = await self.extract_chapter_content(images_dir=images_dir, chapter_index=chapter_index)
        if first.source == "xhtml":
            return first
        parts = [first.markdown] if first.markdown else []
        pages = 0
        while True:
            btn = await self.detect_page_button()
            if btn != '下一页':
                break
            await self.clear_wrpa_markdown()
            try:
                await self.page.locator('button.readerFooter_button:has-text("下一页")').click(timeout=2000)
            except Exception:
                await self.page.keyboard.press("ArrowRight")
            await self.wait_for_chapter_render(timeout=10.0)
            page_content = await self.extract_chapter_content(images_dir=images_dir)
            if page_content.markdown:
                parts.append(page_content.markdown)
            pages += 1
            if pages >= 200:
                break
        merged = "\n\n".join(p for p in parts if p).strip()
        return ChapterContent(markdown=merged, source=first.source, anti_crawl_status=first.anti_crawl_status)

    async def go_next(self, previous_markdown: str, *, images_dir: Path | None = None) -> bool:
        await self.clear_wrpa_markdown()
        clicked = await self.page.evaluate(
            """
            () => {
              const texts = ['下一页', '下一章', '下章'];
              const nodes = Array.from(document.querySelectorAll('button'));
              const target = nodes.find((node) => texts.some((text) => (node.innerText || node.title || '').trim().includes(text)));
              if (!target) return false;
              target.click();
              return true;
            }
            """
        )
        if not clicked:
            await self.page.keyboard.press("ArrowRight")
        await self.page.wait_for_timeout(int(self.delay * 1000))
        current = await self.extract_chapter_content(images_dir=images_dir)
        return bool(current.markdown and current.markdown != previous_markdown)

    async def _first_text(self, selectors: tuple[str, ...]) -> str:
        return await self.page.evaluate(
            """
            (selectors) => {
              for (const selector of selectors) {
                const nodes = Array.from(document.querySelectorAll(selector));
                const text = nodes
                  .filter((node) => {
                    const cls = String(node.className || '');
                    const tag = node.tagName;
                    if (tag === 'STYLE') return false;
                    if (cls.includes('preRender')) return false;
                    return true;
                  })
                  .map((node) => {
                    const inner = node.innerText || '';
                    if (inner.trim()) return inner;
                    const clone = node.cloneNode(true);
                    clone.querySelectorAll('style, script').forEach((e) => e.remove());
                    return clone.textContent || '';
                  })
                  .join('\\n').trim();
                if (text && text.length > 10) return text;
              }
              return '';
            }
            """,
            list(selectors),
        )

    async def _image_urls(self, selectors: tuple[str, ...]) -> list[str]:
        urls = await self.page.evaluate(
            """
            (selectors) => {
              const seen = new Set();
              const urls = [];
              for (const selector of selectors) {
                for (const img of document.querySelectorAll(selector)) {
                  const src = img.currentSrc || img.src || img.getAttribute('data-src') || '';
                  if (src && !seen.has(src)) { seen.add(src); urls.push(src); }
                }
                if (urls.length) break;
              }
              return urls;
            }
            """,
            list(selectors),
        )
        return list(urls or [])

    async def _rare_char_images(self) -> list[dict]:
        records = await self.page.evaluate(
            """
            () => {
              const scale = (window.wrpaHandler && window.wrpaHandler.getCanvasScale)
                ? window.wrpaHandler.getCanvasScale() : 1;
              const out = [];
              const imgs = document.querySelectorAll('.passage-content img.h-pic');
              for (const img of imgs) {
                const src = img.src || img.getAttribute('data-src') || '';
                if (!src.includes('res.weread.qq.com/wrepub/')) continue;
                const transform = img.getAttribute('style') || '';
                const m = transform.match(/translate\\(\\s*([-\\d.]+)px\\s*,\\s*([-\\d.]+)px\\s*\\)/);
                const xCss = m ? parseFloat(m[1]) : null;
                const yCss = m ? parseFloat(m[2]) : null;
                out.push({
                  src, data_wr_id: img.getAttribute('data-wr-id') || null,
                  x: (xCss != null) ? Math.round(xCss / scale * 100) / 100 : null,
                  y: (yCss != null) ? Math.round(yCss / scale * 100) / 100 : null,
                  xCss, yCss, scale,
                  width: img.getAttribute('data-w') || null,
                  ratio: img.getAttribute('data-ratio') || null,
                });
              }
              return out;
            }
            """
        )
        return list(records or [])

    async def _download_rare_chars(self, rare_chars: list[dict], images_dir: Path) -> list[dict]:
        if not rare_chars:
            return []
        images_dir.mkdir(parents=True, exist_ok=True)
        total_rare = len(rare_chars)
        if total_rare:
            self._emit_waiting(f"下载生僻字图片 0/{total_rare}...")
        enriched: list[dict] = []
        for index, rc in enumerate(rare_chars):
            src = rc.get("src")
            if not src:
                continue
            name = rc.get("data_wr_id") or f"rare_{index}"
            target = images_dir / f"{name}.png"
            if not target.exists():
                try:
                    response = await self.page.request.get(src)
                    if response.ok:
                        target.write_bytes(await response.body())
                except Exception:
                    pass
            rc = dict(rc)
            rc["local_path"] = f"../images/{name}.png"
            enriched.append(rc)
            if total_rare:
                self._emit_waiting(f"下载生僻字图片 {index + 1}/{total_rare}...")
        return enriched


def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Cannot run crawler inside an existing asyncio event loop")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")