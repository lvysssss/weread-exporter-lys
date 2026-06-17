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


def merge_rare_chars(
    lines: list[dict],
    rare_chars: list[dict],
    *,
    line_tolerance: float = 20.0,
) -> str:
    """Interleave rare-character image tokens into canvas text lines by y-coordinate.

    ``lines`` is the output of ``wrpaHandler.getLinesWithCoords()``: a list of
    ``{text, y, fontSize, minFontSize, prefix}`` records, where ``prefix`` is the
    markdown heading prefix already decided by the hook (``''`` / ``'## '`` /
    ``'### '``). ``rare_chars`` is a list of ``{local_path, x, y, ...}`` records
    for each rare-character <img>, where ``y`` is the translate-y (px) in the same
    chapter coordinate system as the canvas lines.

    Because the canvas text leaves no placeholder where a rare char sits (it just
    truncates the line and the next line continues), each rare-char image is
    appended to the end of the closest line by y. Rare chars assigned to the same
    line are appended in ascending x order, so a row of several rare chars stays
    in reading order. Lines and rare chars without a close match (beyond
    ``line_tolerance``) are preserved unchanged / appended at the end.
    """
    if not lines:
        return ""

    # Work on copies so the caller's records are untouched.
    rows = [
        {
            "text": str(rec.get("text", "")),
            "y": float(rec.get("y") or 0),
            "prefix": str(rec.get("prefix", "")),
        }
        for rec in lines
    ]

    attachments: list[tuple[int, str]] = []  # (row_index, "![](local_path)")
    orphans: list[str] = []
    for rc in rare_chars:
        local_path = rc.get("local_path")
        if not local_path:
            continue
        rc_y = float(rc.get("y") or 0)
        rc_x = float(rc.get("x") or 0)
        # Closest row by absolute y distance.
        best_idx = -1
        best_dist = float("inf")
        for idx, row in enumerate(rows):
            dist = abs(row["y"] - rc_y)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        if best_idx < 0 or best_dist > line_tolerance:
            orphans.append((rc_y, rc_x, f"![]({local_path})"))
            continue
        attachments.append((best_idx, rc_x, f"![]({local_path})"))

    # Group attachments by row, then order each group by x ascending.
    by_row: dict[int, list[tuple[float, str]]] = {}
    for idx, x, token in attachments:
        by_row.setdefault(idx, []).append((x, token))
    for idx, items in by_row.items():
        items.sort(key=lambda pair: pair[0])
        suffix = "".join(token for _, token in items)
        rows[idx]["text"] = rows[idx]["text"] + suffix

    parts = [f'{row["prefix"]}{row["text"]}' for row in rows if row["text"]]
    # Orphan rare chars (no nearby line) go at the end in y order so they are not lost.
    for _, _, token in sorted(orphans, key=lambda t: (t[0], t[1])):
        parts.append(token)
    return "\n\n".join(parts).strip()



def looks_like_login_or_paywall(text: str) -> bool:
    return bool(re.search(r"登录|扫码|微信扫码|付费|购买|会员", text or ""))
