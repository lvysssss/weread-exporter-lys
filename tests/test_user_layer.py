import json
import tempfile
import unittest
from pathlib import Path

from weread_exporter_lys.cli import build_parser, build_request
from weread_exporter_lys.config import AppConfig, load_config
from weread_exporter_lys.platforms import get_platform
from weread_exporter_lys.platforms.weread import WeReadPlatform


class WeReadPlatformTests(unittest.TestCase):
    def setUp(self):
        self.platform = WeReadPlatform()

    def test_extracts_book_id_from_reader_url(self):
        book_id = self.platform.normalize_book_id(
            "https://weread.qq.com/web/reader/7cb324e0727ab1f17cbf4c1"
        )
        self.assertEqual(book_id, "7cb324e0727ab1f17cbf4c1")

    def test_accepts_plain_book_id(self):
        self.assertEqual(self.platform.normalize_book_id("abc_123-def"), "abc_123-def")

    def test_rejects_invalid_book_id(self):
        with self.assertRaises(ValueError):
            self.platform.normalize_book_id("abc/123")


class ConfigTests(unittest.TestCase):
    def test_missing_config_uses_defaults(self):
        config = load_config("missing-config-file.json")
        self.assertEqual(config.default_platform, "weread")
        self.assertEqual(config.default_format, "epub")
        self.assertEqual(config.cache_dir, Path("cache"))

    def test_json_config_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "default_format": "pdf",
                        "cache_dir": "tmp-cache",
                        "output_dir": "tmp-output",
                        "delay": 2.5,
                        "headless": True,
                        "auth_state_path": "auth-state.json",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.default_format, "pdf")
        self.assertEqual(config.cache_dir, Path("tmp-cache"))
        self.assertEqual(config.output_dir, Path("tmp-output"))
        self.assertEqual(config.delay, 2.5)
        self.assertTrue(config.headless)
        self.assertEqual(config.auth_state_path, Path("auth-state.json"))


class PlatformRegistryTests(unittest.TestCase):
    def test_finds_weread_platform(self):
        platform = get_platform("weread")
        self.assertEqual(platform.display_name, "微信读书")


class CliTests(unittest.TestCase):
    def test_build_request_from_url_args(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--platform",
                "weread",
                "--url",
                "https://weread.qq.com/web/reader/7cb324e0727ab1f17cbf4c1",
                "--format",
                "azw3",
                "--cache-dir",
                "cache2",
                "--output-dir",
                "out2",
                "--delay",
                "3",
                "--auth-state",
                "auth.json",
                "--headless",
            ]
        )

        request = build_request(args, AppConfig())

        self.assertIsNotNone(request)
        self.assertEqual(request.platform, "weread")
        self.assertEqual(request.book_id, "7cb324e0727ab1f17cbf4c1")
        self.assertEqual(request.output_format, "azw3")
        self.assertEqual(request.cache_dir, Path("cache2"))
        self.assertEqual(request.output_dir, Path("out2"))
        self.assertEqual(request.delay, 3)
        self.assertTrue(request.headless)
        self.assertEqual(request.auth_state_path, Path("auth.json"))

    def test_build_request_returns_none_without_book(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertIsNone(build_request(args, AppConfig()))


if __name__ == "__main__":
    unittest.main()
