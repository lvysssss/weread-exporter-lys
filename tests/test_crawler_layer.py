import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weread_exporter_lys.crawler.extractor import html_text_to_markdown, normalize_markdown_text
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


class ExtractorTests(unittest.TestCase):
    def test_normalizes_markdown_text(self):
        text = normalize_markdown_text("  第一段  \n\n\n第二段\r\n")
        self.assertEqual(text, "第一段\n\n第二段")

    def test_combines_text_and_images(self):
        markdown = html_text_to_markdown("正文", ["https://example.test/a.jpg"])
        self.assertEqual(markdown, "正文\n\n![](https://example.test/a.jpg)")


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
