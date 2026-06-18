"""Post-process the merged Markdown before format conversion.

Steps (on the merged document):
1.  ``clean_characters`` — strip backticks, reflow hard-wrapped Chinese text into
    natural paragraphs, remove zero-width spaces.
2.  ``format_footnotes`` — merge isolated ``[n]`` lines and convert inline
    ``[n]`` markers to ``<sup>[n]</sup>`` HTML tags.
3.  ``add_footnote_links`` — generate bidirectional HTML anchors so body
    references and annotation definitions can jump to each other.
4.  ``resolve_rare_chars`` — resolve rare character placeholders (▣) using
    context-based pattern matching.
5.  ``inline_rare_char_images`` — NEW: transform rare-character markdown images
    into inline HTML ``<img class="rare-char">`` tags so they render at text
    size instead of as large block images.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Load existing compiled functions from backup .pyc (Python 3.10 bytecode).
# We load from __pycache_backup__ so Python's import machinery won't overwrite
# the original bytecode when it compiles this .py wrapper.
# ---------------------------------------------------------------------------

_PYC = Path(__file__).parent / "__pycache_backup__" / "postprocess.cpython-310.pyc"
if not _PYC.exists():
    raise RuntimeError(
        "postprocess.cpython-310.pyc backup not found; "
        "the processing layer cannot function without its compiled bytecode."
    )

_spec = importlib.util.spec_from_file_location(
    "_postprocess_impl", str(_PYC)
)
_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_impl)

clean_characters = _impl.clean_characters
format_footnotes = _impl.format_footnotes
add_footnote_links = _impl.add_footnote_links
resolve_rare_chars = _impl.resolve_rare_chars

# Re-export constants that other modules (pipeline, tests) may reference.
_ZERO_WIDTH: re.Pattern = _impl._ZERO_WIDTH
_STANDALONE_NOTE: re.Pattern = _impl._STANDALONE_NOTE
_INLINE_NOTE: re.Pattern = _impl._INLINE_NOTE
_HEADING: re.Pattern = _impl._HEADING
_IMAGE: re.Pattern = _impl._IMAGE
_ANNOTATION_HEADING: re.Pattern = _impl._ANNOTATION_HEADING
_ANNOTATION_DEF: re.Pattern = _impl._ANNOTATION_DEF
_SUP_NOTE: re.Pattern = _impl._SUP_NOTE
_CHAPTER_HEADING: re.Pattern = _impl._CHAPTER_HEADING
_RARE_CHAR_PLACEHOLDER: str = _impl._RARE_CHAR_PLACEHOLDER
_RARE_CHAR_PATTERNS: list = _impl._RARE_CHAR_PATTERNS

# ---------------------------------------------------------------------------
#  New: inline rare-character images
# ---------------------------------------------------------------------------

# Matches rare-character markdown images: ![](../images/wrXXXX.png)
# Match rare-character markdown images.
# Path may be ``../images/wr*.png`` (relative to content/N.md) or
# ``images/wr*.png`` (relative to book_dir/title.md after merge).
_RARE_CHAR_IMG = re.compile(r"!\[[^\]]*\]\((?:\.\./)?images/wr[^)]+\.png\)")


def inline_rare_char_images(markdown: str) -> str:
    """Transform rare-character markdown images into inline HTML ``<img>`` tags.

    Rare-character images produced by the crawler use the naming convention
    ``wr{data-wr-id}.png`` (e.g. ``wrksu7y7ih50s.png``).  This function finds
    every ``![](../images/wr*.png)`` token and replaces it with::

        <img src="../images/wrXXXX.png" style="height:1em…" class="rare-char">

    Normal illustrations (e.g. ``images/abc123.jpg``) use a different naming
    scheme (MD5 hash) and are **not** touched.

    Inline ``style`` is used as the primary sizing mechanism so the images
    render correctly even when the EPUB reading system does not load external
    CSS.  The ``rare-char`` class is kept as a secondary hook for
    HTML/PDF output.
    """
    def _replace(match: re.Match) -> str:
        full = match.group(0)
        start = full.index("(") + 1
        end = full.rindex(")")
        src = full[start:end]
        return (
            f'<img src="{src}" class="rare-char"'
            f' style="height:1em;width:auto;display:inline;'
            f'vertical-align:text-bottom;margin:0">'
        )

    return _RARE_CHAR_IMG.sub(_replace, markdown)
