from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..progress import ProgressCallback, ProgressEvent, emit
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
    def crawl(self, request: ExportRequest, on_progress: ProgressCallback | None = None) -> WeReadCrawlerResult:
        callback = on_progress or request.on_progress
        return run_async(self._crawl(request, callback))

    @staticmethod
    def _max_saved_chapter(content_dir: Path) -> int:
        if not content_dir.is_dir():
            return 0
        max_n = 0
        for f in content_dir.iterdir():
            if f.is_file() and f.suffix == ".md":
                try:
                    n = int(f.stem)
                    if n > max_n:
                        max_n = n
                except ValueError:
                    pass
        return max_n

    def paths_for(self, request: ExportRequest) -> WeReadCrawlerPaths:
        book_dir = request.cache_dir / request.book_id
        method_dir = book_dir / request.crawl_method
        auth_state_path = request.auth_state_path or request.cache_dir / "auth" / "weread-storage-state.json"
        return WeReadCrawlerPaths(
            book_dir=book_dir,
            cover_path=book_dir / "封面.jpg",
            toc_path=book_dir / "toc.json",
            state_path=method_dir / "state.json",
            content_dir=method_dir / "content",
            images_dir=method_dir / "images",
            auth_state_path=auth_state_path,
        )

    async def _crawl(self, request: ExportRequest, on_progress: ProgressCallback | None = None) -> WeReadCrawlerResult:
        paths = self.paths_for(request)
        paths.content_dir.mkdir(parents=True, exist_ok=True)
        paths.images_dir.mkdir(parents=True, exist_ok=True)
        reader_url = READER_URL.format(book_id=request.book_id)
        state = CrawlState.load(paths.state_path, book_id=request.book_id, reader_url=reader_url)

        def warn(message: str) -> None:
            if message in state.warnings:
                return
            state.add_warning(message)
            emit(on_progress, ProgressEvent(kind="warning", message=message))

        try:
            async with WeReadPageFetcher(
                headless=request.headless,
                delay=request.delay,
                auth_state_path=paths.auth_state_path,
                on_progress=on_progress,
                crawl_method=request.crawl_method,
                debug=request.debug,
            ) as fetcher:
                await fetcher.goto_reader(request.book_id)
                await fetcher.ensure_logged_in()

                if await fetcher.has_blocking_paywall():
                    state.last_error = "页面提示试读结束、购买、会员或登录后可读，请先在浏览器中确认访问权限。"
                    state.save(paths.state_path)
                    return self._finish(False, state.last_error, paths, state, on_progress)

                if not paths.cover_path.exists():
                    cover_saved = await fetcher.save_cover(paths.cover_path)
                    emit(on_progress, ProgressEvent(
                        kind="cover", ok=cover_saved,
                        message="保存封面 ✓" if cover_saved else "未找到封面，已跳过封面保存。",
                    ))
                    if not cover_saved:
                        warn("未找到封面，已跳过封面保存。")
                else:
                    emit(on_progress, ProgressEvent(kind="cover", ok=True, message="封面已存在，跳过保存。"))

                toc = await fetcher.extract_toc()
                if toc:
                    write_json(paths.toc_path, toc)
                    state.toc_path = str(paths.toc_path)
                    emit(on_progress, ProgressEvent(kind="toc", total=len(toc)))
                else:
                    warn("未能从页面提取目录，将只保存当前可见正文。")
                    emit(on_progress, ProgressEvent(kind="toc", total=1))

                # Extract book metadata (title / author / description) from page
                # meta tags. Saved as meta.json in the shared book_dir so both
                # crawl methods and the processing pipeline can read it.
                meta_path = paths.book_dir / "meta.json"
                if not meta_path.exists():
                    try:
                        meta = await fetcher.extract_book_meta()
                        if meta and (meta.get("title") or meta.get("author")):
                            write_json(meta_path, meta)
                    except Exception:
                        pass

                total = len(toc) if toc else 1
                if request.max_chapters and request.max_chapters < total:
                    total = request.max_chapters
                emit(on_progress, ProgressEvent(kind="started", total=total))
                current_index = self._max_saved_chapter(paths.content_dir)

                while current_index < total:
                    chapter_number = current_index + 1  # 1-based chapter number for filenames
                    chapter_title = toc[current_index].get("title") if toc else None
                    emit(on_progress, ProgressEvent(
                        kind="chapter_started",
                        index=chapter_number,
                        total=total,
                        title=chapter_title,
                    ))

                    if toc:
                        await fetcher.clear_wrpa_markdown()
                        if not await fetcher.goto_toc_item(current_index):
                            toc = await fetcher.extract_toc()
                            await fetcher.clear_wrpa_markdown()
                            if not await fetcher.goto_toc_item(current_index):
                                state.last_error = f"未能点击目录第 {chapter_number} 项。"
                                state.save(paths.state_path)
                                return self._finish(False, state.last_error, paths, state, on_progress)

                    content = await fetcher.extract_full_chapter(
                        images_dir=paths.images_dir, chapter_index=chapter_number,
                    )
                    if not content.markdown:
                        state.last_error = "未能提取正文内容；可能需要登录、页面结构变化，或 WRPA hook 尚未捕获到文本。"
                        state.save(paths.state_path)
                        return self._finish(False, state.last_error, paths, state, on_progress)

                    # Save chapter markdown.
                    chapter_path = paths.content_dir / f"{chapter_number}.md"
                    chapter_text = content.markdown
                    if chapter_title and not chapter_text.startswith(str(chapter_title)):
                        chapter_text = f"# {chapter_title}\n\n{chapter_text}"
                    chapter_path.write_text(chapter_text + "\n", encoding="utf-8")

                    # Persist raw XHTML source so future resume runs detect a
                    # cache hit and skip re-capture.
                    if content.xhtml_source:
                        xhtml_dir = paths.content_dir.parent / "xhtml_src"
                        xhtml_dir.mkdir(parents=True, exist_ok=True)
                        (xhtml_dir / f"{chapter_number}.xhtml").write_text(
                            content.xhtml_source, encoding="utf-8",
                        )

                    state.mark_completed(chapter_path)
                    state.current_chapter_index = chapter_number
                    state.last_error = None

                    if content.anti_crawl_status and content.anti_crawl_status.get("hasWRPA"):
                        warn("检测到 WRPA；已使用 Canvas hook 初版尝试提取文本，图片解密将在后续处理。")

                    state.save(paths.state_path)
                    emit(on_progress, ProgressEvent(
                        kind="chapter_saved",
                        index=chapter_number,
                        total=total,
                        title=chapter_title,
                    ))
                    current_index += 1

                    if not toc and current_index < total:
                        moved = await fetcher.go_next(content.markdown, images_dir=paths.images_dir)
                        if not moved:
                            warn("未能自动进入下一页，已停止在当前进度。")
                            break

                await fetcher.save_auth_state()
                return self._finish(
                    True,
                    f"爬虫层完成，正文已保存到：{paths.content_dir}",
                    paths, state, on_progress,
                )
        except LoginRequiredError as error:
            state.last_error = str(error)
            state.save(paths.state_path)
            return self._finish(False, str(error), paths, state, on_progress)
        except PlaywrightUnavailableError as error:
            state.last_error = str(error)
            state.save(paths.state_path)
            return self._finish(False, str(error), paths, state, on_progress)
        except Exception as error:
            message = f"爬虫执行失败：{error}"
            state.last_error = message
            state.save(paths.state_path)
            return self._finish(False, message, paths, state, on_progress)

    def _finish(
        self,
        ok: bool,
        message: str,
        paths: WeReadCrawlerPaths,
        state: CrawlState,
        on_progress: ProgressCallback | None = None,
    ) -> WeReadCrawlerResult:
        emit(on_progress, ProgressEvent(kind="finished", ok=ok, message=message))
        return self._result(ok, message, paths, state)

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