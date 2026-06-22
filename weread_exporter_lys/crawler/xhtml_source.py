"""Capture and convert WeRead's structured chapter XHTML source.

When the WeRead reader loads a chapter, the browser fires a batch of
``POST /web/book/chapter/e_{N}`` requests (signed with a one-shot
``x-wrpa-0`` header). Each response body is::

    <32-hex-hash>PP<base64 of one or more XHTML documents concatenated>

Decoded, the XHTML is the chapter's original EPUB source with rare
characters inlined as ``<img class="h-pic">`` at their exact text-stream
position and footnotes as ``<sup><a href id>[n]</a></sup>`` bidirectional
anchors. Capturing these responses (via Playwright's ``page.on("response")``)
lets us bypass canvas coordinate merging entirely, yielding zero-offset
rare-character placement.

This module is purely functional and has no Playwright dependency — the
fetcher supplies the captured response bodies and drives image downloads,
so the conversion logic is unit-testable with plain string fixtures.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin

from .images import ImageFilter

# A rare-character image in the XHTML source. WeRead renders rare CJK glyphs
# as ``<img class="h-pic" src="https://res.weread.qq.com/wrepub/epub_{bookId}_{n}">``
# inlined at the exact position where the missing glyph belongs.
_HPIC_RE = re.compile(
    r'<img[^>]*\bclass="[^"]*h-pic[^"]*"[^>]*>'
    r'|<img[^>]*\bsrc="[^"]*wrepub/[^"]*"[^>]*>',
    re.IGNORECASE,
)

# Each ``e_{N}`` response body is ``<32-hex hash><1 flag char><base64 chunk>``.
# A chapter is fetched as a contiguous range of such chunks (e.g. e_0..e_3);
# the base64 chunks concatenate (in N order) into one stream that decodes to
# the chapter's full XHTML (multiple ``<?xml`` documents joined). The flag
# char (e.g. ``P``, ``E``, ``j``) is per-chunk metadata, not base64 payload.
_HASH_LEN = 32
_FLAG_LEN = 1


def decode_chapter_responses(responses: dict[int, str]) -> str | None:
    """Decode and concatenate captured ``e_{N}`` chapter responses.

    ``responses`` maps the batch index ``N`` (from the URL ``e_{N}``) to the
    raw response body. The reader fetches a chapter as a contiguous range of
    batch indices; each body is ``<32-hex hash><1 flag char><base64 chunk>``
    and the base64 chunks concatenate (in ascending ``N`` order) into one
    stream that decodes to the chapter's full XHTML — multiple ``<?xml``
    documents (one per 【原文】/【注释】 section) joined end-to-end.

    Returns the decoded XHTML text, or ``None`` if no body yielded decodable
    content.
    """
    if not responses:
        return None

    # Concatenate the base64 chunks in batch-index order, stripping each
    # body's 32-hex hash prefix and 1-char flag prefix.
    b64_stream = "".join(
        responses[n][_HASH_LEN + _FLAG_LEN:]
        for n in sorted(responses)
        if responses[n]
    )
    if not b64_stream:
        return None

    # Restore base64 padding before decoding.
    padded = b64_stream + "=" * (-len(b64_stream) % 4)
    try:
        decoded = base64.b64decode(padded, validate=False).decode("utf-8", errors="replace")
    except Exception:
        return None
    return decoded if decoded else None


def rare_char_name(src: str) -> str:
    """Derive a filename for a rare-char image from its ``src`` URL.

    WeRead rare-char URLs look like
    ``https://res.weread.qq.com/wrepub/epub_41595377_3``. We use the last
    path segment (``epub_41595377_3``) as the stem and prepend ``wr`` so the
    resulting ``![](../images/wr{stem}.png)`` token is matched by
    :func:`postprocess.inline_rare_char_images` (which keys on the
    ``images/wr`` prefix), keeping the two crawl methods' output uniformly
    post-processable. The ``.png`` extension is added by the caller at
    download time.
    """
    stem = src.rstrip("/").rsplit("/", 1)[-1]
    if not stem:
        stem = "rare"
    return f"wr{stem}"


def collect_rare_char_srcs(xhtml: str) -> dict[str, str]:
    """Scan XHTML for rare-char ``<img>`` and map each ``src`` to its filename stem.

    Returns ``{src: stem}`` (stem without extension), preserving first-seen
    order. Same ``src`` maps to one entry — the image is downloaded once and
    referenced wherever it appears in the text.
    """
    src_to_stem: dict[str, str] = {}
    for m in _HPIC_RE.finditer(xhtml):
        tag = m.group(0)
        src_match = re.search(r'src="([^"]+)"', tag)
        if not src_match:
            continue
        src = src_match.group(1)
        if src not in src_to_stem:
            src_to_stem[src] = rare_char_name(src)
    return src_to_stem


def _split_xhtml_docs(xhtml: str) -> list[str]:
    """Split a concatenated XHTML blob into individual document strings.

    Each document starts with ``<?xml``; anything before the first marker
    is dropped. The split preserves the ``<?xml`` prolog on each piece so
    BeautifulSoup can parse them as standalone XHTML.
    """
    pieces = re.split(r"(?=<\?xml)", xhtml)
    return [p for p in pieces if p.strip() and "<?xml" in p]


def _render_inline(node) -> str:
    """Render a BeautifulSoup node's inline content to a markdown string.

    Walks descendants and emits text, converting:
      - ``<sup><a ...>[n]</a></sup>`` (footnote refs) → ``[n]``
      - ``<img class="h-pic">`` (rare chars) → ``![](../images/{name}.png)``
      - other ``<a>`` → just their inner text
      - everything else → its text

    Returns the concatenated inline string (no surrounding block markup).
    """
    from bs4 import NavigableString, Tag

    out: list[str] = []
    for child in node.descendants:
        if isinstance(child, NavigableString):
            # Skip text inside <a>/<sup> — handled when we hit the <a> tag.
            parent = child.parent
            if parent is not None and parent.name in ("a", "sup"):
                continue
            out.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "img":
                classes = child.get("class") or []
                src = child.get("src") or child.get("data-src") or ""
                if "h-pic" in classes or "wrepub/" in src:
                    stem = rare_char_name(src)
                    out.append(f"![](../images/{stem}.png)")
                # Non-rare images are collected separately at paragraph end.
            elif child.name == "a":
                # Emit the anchor's text (footnote marker like [9]).
                out.append(child.get_text())
    return "".join(out)


def _looks_like_css(text: str) -> bool:
    """Detect CSS content masquerading as paragraph text.

    Some chapters (e.g.导读) embed stylesheet content as <p> text in
    the XHTML source. Detect by the density of { } ; characters.
    """
    if not text:
        return False
    css_chars = text.count("{") + text.count("}") + text.count(";")
    return css_chars > 0 and len(text) / css_chars < 40


def _compact_inline(text: str) -> str:
    """Collapse whitespace in inline-rendered text while preserving rare-char tokens.

    Chinese text should have no internal spaces, but the ``![](../images/...)``
    rare-char tokens must stay intact. We split on the token pattern, strip
    whitespace from the text segments, and rejoin.
    """
    segments = re.split(r"(!\[\]\(\.\./images/[^)]+\.png\))", text)
    out: list[str] = []
    for i, seg in enumerate(segments):
        if i % 2 == 1:
            out.append(seg)  # rare-char token — keep verbatim
        else:
            out.append(re.sub(r"\s+", "", seg))  # text — drop whitespace
    return "".join(out)


def xhtml_to_markdown(
    xhtml: str,
    *,
    page_url: str,
) -> tuple[str, set[str]]:
    """Convert decoded chapter XHTML into markdown.

    The XHTML is a concatenation of EPUB documents (one per ``<?xml``
    prolog). Each contributes a heading (``<h2>``/``<h3>``) and one or more
    ``<p>`` paragraphs. Rare-character ``<img class="h-pic">`` elements are
    inlined at their exact source position — this is the zero-offset path
    that replaces canvas coordinate merging.

    Image downloads are **not** performed here (this function is sync and
    the download callback is async). The caller (fetcher) uses
    :func:`collect_rare_char_srcs` to discover rare-char ``src`` URLs and
    downloads them separately, then this function's output already contains
    ``![](../images/wr{stem}.png)`` tokens referencing the downloaded files.

    Returns ``(markdown, rare_char_srcs)`` where the second element is the
    set of rare-char ``src`` URLs that were inlined (so the caller can
    exclude them from the tail-image list).
    """
    from bs4 import BeautifulSoup

    if not xhtml:
        return "", set()

    rare_srcs_inlined: set[str] = set()
    tail_image_urls: list[str] = []
    blocks: list[str] = []

    for doc in _split_xhtml_docs(xhtml):
        soup = BeautifulSoup(doc, "lxml-xml")

        # Headings: <h2 class="secondTitle-1"> → ## title
        #          <h3 class="thirdTitle-1">  → ### title
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            level = int(heading.name[1])
            text = heading.get_text(strip=True)
            if text:
                blocks.append(f"{'#' * level} {text}")
            heading.decompose()

        # Paragraphs
        for p in soup.find_all("p"):
            # Collect non-rare images for the tail (before any CSS check).
            for img in p.find_all("img"):
                classes = img.get("class") or []
                src = img.get("src") or img.get("data-src") or ""
                if "h-pic" in classes or "wrepub/" in src:
                    continue
                if src:
                    tail_image_urls.append(src)
            inline = _render_inline(p)
            inline = _compact_inline(inline)
            stripped = inline.strip()
            if not stripped:
                continue
            # Skip paragraphs that are actually CSS (some 导读 chapters
            # embed stylesheet content as <p> text).
            if _looks_like_css(stripped):
                continue
            blocks.append(stripped)

    # Record which rare-char srcs were inlined (for tail exclusion).
    for src, _stem in collect_rare_char_srcs(xhtml).items():
        rare_srcs_inlined.add(src)

    # Append non-rare tail images via ImageFilter (dedup, skip watermarks).
    tail_lines: list[str] = []
    if tail_image_urls:
        image_filter = ImageFilter()
        tail_lines = image_filter.markdown_lines(
            tail_image_urls, base_url=page_url, exclude=rare_srcs_inlined
        )

    parts = [b for b in blocks if b.strip()] + tail_lines
    return "\n\n".join(parts).strip(), rare_srcs_inlined
