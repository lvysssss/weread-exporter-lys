import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weread_exporter_lys.crawler.extractor import html_text_to_markdown, merge_rare_chars, normalize_markdown_text
from weread_exporter_lys.crawler.images import ImageFilter
from weread_exporter_lys.crawler.state import CrawlState
from weread_exporter_lys.crawler.weread import WeReadCrawler, WeReadCrawlerResult
from weread_exporter_lys.platforms.base import ExportRequest
from weread_exporter_lys.platforms.weread import WeReadPlatform


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
        self.assertEqual(paths.content_dir, Path("cache") / "book1" / "content")
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


if __name__ == "__main__":
    unittest.main()
