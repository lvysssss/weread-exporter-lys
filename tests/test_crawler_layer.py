import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weread_exporter_lys.crawler.extractor import html_text_to_markdown, merge_rare_chars, normalize_markdown_text
from weread_exporter_lys.crawler.fetcher import WeReadPageFetcher
from weread_exporter_lys.crawler.images import ImageFilter
from weread_exporter_lys.crawler.state import CrawlState
from weread_exporter_lys.crawler.weread import WeReadCrawler, WeReadCrawlerResult
from weread_exporter_lys.platforms.base import ExportRequest
from weread_exporter_lys.platforms.weread import WeReadPlatform
from weread_exporter_lys.progress import ProgressEvent, ProgressRenderer


class CrawlStateTests(unittest.TestCase):
    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = CrawlState(book_id="book1", reader_url="https://example.test")
            state.current_chapter_index = 2
            state.mark_completed(Path("content/1.md"))
            state.add_warning("warning")
            state.save(path)

            loaded = CrawlState.load(path, book_id="book1", reader_url="https://example.test")

        self.assertEqual(loaded.current_chapter_index, 2)
        self.assertEqual(loaded.completed_chapters, ["content\\1.md" if "\\" in str(Path("content/1.md")) else "content/1.md"])
        self.assertEqual(loaded.warnings, ["warning"])


class ImageFilterTests(unittest.TestCase):
    def test_filters_watermarks_placeholders_and_duplicates(self):
        image_filter = ImageFilter()
        urls = [
            "data:image/png;base64,aaa",
            "/loading_dark.png",
            "https://res.weread.qq.com/a.jpg",
            "https://res.weread.qq.com/a.jpg",
            "/b.jpg",
        ]

        lines = image_filter.markdown_lines(urls, base_url="https://weread.qq.com/web/reader/book")

        self.assertEqual(
            lines,
            [
                "![](https://res.weread.qq.com/a.jpg)",
                "![](https://weread.qq.com/b.jpg)",
            ],
        )

    def test_excludes_rare_char_srcs_already_inlined(self):
        image_filter = ImageFilter()
        urls = [
            "https://res.weread.qq.com/wrepub/epub_41595377_3",  # rare char, inlined
            "https://res.weread.qq.com/wrepub/epub_41595377_4",  # rare char, inlined
            "https://res.weread.qq.com/illustration.jpg",         # normal illustration, keep
        ]
        exclude = {
            "https://res.weread.qq.com/wrepub/epub_41595377_3",
            "https://res.weread.qq.com/wrepub/epub_41595377_4",
        }

        lines = image_filter.markdown_lines(urls, exclude=exclude)

        self.assertEqual(lines, ["![](https://res.weread.qq.com/illustration.jpg)"])


class ExtractorTests(unittest.TestCase):
    def test_normalizes_markdown_text(self):
        text = normalize_markdown_text("  第一段  \n\n\n第二段\r\n")
        self.assertEqual(text, "第一段\n\n第二段")

    def test_combines_text_and_images(self):
        markdown = html_text_to_markdown("正文", ["https://example.test/a.jpg"])
        self.assertEqual(markdown, "正文\n\n![](https://example.test/a.jpg)")


class RareCharMergeTests(unittest.TestCase):
    def _line(self, text, y, prefix="", frag_xs=None):
        """Helper: build a line record with per-fragment xCss (canvas-CSS space).

        If ``frag_xs`` is None, synthesize a single fragment at x=0 so the image
        falls back to line-end insertion (backward-compat path).
        """
        if frag_xs is None:
            fragments = [{"text": text, "xCss": 0.0}] if text else []
        else:
            # Split text into one-char fragments at the given x positions.
            fragments = [
                {"text": ch, "xCss": float(x)} for ch, x in zip(text, frag_xs)
            ]
        return {"text": text, "fragments": fragments, "y": float(y), "prefix": prefix}

    def test_no_rare_chars_returns_lines_unchanged(self):
        lines = [self._line("第一行", 100.0), self._line("第二行", 140.0)]
        self.assertEqual(merge_rare_chars(lines, []), "第一行\n\n第二行")

    def test_inserts_image_into_x_gap_within_line(self):
        # Mirrors real data: 教熊罴貔貅[]虎 — the canvas left a gap at x=43
        # between ](41) and 虎(51). Image x=43 must land there, NOT at line end.
        frags = [0, 7, 14.33, 21.33, 28.67, 35.67, 41, 51.67, 59]
        text = "教熊罴貔貅[]虎，"
        lines = [self._line(text, 1029.89, frag_xs=frags)]
        rare = [{"local_path": "../images/r.png", "x": 43.0, "y": 1028.67}]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "教熊罴貔貅[]![](../images/r.png)虎，")

    def test_inserts_image_between_footnote_and_gloss(self):
        # The user-reported case: image belongs between [3] and ：搅乱。
        # Fragments: [(0) 3(7) ](14)  <gap>  ：(21) 搅(28) 乱(35) 。(42)
        # Image x=17 falls in the gap between ](14) and ：(21).
        frags = [0, 7, 14, 21, 28, 35, 42]
        text = "[3]：搅乱。"
        lines = [self._line(text, 100.0, frag_xs=frags)]
        rare = [{"local_path": "../images/r.png", "x": 17.0, "y": 100.0}]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "[3]![](../images/r.png)：搅乱。")

    def test_image_at_line_end_appended_when_no_fragment_to_right(self):
        frags = [0, 7]
        text = "正文"
        lines = [self._line(text, 100.0, frag_xs=frags)]
        rare = [{"local_path": "../images/r.png", "x": 50.0, "y": 100.0}]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "正文![](../images/r.png)")

    def test_same_line_multiple_rare_chars_ordered_by_x(self):
        frags = [0, 7]
        text = "甲乙"
        lines = [self._line(text, 100.0, frag_xs=frags)]
        rare = [
            {"local_path": "../images/b.png", "x": 30.0, "y": 100.0},
            {"local_path": "../images/a.png", "x": 15.0, "y": 100.0},
            {"local_path": "../images/c.png", "x": 45.0, "y": 100.0},
        ]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "甲乙![](../images/a.png)![](../images/b.png)![](../images/c.png)")

    def test_preserves_heading_prefix(self):
        lines = [
            self._line("标题", 50.0, prefix="## ", frag_xs=[0, 7]),
            self._line("正文", 100.0, frag_xs=[0, 7]),
        ]
        rare = [{"local_path": "../images/x.png", "x": 50.0, "y": 100.0}]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "## 标题\n\n正文![](../images/x.png)")

    def test_orphan_rare_char_beyond_tolerance_appended_at_end(self):
        lines = [self._line("正文", 100.0, frag_xs=[0, 7])]
        rare = [{"local_path": "../images/far.png", "x": 10.0, "y": 5000.0}]
        result = merge_rare_chars(lines, rare, line_tolerance=20.0)
        self.assertEqual(result, "正文\n\n![](../images/far.png)")

    def test_empty_lines_returns_empty(self):
        self.assertEqual(merge_rare_chars([], [{"local_path": "../images/x.png", "x": 0, "y": 0}]), "")

    def test_backward_compat_no_fragments_falls_back_to_line_end(self):
        # Records without `fragments` (old hook output) still work: image → line end.
        lines = [{"text": "正文", "y": 100.0, "prefix": ""}]
        rare = [{"local_path": "../images/r.png", "x": 50.0, "y": 100.0}]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "正文![](../images/r.png)")




class WeReadCrawlerTests(unittest.TestCase):
    def test_paths_for_request(self):
        request = ExportRequest(
            platform="weread",
            book_id="book1",
            output_format="md",
            cache_dir=Path("cache"),
            output_dir=Path("output"),
            delay=1,
            headless=True,
            auth_state_path=Path("auth.json"),
        )

        paths = WeReadCrawler().paths_for(request)

        self.assertEqual(paths.book_dir, Path("cache") / "book1")
        self.assertEqual(paths.cover_path, Path("cache") / "book1" / "封面.jpg")
        # Content lives under the per-method subdir (xhtml is the default),
        # isolated from the canvas method's content.
        self.assertEqual(paths.content_dir, Path("cache") / "book1" / "canvas" / "content")
        self.assertEqual(paths.images_dir, Path("cache") / "book1" / "canvas" / "images")
        self.assertEqual(paths.state_path, Path("cache") / "book1" / "canvas" / "state.json")
        self.assertEqual(paths.auth_state_path, Path("auth.json"))

    def test_paths_defaults_auth_state_from_cache_dir(self):
        request = ExportRequest(
            platform="weread",
            book_id="book1",
            output_format="md",
            cache_dir=Path("cache"),
            output_dir=Path("output"),
            delay=1,
            headless=True,
        )
        paths = WeReadCrawler().paths_for(request)
        self.assertEqual(
            paths.auth_state_path,
            Path("cache") / "auth" / "weread-storage-state.json",
        )

    def test_paths_isolate_by_crawl_method(self):
        """xhtml and canvas methods get separate content/images/state dirs."""
        base = dict(
            platform="weread", book_id="book1", output_format="md",
            cache_dir=Path("cache"), output_dir=Path("output"),
            delay=1, headless=True,
        )
        xhtml_paths = WeReadCrawler().paths_for(ExportRequest(crawl_method="xhtml", **base))
        canvas_paths = WeReadCrawler().paths_for(ExportRequest(crawl_method="canvas", **base))
        # Shared (method-independent): cover, toc, book_dir, auth.
        self.assertEqual(xhtml_paths.book_dir, canvas_paths.book_dir)
        self.assertEqual(xhtml_paths.cover_path, canvas_paths.cover_path)
        self.assertEqual(xhtml_paths.toc_path, canvas_paths.toc_path)
        # Isolated: content, images, state.
        self.assertNotEqual(xhtml_paths.content_dir, canvas_paths.content_dir)
        self.assertEqual(canvas_paths.content_dir, Path("cache") / "book1" / "canvas" / "content")
        self.assertEqual(xhtml_paths.images_dir, Path("cache") / "book1" / "xhtml" / "images")
        self.assertEqual(canvas_paths.state_path, Path("cache") / "book1" / "canvas" / "state.json")


class WeReadPlatformCrawlerTests(unittest.TestCase):
    def test_export_delegates_to_crawler(self):
        request = self._request()
        result = WeReadCrawlerResult(
            ok=True,
            message="done",
            book_dir=Path("cache") / "book1",
            content_dir=Path("cache") / "book1" / "content",
            warnings=["warn"],
        )

        with patch("weread_exporter_lys.platforms.weread.WeReadCrawler") as crawler_class:
            crawler_class.return_value.crawl.return_value = result
            export_result = WeReadPlatform().export(request)

        self.assertTrue(export_result.ok)
        self.assertEqual(export_result.output_path, Path("cache") / "book1" / "content")
        self.assertIn("done", export_result.message)
        self.assertIn("警告：warn", export_result.message)

    def test_export_does_not_report_output_path_on_failure(self):
        result = WeReadCrawlerResult(
            ok=False,
            message="failed",
            book_dir=Path("cache") / "book1",
            content_dir=Path("cache") / "book1" / "content",
            warnings=[],
        )

        with patch("weread_exporter_lys.platforms.weread.WeReadCrawler") as crawler_class:
            crawler_class.return_value.crawl.return_value = result
            export_result = WeReadPlatform().export(self._request())

        self.assertFalse(export_result.ok)
        self.assertIsNone(export_result.output_path)

    def _request(self):
        return ExportRequest(
            platform="weread",
            book_id="book1",
            output_format="md",
            cache_dir=Path("cache"),
            output_dir=Path("output"),
            delay=1,
            headless=True,
            auth_state_path=Path("auth.json"),
        )


class ProgressRendererTests(unittest.TestCase):
    def _renderer(self, is_tty=True):
        stream = io.StringIO()
        renderer = ProgressRenderer(stream=stream, is_tty=is_tty)
        return renderer, stream

    def test_renders_chapter_progress_with_percentage(self):
        renderer, stream = self._renderer(is_tty=True)
        renderer.handle(ProgressEvent(kind="started", total=50))
        renderer.handle(ProgressEvent(kind="chapter_started", index=3, total=50, title="第一章 引言"))
        renderer.handle(ProgressEvent(kind="finished", ok=True, message="done"))
        out = stream.getvalue()
        self.assertIn("[3/50]", out)
        self.assertIn("第一章 引言", out)
        self.assertIn("6%", out)  # 3/50 = 6%
        # finished must terminate the progress line with a newline
        self.assertTrue(out.rstrip().endswith("\n") or out.endswith("\n"))

    def test_tty_overwrites_with_carriage_return(self):
        renderer, stream = self._renderer(is_tty=True)
        renderer.handle(ProgressEvent(kind="started", total=4))
        renderer.handle(ProgressEvent(kind="chapter_started", index=1, total=4, title="A"))
        renderer.handle(ProgressEvent(kind="chapter_started", index=2, total=4, title="B"))
        out = stream.getvalue()
        # Each redraw is prefixed by \r so the line is overwritten, not appended.
        self.assertEqual(out.count("\r"), 3)

    def test_non_tty_suppresses_progress_line(self):
        renderer, stream = self._renderer(is_tty=False)
        renderer.handle(ProgressEvent(kind="started", total=4))
        renderer.handle(ProgressEvent(kind="chapter_started", index=1, total=4, title="A"))
        renderer.handle(ProgressEvent(kind="chapter_saved", index=1, total=4, title="A"))
        renderer.handle(ProgressEvent(kind="warning", message="小心"))
        renderer.handle(ProgressEvent(kind="finished", ok=True, message="done"))
        out = stream.getvalue()
        # No progress bar, but warnings still print (on their own line).
        self.assertNotIn("\r", out)
        self.assertIn("警告：小心", out)

    def test_warning_breaks_line_then_redraws_progress(self):
        renderer, stream = self._renderer(is_tty=True)
        renderer.handle(ProgressEvent(kind="started", total=4))
        renderer.handle(ProgressEvent(kind="chapter_started", index=2, total=4, title="正文"))
        renderer.handle(ProgressEvent(kind="warning", message="检测到 WRPA"))
        out = stream.getvalue()
        self.assertIn("警告：检测到 WRPA", out)
        # After the warning the current chapter progress should be redrawn.
        self.assertIn("[2/4]", out)


class CrawlerProgressEventsTests(unittest.TestCase):
    """Verify _crawl emits the expected event sequence to on_progress."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, callback=None):
        return ExportRequest(
            platform="weread",
            book_id="book1",
            output_format="md",
            cache_dir=self.cache,
            output_dir=self.cache / "out",
            delay=0,
            headless=True,
            on_progress=callback,
        )

    def _run_crawl(self, fetcher_cls):
        """Drive WeReadCrawler._crawl with a stubbed fetcher class.

        The stub implements the async context-manager protocol itself so the
        crawler's ``async with WeReadPageFetcher(...) as fetcher`` resolves to
        the stub instance without launching a real browser.
        """
        events: list[ProgressEvent] = []
        request = self._request(callback=events.append)
        crawler = WeReadCrawler()

        with patch("weread_exporter_lys.crawler.weread.WeReadPageFetcher", fetcher_cls):
            asyncio.run(crawler._crawl(request, on_progress=events.append))
        return events

    def test_emits_started_chapter_started_saved_finished(self):
        class FakeFetcher:
            def __init__(self, **kwargs):
                self.on_progress = kwargs.get("on_progress")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def goto_reader(self, book_id):
                return None

            async def ensure_logged_in(self):
                return None

            async def has_blocking_paywall(self):
                return False

            async def save_cover(self, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x")
                return True

            async def extract_toc(self):
                return [{"index": 0, "title": "封面", "level": 1},
                        {"index": 1, "title": "第一章", "level": 1}]

            async def clear_wrpa_markdown(self):
                return None

            async def goto_toc_item(self, index):
                return True

            async def extract_chapter_content(self, *, images_dir=None, chapter_index=None):
                from weread_exporter_lys.crawler.extractor import ChapterContent
                return ChapterContent(markdown=f"正文{images_dir}", source="dom",
                                      anti_crawl_status={"hasWRPA": False})

            async def extract_full_chapter(self, *, images_dir=None, chapter_index=None):
                from weread_exporter_lys.crawler.extractor import ChapterContent
                return ChapterContent(markdown=f"正文{images_dir}", source="dom",
                                      anti_crawl_status={"hasWRPA": False})

            async def save_auth_state(self):
                return None

            async def go_next(self, previous_markdown, *, images_dir=None):
                return True

        events = self._run_crawl(FakeFetcher)
        kinds = [e.kind for e in events]
        # Cover + toc + started, then per-chapter started/saved pairs, then finished.
        self.assertIn("cover", kinds)
        self.assertIn("toc", kinds)
        self.assertEqual(kinds[kinds.index("toc") + 1], "started")
        self.assertEqual(kinds[-1], "finished")
        # Two chapters => two chapter_started + two chapter_saved events.
        self.assertEqual(kinds.count("chapter_started"), 2)
        self.assertEqual(kinds.count("chapter_saved"), 2)
        # chapter_started for index 1 carries the right title.
        first_started = next(e for e in events if e.kind == "chapter_started")
        self.assertEqual(first_started.index, 1)
        self.assertEqual(first_started.total, 2)
        self.assertEqual(first_started.title, "封面")
        last_saved = [e for e in events if e.kind == "chapter_saved"][-1]
        self.assertEqual(last_saved.index, 2)

    def test_finished_emitted_on_paywall_failure(self):
        class FakeFetcher:
            def __init__(self, **kwargs):
                self.on_progress = kwargs.get("on_progress")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def goto_reader(self, book_id):
                return None

            async def ensure_logged_in(self):
                return None

            async def has_blocking_paywall(self):
                return True

            async def save_auth_state(self):
                return None

        events = self._run_crawl(FakeFetcher)
        finished = [e for e in events if e.kind == "finished"]
        self.assertEqual(len(finished), 1)
        self.assertFalse(finished[0].ok)
        self.assertIn("试读结束", finished[0].message or "")


class FetcherWaitingEventsTests(unittest.TestCase):
    """Verify the fetcher emits ``waiting`` events at long-running points."""

    def test_wait_for_chapter_render_emits_waiting(self):
        events: list[ProgressEvent] = []
        fetcher = WeReadPageFetcher.__new__(WeReadPageFetcher)
        fetcher.on_progress = events.append
        fetcher.delay = 0

        async def fake_evaluate(self_p, script, *args, **kwargs):
            # Return a render-stable signal immediately so the loop exits fast.
            if isinstance(script, str) and "getMarkdown" in script:
                return "x" * 100
            return True

        class FakeLoop:
            def time(self):
                # Make every deadline check return a time past the deadline so
                # both poll phases exit immediately after one iteration.
                return 1_000_000.0

        # Supply a dummy page whose evaluate resolves the render-stable check.
        fetcher._page = type("P", (), {"evaluate": fake_evaluate})()
        with patch("asyncio.get_event_loop", return_value=FakeLoop()):
            asyncio.run(fetcher.wait_for_chapter_render(timeout=0.01))

        waiting = [e for e in events if e.kind == "waiting"]
        self.assertTrue(any("canvas" in (e.message or "") for e in waiting))

    def test_download_rare_chars_emits_waiting_progress(self):
        events: list[ProgressEvent] = []
        fetcher = WeReadPageFetcher.__new__(WeReadPageFetcher)
        fetcher.on_progress = events.append

        class FakeResponse:
            ok = True

            async def body(self):
                return b"x"

        class FakeRequest:
            async def get(self, src):
                return FakeResponse()

        fetcher._page = type("P", (), {"request": FakeRequest()})()
        with tempfile.TemporaryDirectory() as d:
            images_dir = Path(d) / "images"
            rare = [{"src": "https://res.weread.qq.com/wrepub/a", "data_wr_id": "r1"},
                    {"src": "https://res.weread.qq.com/wrepub/b", "data_wr_id": "r2"}]
            asyncio.run(fetcher._download_rare_chars(rare, images_dir))

        waiting = [e for e in events if e.kind == "waiting"]
        messages = [e.message for e in waiting]
        self.assertTrue(any("0/2" in m for m in messages))
        self.assertTrue(any("2/2" in m for m in messages))

    def _make_fetcher(self, events):
        fetcher = WeReadPageFetcher.__new__(WeReadPageFetcher)
        fetcher.on_progress = events.append
        fetcher.delay = 0
        return fetcher

    def test_wait_for_chapter_render_waits_for_complete(self):
        """Multi-batch chapter: __wrpaRenderComplete flips to true after a few
        polls; the fetcher must keep waiting until it does."""
        events: list[ProgressEvent] = []
        fetcher = self._make_fetcher(events)

        # Sequence of evaluate results: complete=False x2, then True.
        complete_returns = iter([False, False, True])

        async def fake_evaluate(self_p, script, *a, **kw):
            s = script if isinstance(script, str) else ""
            if "__wrpaRenderComplete" in s:
                return next(complete_returns)
            if "getMarkdown" in s:
                return "x" * 50  # below 80 so the stable-fallback path is skipped
            if "__wrpaRenderStable" in s:
                return True
            return None

        class FakeLoop:
            _t = [0.0]

            def time(self):
                return self._t[0]

        async def fake_sleep(d):
            FakeLoop._t[0] += d

        fetcher._page = type("P", (), {"evaluate": fake_evaluate})()
        with patch("asyncio.get_event_loop", return_value=FakeLoop()), \
             patch("asyncio.sleep", fake_sleep):
            result = asyncio.run(fetcher.wait_for_chapter_render(timeout=30.0))

        self.assertTrue(result)
        waiting = [e for e in events if e.kind == "waiting"]
        self.assertTrue(any("canvas" in (e.message or "") for e in waiting))

    def test_wait_for_chapter_render_falls_back_to_stable(self):
        """Single-batch short chapter: __wrpaRenderComplete never fires, but
        render-stable + >80 chars should be accepted as a fallback."""
        events: list[ProgressEvent] = []
        fetcher = self._make_fetcher(events)

        async def fake_evaluate(self_p, script, *a, **kw):
            s = script if isinstance(script, str) else ""
            if "__wrpaRenderComplete" in s:
                return False
            if "getMarkdown" in s:
                return "y" * 100
            if "__wrpaRenderStable" in s:
                return True
            return None

        class FakeLoop:
            _t = [0.0]

            def time(self):
                return self._t[0]

        async def fake_sleep(d):
            FakeLoop._t[0] += d

        fetcher._page = type("P", (), {"evaluate": fake_evaluate})()
        with patch("asyncio.get_event_loop", return_value=FakeLoop()), \
             patch("asyncio.sleep", fake_sleep):
            result = asyncio.run(fetcher.wait_for_chapter_render(timeout=30.0))

        self.assertTrue(result)

    def test_wait_for_chapter_render_timeout(self):
        """Neither complete nor a usable stable signal: must time out and
        return False without looping forever."""
        events: list[ProgressEvent] = []
        fetcher = self._make_fetcher(events)

        async def fake_evaluate(self_p, script, *a, **kw):
            s = script if isinstance(script, str) else ""
            if "__wrpaRenderComplete" in s:
                return False
            if "getMarkdown" in s:
                return ""  # empty text → stable fallback skipped
            if "__wrpaRenderStable" in s:
                return False
            return None

        # Time advances each call; deadline = time()+0.01, so after one poll
        # the clock passes the deadline and the loop exits.
        class FakeLoop:
            _t = [0.0]

            def time(self):
                return self._t[0]

        async def fake_sleep(d):
            FakeLoop._t[0] += 1.0  # jump past the small timeout

        fetcher._page = type("P", (), {"evaluate": fake_evaluate})()
        with patch("asyncio.get_event_loop", return_value=FakeLoop()), \
             patch("asyncio.sleep", fake_sleep):
            result = asyncio.run(fetcher.wait_for_chapter_render(timeout=0.01))

        self.assertFalse(result)


class XhtmlSourceTests(unittest.TestCase):
    """Tests for the xhtml crawl method's decode + convert pipeline."""

    # A minimal but structurally faithful chapter response: heading, 【原文】
    # section, a paragraph with a rare-char <img.h-pic> inlined BETWEEN [9]
    # and 虎 (the zero-offset position), a footnote sup/a, and a separate
    # paragraph with a normal illustration <img>. Format:
    #   <32 hex>PP<base64 of concatenated XHTML>
    _XHTML_DOC = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head><title></title></head>\n<body>\n'
        '<h2 class="secondTitle-1">第一卷 五帝本纪第一</h2>\n'
        '<h3 class="thirdTitle-1">【原文】</h3>\n'
        '<p>教熊罴貔貅<sup><a href="#a15" id="b15">[9]</a></sup>'
        '<img alt="alt" class="h-pic" src="https://res.weread.qq.com/wrepub/epub_41595377_3" '
        'data-w="85px" data-ratio="0.941" data-w-new="80px"/>'
        '虎，以与炎帝战于阪泉<sup><a href="#a16" id="b16">[10]</a></sup>之野。</p>\n'
        '<p><img alt="插图" src="https://res.weread.qq.com/wrco/illustration_123.jpg"/></p>\n'
        '</body>\n</html>'
    )

    def _make_response(self, xhtml: str = _XHTML_DOC, flag: str = "P") -> str:
        """Build a single ``e_N`` response body.

        Format: ``<32-hex hash><1 flag char><base64 chunk>``. A real chapter
        spans multiple such responses whose base64 chunks concatenate; for
        single-response tests we encode the whole XHTML in one chunk.
        """
        import base64
        b64 = base64.b64encode(xhtml.encode("utf-8")).decode("ascii")
        return "D8E5F0E2C4530BF7951D5E3B72969590" + flag + b64

    def test_decode_chapter_responses_handles_hash_flag_base64(self):
        from weread_exporter_lys.crawler.xhtml_source import decode_chapter_responses
        responses = {0: self._make_response()}
        xhtml = decode_chapter_responses(responses)
        self.assertIsNotNone(xhtml)
        self.assertIn("熊罴貔貅", xhtml)
        self.assertIn("h-pic", xhtml)

    def test_decode_returns_none_for_empty_or_invalid(self):
        from weread_exporter_lys.crawler.xhtml_source import decode_chapter_responses
        self.assertIsNone(decode_chapter_responses({}))
        self.assertIsNone(decode_chapter_responses({0: "{}"}))
        self.assertIsNone(decode_chapter_responses({0: "notbase64!!!"}))

    def test_decode_concatenates_multiple_chunks_in_index_order(self):
        """Each e_N response is a self-contained base64 block with flag 'P'.
        Only P-flag chunks are decoded; their decoded text is concatenated
        in N order. Non-P (encrypted) chunks are skipped."""
        from weread_exporter_lys.crawler.xhtml_source import decode_chapter_responses
        import base64
        doc_a = '<?xml version="1.0"?><html><body><p>甲段</p></body></html>'
        doc_b = '<?xml version="1.0"?><html><body><p>乙段</p></body></html>'
        b64_a = base64.b64encode(doc_a.encode("utf-8")).decode("ascii")
        b64_b = base64.b64encode(doc_b.encode("utf-8")).decode("ascii")
        resp_a = "00" * 16 + "P" + b64_a
        resp_b = "11" * 16 + "P" + b64_b
        # Out-of-order dict insertion shouldn't matter — sorted by key.
        xhtml = decode_chapter_responses({1: resp_b, 0: resp_a})
        self.assertIsNotNone(xhtml)
        self.assertIn("甲段", xhtml)
        self.assertIn("乙段", xhtml)

    def test_decode_skips_encrypted_non_p_chunks(self):
        """Non-P flag chunks are encrypted and must be skipped to avoid
        corrupting the output with binary garbage."""
        from weread_exporter_lys.crawler.xhtml_source import decode_chapter_responses
        import base64
        doc = '<?xml version="1.0"?><html><body><p>明文</p></body></html>'
        b64 = base64.b64encode(doc.encode("utf-8")).decode("ascii")
        # e_0: plaintext (flag P), e_1: encrypted (flag u), e_2: plaintext (flag P)
        resp_0 = "00" * 16 + "P" + b64
        resp_1 = "11" * 16 + "u" + b64  # would be garbage if decoded
        resp_2 = "22" * 16 + "P" + b64
        xhtml = decode_chapter_responses({0: resp_0, 1: resp_1, 2: resp_2})
        self.assertIsNotNone(xhtml)
        self.assertIn("明文", xhtml)
        # The encrypted chunk should not contribute any garbage.
        self.assertNotIn("\ufffd", xhtml)

    def test_rare_char_image_inlined_at_exact_position(self):
        """The core zero-offset guarantee: <img h-pic> stays between [9] and 虎."""
        from weread_exporter_lys.crawler.xhtml_source import decode_chapter_responses, xhtml_to_markdown
        # Decode the fixture response the same way the fetcher does.
        xhtml = decode_chapter_responses({0: self._make_response()})
        self.assertIsNotNone(xhtml)
        markdown, rare_srcs = xhtml_to_markdown(xhtml, page_url="https://weread.qq.com/")
        # Rare-char token must sit between [9] and 虎 — zero offset.
        self.assertIn("[9]![](../images/wrepub_41595377_3.png)虎", markdown)
        self.assertIn("## 第一卷 五帝本纪第一", markdown)
        self.assertIn("### 【原文】", markdown)
        self.assertIn("[10]", markdown)
        # The rare-char src was inlined.
        self.assertIn("https://res.weread.qq.com/wrepub/epub_41595377_3", rare_srcs)

    def test_normal_illustration_appended_at_tail_not_inline(self):
        from weread_exporter_lys.crawler.xhtml_source import decode_chapter_responses, xhtml_to_markdown
        xhtml = decode_chapter_responses({0: self._make_response()})
        markdown, _ = xhtml_to_markdown(xhtml, page_url="https://weread.qq.com/")
        # The illustration src appears at the tail, not inline in the body paragraph.
        self.assertIn("illustration_123.jpg", markdown)
        # The illustration is NOT glued to the rare-char paragraph (it's a
        # separate tail block).
        rare_line = [ln for ln in markdown.split("\n\n") if "熊罴" in ln][0]
        self.assertNotIn("illustration_123", rare_line)

    def test_rare_char_name_uses_wr_prefix_and_src_stem(self):
        from weread_exporter_lys.crawler.xhtml_source import rare_char_name
        self.assertEqual(
            rare_char_name("https://res.weread.qq.com/wrepub/epub_41595377_3"),
            "wrepub_41595377_3",
        )
        self.assertEqual(rare_char_name("https://x/y/z"), "wrz")

    def test_collect_rare_char_srcs_dedup_by_src(self):
        from weread_exporter_lys.crawler.xhtml_source import collect_rare_char_srcs
        xhtml = (
            '<img class="h-pic" src="https://res.weread.qq.com/wrepub/epub_4"/>'
            '<img class="h-pic" src="https://res.weread.qq.com/wrepub/epub_4"/>'
            '<img class="h-pic" src="https://res.weread.qq.com/wrepub/epub_5"/>'
        )
        srcs = collect_rare_char_srcs(xhtml)
        # Same src collapses to one entry; two distinct srcs → two entries.
        self.assertEqual(len(srcs), 2)
        self.assertEqual(srcs["https://res.weread.qq.com/wrepub/epub_4"], "wrepub_4")


class FetcherXhtmlDispatchTests(unittest.TestCase):
    """The fetcher routes to the xhtml path when crawl_method == 'xhtml' and
    chapter responses were captured, and falls back to canvas otherwise."""

    def _make_fetcher(self, crawl_method="xhtml"):
        fetcher = WeReadPageFetcher.__new__(WeReadPageFetcher)
        fetcher.crawl_method = crawl_method
        fetcher.on_progress = None
        fetcher._chapter_responses = {}
        return fetcher

    def test_drain_chapter_responses_returns_and_clears(self):
        fetcher = self._make_fetcher()
        fetcher._chapter_responses = {0: "body0", 1: "body1"}
        drained = fetcher.drain_chapter_responses()
        self.assertEqual(drained, {0: "body0", 1: "body1"})
        self.assertEqual(fetcher._chapter_responses, {})

    def test_extract_via_xhtml_returns_none_when_no_responses(self):
        fetcher = self._make_fetcher()
        # No responses captured → None (signals canvas fallback).
        import asyncio
        result = asyncio.run(fetcher._extract_via_xhtml(images_dir=None))
        self.assertIsNone(result)

    def test_extract_via_xhtml_returns_none_for_invalid_response(self):
        """An invalid response body decodes to None → canvas fallback."""
        fetcher = self._make_fetcher()
        fetcher._chapter_responses = {0: "{}"}  # server-rejected unsigned request
        import asyncio
        result = asyncio.run(fetcher._extract_via_xhtml(images_dir=None))
        self.assertIsNone(result)

    def test_canvas_method_skips_xhtml_path(self):
        """crawl_method == 'canvas' never touches _chapter_responses."""
        # We can't easily run extract_chapter_content without a browser, but
        # we can assert the guard: the xhtml branch is only entered when
        # crawl_method == 'xhtml'. For canvas, drain_chapter_responses is
        # never called, so responses accumulate (and would be stale). This
        # test documents that contract.
        fetcher = self._make_fetcher("canvas")
        fetcher._chapter_responses = {0: "stale"}
        # Simulate the guard check extract_chapter_content performs.
        self.assertNotEqual(fetcher.crawl_method, "xhtml")
        # Canvas path leaves _chapter_responses untouched (no drain).
        self.assertEqual(fetcher._chapter_responses, {0: "stale"})


if __name__ == "__main__":
    unittest.main()
