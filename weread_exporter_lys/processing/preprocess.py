"""Pre-process chapter Markdown before merging.

Steps (per-chapter):
1.  ``remove_watermarks`` — strip data:-URI (base64) image lines.
2.  ``normalize_format`` — whitespace, blank-line compaction, zero-width space removal.
3.  ``localize_images`` — download remote images, rewrite URLs to local ``images/xxx``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ..progress import ProgressCallback, ProgressEvent

# ---------------------------------------------------------------------------
# Load existing compiled functions from backup .pyc (Python 3.10 bytecode).
# ---------------------------------------------------------------------------

_PYC = Path(__file__).parent / "__pycache_backup__" / "preprocess.cpython-310.pyc"
if not _PYC.exists():
    raise RuntimeError(
        "preprocess.cpython-310.pyc backup not found; "
        "the processing layer cannot function without its compiled bytecode."
    )

# Load with a package-qualified name so relative imports inside the .pyc
# (e.g. ``from ..crawler.extractor import normalize_markdown_text``) resolve
# correctly.
_ORIG_NAME = "weread_exporter_lys.processing._preprocess_pyc"
_spec = importlib.util.spec_from_file_location(_ORIG_NAME, str(_PYC))
_impl = importlib.util.module_from_spec(_spec)
import sys
sys.modules[_ORIG_NAME] = _impl
_spec.loader.exec_module(_impl)

# Direct re-exports (no callback involved).
remove_watermarks = _impl.remove_watermarks
normalize_format = _impl.normalize_format
_filename = _impl._filename
_guess_ext = _impl._guess_ext
_download = _impl._download
load_cookies = _impl.load_cookies

# ---------------------------------------------------------------------------
# localize_images — the compiled bytecode calls the progress callback with
# the *old* signature ``callback(kind: str, data: dict)``, but the current
# ``ProgressCallback`` expects a single ``ProgressEvent`` argument.
# We wrap the underlying pyc function so the callback adapter is transparent.
# ---------------------------------------------------------------------------

_RAW_localize_images = _impl.localize_images


def _adapt_progress_callback(on_progress: ProgressCallback | None) -> Any:
    """Return a callable that forwards ``(kind, data)`` → ``ProgressEvent``."""
    if on_progress is None:
        return None

    def _forward(kind: str, data: dict | None = None) -> None:
        on_progress(ProgressEvent(
            kind=kind,
            total=data.get("total") if isinstance(data, dict) else None,
            index=data.get("index") if isinstance(data, dict) else None,
            message=data.get("message") if isinstance(data, dict) else None,
        ))

    return _forward


def localize_images(
    markdown: str,
    book_dir: Path,
    cookies: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, list[str]]:
    """Download remote images, store locally, and rewrite markdown URLs.

    Wraps the compiled ``localize_images`` so the old-style
    ``callback(kind, data)`` signature used by the bytecode is adapted
    to the current ``ProgressCallback(ProgressEvent)`` API.
    """
    adapted = _adapt_progress_callback(on_progress)
    return _RAW_localize_images(markdown, book_dir, cookies, on_progress=adapted)
