from __future__ import annotations

import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

# Fixed layout widths (in display columns, not character count).
_BAR_WIDTH = 24
_LINE_WIDTH = 80  # pad every redraw to this so shorter lines clear leftover tails

# A progress callback receives a single ProgressEvent and returns nothing.
ProgressCallback = Callable[["ProgressEvent"], None]


@dataclass(frozen=True)
class ProgressEvent:
    """A single progress signal emitted by the crawler/fetcher.

    ``kind`` is one of: cover, toc, started, chapter_started, waiting,
    chapter_saved, warning, finished.  Callers should only read fields
    relevant to the kind; unused fields default to None.
    """

    kind: str
    total: int | None = None
    index: int | None = None
    title: str | None = None
    ok: bool | None = None
    message: str | None = None


def emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    """Invoke ``callback`` if set; swallow nothing — let errors surface."""
    if callback is None:
        return
    callback(event)


def _char_width(ch: str) -> int:
    """Display columns a character occupies: 2 for CJK/fullwidth, else 1."""
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1


def _display_width(text: str) -> int:
    return sum(_char_width(ch) for ch in text)


def _bar(progress: float, width: int = _BAR_WIDTH) -> str:
    filled = int(round(progress * width))
    filled = max(0, min(width, filled))
    return "·" * filled + " " * (width - filled)


def _truncate_to_width(text: str, width: int) -> str:
    """Truncate ``text`` so its *display* width fits ``width`` columns."""
    if _display_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        cw = _char_width(ch)
        if used + cw > width - 1:  # reserve 1 for the ellipsis
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def _pad_to_width(text: str, width: int) -> str:
    """Right-pad ``text`` with spaces to a fixed display width."""
    pad = max(0, width - _display_width(text))
    return text + " " * pad


@dataclass
class ProgressRenderer:
    """Render ProgressEvents to stdout as a single refreshable line.

    In a TTY the current progress line is overwritten with ``\\r`` on each
    event, giving a live ``[N/total] 标题 ········ N%`` view.  When stdout is
    not a TTY (redirected/piped) the progress line is suppressed to avoid
    scattering carriage returns through logs; only warnings and the final
    message are printed.
    """

    stream: Any = None
    is_tty: bool | None = None
    # Render state — kept here so ``waiting`` can repeat the current chapter.
    total: int | None = field(default=None)
    index: int | None = field(default=None)
    title: str | None = field(default=None)
    _line_written: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.stream is None:
            self.stream = sys.stdout
        if self.is_tty is None:
            try:
                self.is_tty = bool(self.stream.isatty())
            except Exception:
                self.is_tty = False

    def handle(self, event: ProgressEvent) -> None:
        if event.kind == "warning":
            # Warnings break the line so they aren't overwritten.
            self._newline()
            print(f"警告：{event.message}", file=self.stream)
            self.stream.flush()
            self._redraw_current()
            return

        if event.kind == "finished":
            self._newline()
            return

        if event.kind in ("cover", "toc", "started", "chapter_started",
                          "waiting", "chapter_saved"):
            self._update_state(event)
            self._redraw_current()
            return

    # -- internals --------------------------------------------------------

    def _update_state(self, event: ProgressEvent) -> None:
        if event.kind == "started":
            self.total = event.total
            self.index = None
            self.title = None
            return
        if event.kind in ("toc", "cover"):
            self.total = event.total if event.total is not None else self.total
            return
        # chapter_started / chapter_saved / waiting carry index/title.
        if event.index is not None:
            self.index = event.index
        if event.title is not None:
            self.title = event.title

    def _redraw_current(self) -> None:
        if not self.is_tty:
            return
        line = self._format_line()
        if line is None:
            return
        # Pad to a fixed display width so a shorter line overwrites any
        # leftover tail from the previous (longer) line.
        line = _pad_to_width(line, _LINE_WIDTH)
        self.stream.write("\r" + line)
        self.stream.flush()
        self._line_written = True

    def _format_line(self) -> str | None:
        total = self.total
        index = self.index
        if total is None:
            return None
        if index is None:
            # Pre-crawl phases (cover/toc/started) without a chapter yet.
            phase = self._phase_label()
            return f"{phase}（共 {total} 章）"

        progress = index / total if total else 0.0
        pct = int(round(progress * 100))
        prefix = f"[{index}/{total}] "
        suffix = f" {pct:>3}%"
        bar = _bar(progress)
        # Fixed columns: prefix + title(padded) + space + bar + suffix.
        fixed = _display_width(prefix) + 1 + _BAR_WIDTH + _display_width(suffix)
        title_budget = max(8, _LINE_WIDTH - fixed)
        title = _truncate_to_width(self.title or "", title_budget)
        title = _pad_to_width(title, title_budget)  # stable bar position
        return f"{prefix}{title} {bar}{suffix}"

    def _phase_label(self) -> str:
        return "准备中"

    def _newline(self) -> None:
        if self._line_written:
            self.stream.write("\n")
            self.stream.flush()
            self._line_written = False
