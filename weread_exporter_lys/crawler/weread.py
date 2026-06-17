from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .fetcher import READER_URL, LoginRequiredError, PlaywrightUnavailableError, WeReadPageFetcher, run_async, write_json
from .state import CrawlState

if TYPE_CHECKING:
    from ..platforms.base import ExportRequest


@dataclass(frozen=True)
class WeReadCrawlerResult:
    ok: bool
    message: str
    book_dir: Path
    content_dir: Path
    warnings: list[str]


@dataclass(frozen=True)
class WeReadCrawlerPaths:
    book_dir: Path
    cover_path: Path
    toc_path: Path
    state_path: Path
    content_dir: Path
    images_dir: Path
    auth_state_path: Path


class WeReadCrawler:
    def crawl(self, request: ExportRequest) -> WeReadCrawlerResult:
        return run_async(self._crawl(request))

    def paths_for(self, request: ExportRequest) -> WeReadCrawlerPaths:
        book_dir = request.cache_dir / request.book_id
        auth_state_path = request.auth_state_path or request.cache_dir / "auth" / "weread-storage-state.json"
        return WeReadCrawlerPaths(
            book_dir=book_dir,
            cover_path=book_dir / "封面.jpg",
            toc_path=book_dir / "toc.json",
            state_path=book_dir / "state.json",
            content_dir=book_dir / "content",
            images_dir=book_dir / "images",
            auth_state_path=auth_state_path,
        )

    async def _crawl(self, request: ExportRequest) -> WeReadCrawlerResult:
        paths = self.paths_for(request)
        paths.content_dir.mkdir(parents=True, exist_ok=True)
        paths.images_dir.mkdir(parents=True, exist_ok=True)
        reader_url = READER_URL.format(book_id=request.book_id)
        state = CrawlState.load(paths.state_path, book_id=request.book_id, reader_url=reader_url)

        try:
            async with WeReadPageFetcher(
                headless=request.headless,
                delay=request.delay,
                auth_state_path=paths.auth_state_path,
            ) as fetcher:
                await fetcher.goto_reader(request.book_id)
                await fetcher.ensure_logged_in()

                if await fetcher.has_blocking_paywall():
                    state.last_error = "页面提示试读结束、购买、会员或登录后可读，请先在浏览器中确认访问权限。"
                    state.save(paths.state_path)
                    return self._result(False, state.last_error, paths, state)

                if not paths.cover_path.exists():
                    cover_saved = await fetcher.save_cover(paths.cover_path)
                    if not cover_saved:
                        state.add_warning("未找到封面，已跳过封面保存。")

                toc = await fetcher.extract_toc()
                if toc:
                    write_json(paths.toc_path, toc)
                    state.toc_path = str(paths.toc_path)
                else:
                    state.add_warning("未能从页面提取目录，将只保存当前可见正文。")

                total = len(toc) if toc else 1
                current_index = max(0, state.current_chapter_index)

                while current_index < total:
                    if toc:
                        await fetcher.clear_wrpa_markdown()
                        if not await fetcher.goto_toc_item(current_index):
                            toc = await fetcher.extract_toc()
                            await fetcher.clear_wrpa_markdown()
                            if not await fetcher.goto_toc_item(current_index):
                                state.last_error = f"未能点击目录第 {current_index + 1} 项。"
                                state.save(paths.state_path)
                                return self._result(False, state.last_error, paths, state)

                    content = await fetcher.extract_chapter_content(images_dir=paths.images_dir)
                    if not content.markdown:
                        state.last_error = "未能提取正文内容；可能需要登录、页面结构变化，或 WRPA hook 尚未捕获到文本。"
                        state.save(paths.state_path)
                        return self._result(False, state.last_error, paths, state)

                    chapter_path = paths.content_dir / f"{current_index + 1}.md"
                    chapter_title = toc[current_index].get("title") if toc else None
                    chapter_text = content.markdown
                    if chapter_title and not chapter_text.startswith(str(chapter_title)):
                        chapter_text = f"# {chapter_title}\n\n{chapter_text}"
                    chapter_path.write_text(chapter_text + "\n", encoding="utf-8")
                    state.mark_completed(chapter_path)
                    state.current_chapter_index = current_index + 1
                    state.last_error = None

                    if content.anti_crawl_status and content.anti_crawl_status.get("hasWRPA"):
                        state.add_warning("检测到 WRPA；已使用 Canvas hook 初版尝试提取文本，图片解密将在后续处理。")

                    state.save(paths.state_path)
                    current_index += 1

                    if not toc and current_index < total:
                        moved = await fetcher.go_next(content.markdown, images_dir=paths.images_dir)
                        if not moved:
                            state.add_warning("未能自动进入下一页，已停止在当前进度。")
                            break

                await fetcher.save_auth_state()
                return self._result(True, f"爬虫层完成，正文已保存到：{paths.content_dir}", paths, state)
        except LoginRequiredError as error:
            state.last_error = str(error)
            state.save(paths.state_path)
            return self._result(False, str(error), paths, state)
        except PlaywrightUnavailableError as error:
            state.last_error = str(error)
            state.save(paths.state_path)
            return self._result(False, str(error), paths, state)
        except Exception as error:
            message = f"爬虫执行失败：{error}"
            state.last_error = message
            state.save(paths.state_path)
            return self._result(False, message, paths, state)

    def _result(
        self,
        ok: bool,
        message: str,
        paths: WeReadCrawlerPaths,
        state: CrawlState,
    ) -> WeReadCrawlerResult:
        return WeReadCrawlerResult(
            ok=ok,
            message=message,
            book_dir=paths.book_dir,
            content_dir=paths.content_dir,
            warnings=list(state.warnings),
        )
