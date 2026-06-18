"""Processing pipeline orchestrator.

Ties together preprocess, merge, postprocess and convert steps, emitting
progress events along the way.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..progress import ProgressCallback, ProgressEvent, emit
from .convert import convert_markdown
from .merge import load_toc, merge_chapters
from .postprocess import (
    add_footnote_links,
    clean_characters,
    format_footnotes,
    inline_rare_char_images,
    resolve_rare_chars,
)
from .preprocess import load_cookies, localize_images, normalize_format, remove_watermarks

if TYPE_CHECKING:
    from ..platforms.base import ExportRequest

# Characters that are unsafe in filenames.
_FILENAME_UNSAFE = str.maketrans({ch: "_" for ch in r'\/:*?"<>|'})


@dataclass
class ProcessingPipeline:
    """Orchestrate the full processing pipeline for one book."""

    request: ExportRequest

    @property
    def book_dir(self) -> Path:
        return self.request.cache_dir / self.request.book_id

    @property
    def content_dir(self) -> Path:
        return self.book_dir / "content"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> Path | None:
        """Execute the pipeline and return the path of the final output file."""
        on_progress = self.request.on_progress
        emit(on_progress, ProgressEvent(kind="processing_start"))

        # ---- 1. Load TOC & metadata ----
        toc = load_toc(self.book_dir)
        title = self._derive_title(toc)
        author = self._derive_author()

        # ---- 2. Load cookies for image downloads ----
        auth_state_path = getattr(self.request, "auth_state_path", None)
        cookies = load_cookies(auth_state_path)

        # ---- 3. Pre-process each chapter ----
        emit(on_progress, ProgressEvent(
            kind="processing_step", message="逐章清洗格式、下载图片…",
        ))
        preprocessed, warnings = self._preprocess(toc, cookies, on_progress)

        # ---- 4. Merge chapters ----
        merged = merge_chapters(self.content_dir, preprocessed)

        # ---- 5. Post-process the merged document ----
        emit(on_progress, ProgressEvent(
            kind="processing_step", message="清理字符、格式化脚注、生僻字识别…",
        ))

        md = merged
        md = clean_characters(md)
        md = resolve_rare_chars(md)
        # --- NEW: inline rare-character images so they render at text size ---
        md = inline_rare_char_images(md)
        md = format_footnotes(md)
        md = add_footnote_links(md)

        # ---- 6. Save the processed Markdown ----
        md_path = self.book_dir / f"{title}.md"
        md_path.write_text(md, encoding="utf-8")
        emit(on_progress, ProgressEvent(
            kind="processing_step",
            message=f"  处理后的 Markdown 已保存到 {md_path}",
        ))

        for w in warnings:
            emit(on_progress, ProgressEvent(kind="warning", message=w))

        # ---- 7. Convert to target format ----
        fmt = self.request.output_format.lower().lstrip(".")
        output_path = convert_markdown(
            md_path,
            fmt,
            book_dir=self.book_dir,
            title=title,
            author=author,
        )

        # ---- 8. Move to configured output directory ----
        final_path = self._build_output_path(title, fmt)
        if final_path != output_path:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, final_path)
        else:
            final_path = output_path

        emit(on_progress, ProgressEvent(
            kind="processing_done", message=str(final_path),
        ))
        return final_path

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _preprocess(
        self,
        toc: list[dict],
        cookies: dict[str, str] | None,
        on_progress: ProgressCallback | None,
    ) -> tuple[list[str], list[str]]:
        """Pre-process every chapter file, returning cleaned texts and warnings."""
        results: list[str] = []
        all_warnings: list[str] = []
        total = len(toc)

        for i, _entry in enumerate(toc, start=1):
            chapter_path = self.content_dir / f"{i}.md"
            if not chapter_path.exists():
                continue

            raw = chapter_path.read_text(encoding="utf-8")
            cleaned = remove_watermarks(raw)
            cleaned = normalize_format(cleaned)
            cleaned, img_warnings = localize_images(
                cleaned, self.book_dir, cookies, on_progress=on_progress,
            )
            results.append(cleaned)
            all_warnings.extend(img_warnings)

            emit(on_progress, ProgressEvent(
                kind="processing_step",
                index=i,
                total=total,
                message=f"  清洗第 {i}/{total} 章…",
            ))

        return results, all_warnings

    # Volume-suffix pattern: "（第一册）", "(第一卷)", etc.
    _VOLUME_SUFFIX = re.compile(r"[（(]第[^)）]*[册卷][)）]")

    def _derive_title(self, toc: list[dict]) -> str:
        """Derive the book title, preferring crawler metadata."""
        title = ""

        # 1. Try crawler metadata.
        meta_path = self.book_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(meta, dict) and meta.get("title"):
                    title = str(meta["title"]).strip()
            except (json.JSONDecodeError, OSError):
                pass

        # 2. Fall back to the first TOC entry.
        if not title and toc:
            title = toc[0].get("title", "").strip()

        # 3. Strip volume suffixes so "史记（第一册）" becomes "史记".
        if title:
            title = self._VOLUME_SUFFIX.sub("", title).strip()

        return title or "导出书籍"

    def _derive_author(self) -> str:
        """Derive the book author from crawler metadata."""
        meta_path = self.book_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(meta, dict) and meta.get("author"):
                    return str(meta["author"]).strip()
            except (json.JSONDecodeError, OSError):
                pass
        return "Unknown"

    def _build_output_path(self, title: str, fmt: str) -> Path:
        """Build the output file path in the configured output directory."""
        safe_title = title.translate(_FILENAME_UNSAFE).strip()
        if not safe_title:
            safe_title = "export"
        filename = f"{safe_title}.{fmt}"
        output_dir = getattr(self.request, "output_dir", None) or self.book_dir.parent
        return Path(output_dir) / filename

    def _notify(
        self,
        kind: str,
        message: str | None = None,
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        """Send a progress event to the user layer."""
        emit(
            self.request.on_progress,
            ProgressEvent(kind=kind, message=message, index=index, total=total),
        )
