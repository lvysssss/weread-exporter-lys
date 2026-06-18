"""Merge chapter Markdown files into a single document."""

from __future__ import annotations

import json
from pathlib import Path


def load_toc(book_dir: Path) -> list[dict]:
    """Load the TOC JSON file from *book_dir*.

    Returns a list of chapter entries.  Each entry is a dict with at least
    a ``"title"`` key.
    """
    toc_path = book_dir / "toc.json"
    if not toc_path.exists():
        raise FileNotFoundError(f"TOC file not found: {toc_path}")
    with open(toc_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Invalid TOC format in {toc_path}: expected a JSON array")
    return data


def merge_chapters(content_dir: Path, toc: list[dict]) -> str:
    """Merge all chapter ``.md`` files in TOC order into a single Markdown string.

    A ``<div class="page-break">`` marker is injected between chapters to
    support per-chapter page breaks in PDF / EPUB output.
    """
    if not toc:
        raise FileNotFoundError("TOC is empty — nothing to merge.")

    parts: list[str] = []
    for i, _entry in enumerate(toc, start=1):
        chapter_path = content_dir / f"{i}.md"
        if not chapter_path.exists():
            # Gracefully skip missing chapters (may happen if crawling was
            # interrupted and the user wants to process what's available).
            continue
        text = chapter_path.read_text(encoding="utf-8").strip()
        if text:
            parts.append(text)

    if not parts:
        raise FileNotFoundError(f"No chapter files found in {content_dir}")

    separator = "\n\n<div class=\"page-break\"></div>\n\n"
    return separator.join(parts)
