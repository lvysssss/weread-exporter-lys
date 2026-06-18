"""Format conversion: Markdown → TXT / HTML / PDF / EPUB / MOBI / AZW3."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

# ---------------------------------------------------------------------------
# CSS / templates
# ---------------------------------------------------------------------------

# Rare-char images use ``class="rare-char"`` so they render inline at 1em
# height, blending with the surrounding text.  Normal illustrations (cover,
# figures, etc.) remain as centered block images.
_RARE_CHAR_CSS = """
img.rare-char {
    height: 1em;
    width: auto;
    display: inline;
    vertical-align: text-bottom;
    margin: 0;
}
""".strip()

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{
    font-family: "Noto Serif CJK SC", "Source Han Serif SC", "SimSun",
                 "宋体", serif;
    line-height: 1.8;
    max-width: 42em;
    margin: 2em auto;
    padding: 0 1em;
    color: #1a1a1a;
  }}
  h1 {{ font-size: 1.6em; margin-top: 1.5em; }}
  h2 {{ font-size: 1.3em; margin-top: 1.3em; }}
  h3 {{ font-size: 1.1em; }}
  sup {{ font-size: 0.75em; line-height: 0; }}
  a {{ color: #2563eb; text-decoration: none; }}
  img:not(.rare-char) {{ max-width: 100%; height: auto; display: block; margin: 1em auto; }}
  __RARE_CHAR_CSS__
  .page-break {{ page-break-before: always; }}
  @media print {{
    body {{ font-size: 12pt; max-width: none; margin: 0; }}
    .page-break {{ page-break-before: always; }}
  }}
</style>
</head>
<body>
{body}
</body>
</html>""".replace("__RARE_CHAR_CSS__", _RARE_CHAR_CSS)

# Bare-bones CSS for EPUB (the reading system may layer its own defaults).
_EPUB_CSS = """\
body { font-family: serif; line-height: 1.8; margin: 0.5em 1em; }
h1 { font-size: 1.5em; margin-top: 1em; }
h2 { font-size: 1.2em; margin-top: 0.8em; }
h3 { font-size: 1.05em; }
sup { font-size: 0.75em; }
a { color: #2563eb; text-decoration: none; }
img:not(.rare-char) { max-width: 100%; height: auto; display: block; margin: 1em auto; }
""" + _RARE_CHAR_CSS + """
.page-break { page-break-before: always; }
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUPPORTED = frozenset({"txt", "html", "pdf", "epub", "mobi", "azw3", "md"})


def _markdown_to_html(md_text: str) -> str:
    """Convert Markdown text to an HTML fragment (no <html>/<body> wrapper)."""
    import markdown
    return markdown.markdown(md_text, output_format="html5")


def _split_chapters(md_text: str) -> list[tuple[str, str]]:
    """Split merged markdown into *(chapter_title, chapter_md)* pairs.

    A chapter begins at every top-level ``# Heading`` line.
    """
    parts = re.split(r"\n(?=^# (?!\#))", md_text, flags=re.MULTILINE)
    result: list[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # First line is the heading; strip the leading ``# `` for the title.
        lines = part.split("\n", 1)
        title = lines[0]
        if title.startswith("# "):
            title = title[2:].strip()
        body = lines[1] if len(lines) > 1 else ""
        result.append((title, body))
    return result


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------

def to_txt(md_text: str, output_path: Path) -> Path:
    """Convert Markdown to plain text via HTML."""
    from bs4 import BeautifulSoup
    html = _markdown_to_html(md_text)
    soup = BeautifulSoup(html, "lxml")
    # Remove script/style tags
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text()
    # Compact blank lines
    lines = [line for line in text.splitlines() if line.strip()]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def to_html(md_text: str, output_path: Path, title: str = "导出书籍") -> Path:
    """Write a self-contained HTML file."""
    body = _markdown_to_html(md_text)
    html = _HTML_TEMPLATE.format(title=title, body=body)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def to_pdf(md_text: str, output_path: Path, title: str = "导出书籍") -> Path:
    """Render Markdown to PDF via WeasyPrint."""
    try:
        from weasyprint import HTML
    except ImportError:
        sys.exit(
            "错误：PDF 生成需要 weasyprint 库。\n"
            "请运行：pip install weasyprint\n"
            "Windows 用户还需安装 GTK+："
            "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows"
        )
    body = _markdown_to_html(md_text)
    html = _HTML_TEMPLATE.format(title=title, body=body)
    HTML(string=html).write_pdf(str(output_path))
    return output_path


def _embed_images(book_dir: Path) -> dict[str, str]:
    """Embed images from ``{book_dir}/images/`` into the EPUB.

    Returns a dict mapping original ``images/xxx`` paths to EPUB internal paths.
    """
    images_dir = book_dir / "images"
    mapping: dict[str, str] = {}
    if not images_dir.is_dir():
        return mapping

    from ebooklib import epub

    # Delayed import — items are returned so the caller adds them to the book.
    _MIME_MAP = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".bmp": "image/bmp",
    }
    # We'll return items for the caller to add.
    for img_file in sorted(images_dir.iterdir()):
        if not img_file.is_file():
            continue
        suffix = img_file.suffix.lower()
        mime = _MIME_MAP.get(suffix)
        if mime is None:
            continue
        stem = img_file.stem
        epub_name = f"images/{img_file.name}"
        mapping[f"images/{img_file.name}"] = epub_name
    return mapping


def _build_epub_items(book_dir: Path):
    """Return a list of ``(epub_item, epub_internal_path)`` tuples for embedding.

    This is a generator so the caller can call ``book.add_item()``.
    """
    images_dir = book_dir / "images"
    if not images_dir.is_dir():
        return

    from ebooklib import epub

    _MIME_MAP = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".bmp": "image/bmp",
    }
    for img_file in sorted(images_dir.iterdir()):
        if not img_file.is_file():
            continue
        suffix = img_file.suffix.lower()
        mime = _MIME_MAP.get(suffix)
        if mime is None:
            continue
        epub_name = f"images/{img_file.name}"
        item = epub.EpubItem(
            uid=f"img_{img_file.stem}",
            file_name=epub_name,
            media_type=mime,
            content=img_file.read_bytes(),
        )
        yield item, epub_name


def to_epub(
    md_text: str,
    output_path: Path,
    book_dir: Path | None = None,
    title: str = "导出书籍",
    author: str = "Unknown",
) -> Path:
    """Build an EPUB from the merged Markdown.

    Chapters are detected by top-level ``# …`` headings.  Each chapter
    becomes a separate EPUB spine item with its own XHTML file.  Local
    images under ``{book_dir}/images/`` are embedded into the EPUB.
    """
    try:
        from ebooklib import epub
    except ImportError:
        sys.exit(
            "错误：EPUB 生成需要 ebooklib 库。\n请运行：pip install ebooklib"
        )

    chapters = _split_chapters(md_text)
    if not chapters:
        sys.exit("Markdown 中没有检测到章节（需要 # 标题）")

    book = epub.EpubBook()
    book.set_identifier(f"weread-{abs(hash(title)):08x}")
    book.set_title(title)
    book.set_language("zh-CN")
    book.add_author(author)

    # CSS
    css_item = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=_EPUB_CSS.encode("utf-8"),
    )
    book.add_item(css_item)

    # Embed images
    image_mapping: dict[str, str] = {}
    if book_dir is not None:
        for item, epub_name in _build_epub_items(book_dir):
            book.add_item(item)
            # Map both ``images/xxx.png`` and ``../images/xxx.png`` forms.
            image_mapping[f"images/{Path(epub_name).name}"] = epub_name
            image_mapping[f"../images/{Path(epub_name).name}"] = epub_name

    # Build chapter XHTML files
    spine = ["nav"]
    toc_entries = []
    for i, (ch_title, ch_body) in enumerate(chapters):
        ch_html = _markdown_to_html(f"# {ch_title}\n\n{ch_body}")
        # Rewrite image src paths to epub internal paths.
        for local_path, epub_path in image_mapping.items():
            ch_html = ch_html.replace(f'src="{local_path}"', f'src="{epub_path}"')
            ch_html = ch_html.replace(f"src='{local_path}'", f"src='{epub_path}'")

        xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">\n'
            f"<head><title>{ch_title}</title>\n"
            '<link rel="stylesheet" type="text/css" href="style/default.css"/>\n'
            "</head>\n<body>\n"
            f"{ch_html}\n"
            "</body>\n</html>"
        )

        file_name = f"chapter_{i}.xhtml"
        chapter_item = epub.EpubHtml(
            title=ch_title,
            file_name=file_name,
            lang="zh-CN",
        )
        chapter_item.content = xhtml.encode("utf-8")
        chapter_item.add_item(css_item)
        book.add_item(chapter_item)
        spine.append(chapter_item)
        toc_entries.append(epub.Link(file_name, ch_title, f"ch{i}"))

    book.toc = toc_entries
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(output_path), book)
    return output_path


def _via_epub_convert(
    md_text: str,
    output_path: Path,
    target_ext: str,
    book_dir: Path | None = None,
    title: str = "导出书籍",
    author: str = "Unknown",
) -> Path:
    """Build an EPUB, then convert via Calibre ``ebook-convert``."""
    epub_path = output_path.with_suffix(".epub")
    try:
        to_epub(md_text, epub_path, book_dir=book_dir, title=title, author=author)
    except SystemExit:
        raise
    except Exception:
        epub_path.unlink(missing_ok=True)
        raise

    if shutil.which("ebook-convert") is None:
        sys.exit(
            f"{target_ext} 格式需要 Calibre 的 ebook-convert 工具。\n"
            "请安装 Calibre：https://calibre-ebook.com/download"
        )

    result = subprocess.run(
        ["ebook-convert", str(epub_path), str(output_path)],
        capture_output=True,
        text=True,
    )
    epub_path.unlink(missing_ok=True)
    if result.returncode != 0:
        sys.exit(f"Calibre 转换失败：\n{result.stderr}")
    return output_path


def to_mobi(
    md_text: str,
    output_path: Path,
    book_dir: Path | None = None,
    title: str = "导出书籍",
    author: str = "Unknown",
) -> Path:
    """Build an EPUB first, then convert to MOBI via Calibre ``ebook-convert``."""
    return _via_epub_convert(md_text, output_path, ".mobi", book_dir, title, author)


def to_azw3(
    md_text: str,
    output_path: Path,
    book_dir: Path | None = None,
    title: str = "导出书籍",
    author: str = "Unknown",
) -> Path:
    """Build an EPUB first, then convert to AZW3 via Calibre ``ebook-convert``."""
    return _via_epub_convert(md_text, output_path, ".azw3", book_dir, title, author)


def convert_markdown(
    md_path: Path,
    target_format: str,
    book_dir: Path | None = None,
    title: str = "导出书籍",
    author: str = "Unknown",
) -> Path:
    """Route *markdown* to the correct converter based on *target_format*."""
    target_format = target_format.lower().lstrip(".")
    if target_format not in _SUPPORTED:
        sys.exit(f"不支持的目标格式：{target_format}")

    md_text = md_path.read_text(encoding="utf-8")

    if target_format == "txt":
        return to_txt(md_text, md_path.with_suffix(".txt"))
    elif target_format == "html":
        return to_html(md_text, md_path.with_suffix(".html"), title=title)
    elif target_format == "pdf":
        return to_pdf(md_text, md_path.with_suffix(".pdf"), title=title)
    elif target_format == "epub":
        return to_epub(
            md_text, md_path.with_suffix(".epub"),
            book_dir=book_dir, title=title, author=author,
        )
    elif target_format == "mobi":
        return to_mobi(
            md_text, md_path.with_suffix(".mobi"),
            book_dir=book_dir, title=title, author=author,
        )
    elif target_format == "azw3":
        return to_azw3(
            md_text, md_path.with_suffix(".azw3"),
            book_dir=book_dir, title=title, author=author,
        )
    elif target_format == "md":
        return md_path
    else:
        sys.exit(f"不支持的目标格式：{target_format}")
