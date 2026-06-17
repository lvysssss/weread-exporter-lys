from __future__ import annotations

import re
from dataclasses import dataclass

from .images import ImageFilter

BODY_SELECTORS = (
    ".readerChapterContent .renderTargetContainer",
    ".readerChapterContent",
    ".readerChapterContent_container",
)

IMAGE_SELECTORS = (
    ".readerChapterContent .renderTargetContainer img",
    ".readerChapterContent img",
    ".readerChapterContent_container img",
)


@dataclass(frozen=True)
class ChapterContent:
    markdown: str
    source: str
    anti_crawl_status: dict | None = None


def normalize_markdown_text(text: str) -> str:
    stripped_lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    chunks: list[str] = []
    blank = False

    for line in stripped_lines:
        current = line.strip()
        if not current:
            if chunks and not blank:
                chunks.append("")
            blank = True
            continue
        chunks.append(current)
        blank = False

    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def html_text_to_markdown(text: str, image_urls: list[str] | None = None, *, base_url: str | None = None) -> str:
    body = normalize_markdown_text(text)
    image_filter = ImageFilter()
    image_lines = image_filter.markdown_lines(image_urls or [], base_url=base_url)

    parts = [part for part in (body, "\n".join(image_lines)) if part]
    return "\n\n".join(parts).strip()


def looks_like_login_or_paywall(text: str) -> bool:
    return bool(re.search(r"登录|扫码|微信扫码|付费|购买|会员", text or ""))
