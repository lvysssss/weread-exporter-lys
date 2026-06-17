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
    """Interleave rare-character image tokens into canvas text lines by coordinate.

    ``lines`` is the output of ``wrpaHandler.getLinesWithCoords()``: each record
    has ``{text, fragments, y, fontSize, minFontSize, prefix}``. ``fragments`` is
    a list of ``{text, xCss}`` (canvas-CSS space, sorted by x) — the per-glyph
    pieces the canvas drew, with gaps where rare chars sit. ``y`` is the line's
    top y in canvas-CSS space. ``rare_chars`` is a list of
    ``{local_path, x, y, ...}`` where ``x``/``y`` are the rare-char <img>
    translate coords converted to the same canvas-CSS space.

    Each rare char is matched to the closest line by y, then inserted *inside*
    that line at the x gap where the canvas left a blank: the token goes just
    before the first fragment whose ``xCss`` exceeds the image's ``x``. If no
    fragment is to the right (image at line end) the token is appended. This
    fixes cases like ``[3]：搅乱。`` where the image belongs between ``[3]`` and
    ``：`` rather than at line end. Rare chars with no close line (beyond
    ``line_tolerance``) are appended at the end in y order so nothing is lost.
    """
    if not lines:
        return ""

    # Build mutable copies of rows; prefer fragment-level geometry when present.
    rows: list[dict] = []
    for rec in lines:
        fragments = rec.get("fragments")
        if fragments:
            frags = [
                {"text": str(f.get("text", "")), "xCss": float(f.get("xCss") or 0)}
                for f in fragments
            ]
        else:
            # Backward-compat: synthesize a single fragment from the joined text
            # so the image falls back to line-end insertion.
            frags = [{"text": str(rec.get("text", "")), "xCss": 0.0}] if rec.get("text") else []
        rows.append({
            "fragments": frags,
            "y": float(rec.get("y") or 0),
            "prefix": str(rec.get("prefix", "")),
        })

    # Assign each rare char to its closest row by y.
    by_row: dict[int, list[dict]] = {}
    orphans: list[tuple[float, float, str]] = []
    for rc in rare_chars:
        local_path = rc.get("local_path")
        if not local_path:
            continue
        rc_x = float(rc.get("x") or 0)
        rc_y = float(rc.get("y") or 0)
        best_idx = -1
        best_dist = float("inf")
        for idx, row in enumerate(rows):
            dist = abs(row["y"] - rc_y)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        if best_idx < 0 or best_dist > line_tolerance or not rows[best_idx]["fragments"]:
            orphans.append((rc_y, rc_x, f"![]({local_path})"))
            continue
        by_row.setdefault(best_idx, []).append({"x": rc_x, "token": f"![]({local_path})"})

    # Insert each assigned image into its row at the correct x gap.
    for idx, items in by_row.items():
        items.sort(key=lambda it: it["x"])
        frags = rows[idx]["fragments"]
        for item in items:
            token, rc_x = item["token"], item["x"]
            # Find the first fragment whose xCss is >= the image x; insert before it.
            insert_at = len(frags)
            for i, fr in enumerate(frags):
                if fr["xCss"] >= rc_x:
                    insert_at = i
                    break
            frags.insert(insert_at, {"text": token, "xCss": rc_x, "isImage": True})

    parts: list[str] = []
    for row in rows:
        if not row["fragments"]:
            continue
        body = "".join(fr["text"] for fr in row["fragments"])
        if body.strip():
            parts.append(f'{row["prefix"]}{body}')
    # Orphan rare chars (no nearby line) go at the end in y order.
    for _, _, token in sorted(orphans, key=lambda t: (t[0], t[1])):
        parts.append(token)
    return "\n\n".join(parts).strip()



def looks_like_login_or_paywall(text: str) -> bool:
    return bool(re.search(r"登录|扫码|微信扫码|付费|购买|会员", text or ""))
