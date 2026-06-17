from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import BookPlatform, ExportRequest, ExportResult
from ..crawler.weread import WeReadCrawler

BOOK_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


class WeReadPlatform(BookPlatform):
    name = "weread"
    display_name = "微信读书"

    def normalize_book_id(self, value: str) -> str:
        raw_value = value.strip()
        if not raw_value:
            raise ValueError("书本 id 不能为空")

        if raw_value.startswith(("http://", "https://")):
            parsed = urlparse(raw_value)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 3 and parts[-2] == "reader":
                return self._validate_book_id(parts[-1])
            raise ValueError("微信读书链接应形如 https://weread.qq.com/web/reader/{book_id}")

        return self._validate_book_id(raw_value)

    def export(self, request: ExportRequest) -> ExportResult:
        crawler_result = WeReadCrawler().crawl(request)
        message = crawler_result.message
        if crawler_result.warnings:
            message = message + "\n" + "\n".join(f"警告：{warning}" for warning in crawler_result.warnings)
        return ExportResult(
            ok=crawler_result.ok,
            message=message,
            output_path=crawler_result.content_dir if crawler_result.ok else None,
        )

    def _validate_book_id(self, book_id: str) -> str:
        if not BOOK_ID_PATTERN.fullmatch(book_id):
            raise ValueError("书本 id 只能包含字母、数字、下划线或连字符")
        return book_id
