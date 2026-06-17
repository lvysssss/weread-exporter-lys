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
    def test_no_rare_chars_returns_lines_unchanged(self):
        lines = [
            {"text": "第一行", "y": 100.0, "fontSize": 16, "prefix": ""},
            {"text": "第二行", "y": 140.0, "fontSize": 16, "prefix": ""},
        ]
        self.assertEqual(merge_rare_chars(lines, []), "第一行\n\n第二行")

    def test_appends_rare_char_to_closest_line_by_y(self):
        # Sample mirrors 《五帝本纪》"熊罴貔貅〔图〕虎": the rare char sits between
        # the 貔貅 line and the 虎 line; it should attach to the closest one.
        lines = [
            {"text": "教熊罴貔貅", "y": 3070.0, "fontSize": 16, "prefix": ""},
            {"text": "虎，以与炎帝战于阪泉", "y": 3100.0, "fontSize": 16, "prefix": ""},
        ]
        rare = [{"local_path": "../images/wrqsm9qrxc61.png", "x": 129.0, "y": 3086.0}]
        result = merge_rare_chars(lines, rare)
        # 3086 is closer to 3100 (dist 14) than 3070 (dist 16) → attaches to 虎 line end.
        self.assertEqual(
            result,
            "教熊罴貔貅\n\n虎，以与炎帝战于阪泉![](../images/wrqsm9qrxc61.png)",
        )

    def test_same_line_multiple_rare_chars_ordered_by_x(self):
        lines = [{"text": "甲乙", "y": 100.0, "fontSize": 16, "prefix": ""}]
        rare = [
            {"local_path": "../images/b.png", "x": 300.0, "y": 100.0},
            {"local_path": "../images/a.png", "x": 129.0, "y": 100.0},
            {"local_path": "../images/c.png", "x": 458.0, "y": 100.0},
        ]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "甲乙![](../images/a.png)![](../images/b.png)![](../images/c.png)")

    def test_preserves_heading_prefix(self):
        lines = [
            {"text": "第一卷 五帝本纪第一", "y": 50.0, "fontSize": 28, "prefix": "## "},
            {"text": "正文", "y": 100.0, "fontSize": 16, "prefix": ""},
        ]
        rare = [{"local_path": "../images/x.png", "x": 10.0, "y": 100.0}]
        result = merge_rare_chars(lines, rare)
        self.assertEqual(result, "## 第一卷 五帝本纪第一\n\n正文![](../images/x.png)")

    def test_orphan_rare_char_beyond_tolerance_appended_at_end(self):
        lines = [{"text": "正文", "y": 100.0, "fontSize": 16, "prefix": ""}]
        rare = [{"local_path": "../images/far.png", "x": 10.0, "y": 5000.0}]
        result = merge_rare_chars(lines, rare, line_tolerance=20.0)
        self.assertEqual(result, "正文\n\n![](../images/far.png)")

    def test_empty_lines_returns_empty(self):
        self.assertEqual(merge_rare_chars([], [{"local_path": "../images/x.png", "x": 0, "y": 0}]), "")



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
