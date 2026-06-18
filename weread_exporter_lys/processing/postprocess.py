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
# These two are overridden below with correct Python implementations
# because the .pyc versions produce mismatched anchor IDs.
format_footnotes = _impl.format_footnotes  # replaced below
add_footnote_links = _impl.add_footnote_links  # replaced below
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
#  Override format_footnotes / add_footnote_links from .pyc
#  The .pyc versions produce mismatched anchor IDs between body refs and
#  annotation defs, causing footnote links to jump to the wrong annotation.
#  We replace them with correct pure-Python implementations that pair refs
#  and defs by position within each 【原文】–【注释】 block.
# ---------------------------------------------------------------------------

# Sentinel used to protect already-processed links from double-processing.
# Matches both our new 2-number IDs (fn_1_2) and old .pyc 3-number IDs (note_3_1_1).
_FN_ID_RE = re.compile(r'(?:fnref_|fn_|ref_|note_)\d+_\d+(?:_\d+)?')

# Footage marker in body text — bare [n] that should become <sup>[n]</sup>.
# The sections are already split into body vs annotation, so inside a body
# section every ``[n]`` is a footnote ref (no false positives from annotation
# headers or markdown link syntax, both of which have been resolved earlier).
_BARE_FN = re.compile(r'\[(\d+)\]')

# Pattern for [n] at line start in an annotation block (possibly leading a
# definition line like "[1]黄帝：...").
_ANN_FN_LINE = re.compile(r'^\[(\d+)\](?=\S)', re.MULTILINE)

# Section headers that delimit 【原文】 / 【注释】 blocks.
_SEC_HDR = re.compile(r'^(#{1,3}\s*【(原文|注释|译文)】.*)$', re.MULTILINE)


def _section_split(text: str) -> list[dict]:
    """Split ``text`` into a list of section dicts, each with ``type``,
    ``header``, ``content`` and position info.
    """
    sections: list[dict] = []

    # Capture any text before the first section header.
    first_match = _SEC_HDR.search(text)
    pre_start = 0
    if first_match:
        if first_match.start() > 0:
            sections.append({
                "type": "other",
                "header": "",
                "content": text[0:first_match.start()],
            })
        pre_start = first_match.start()
    else:
        # No headers at all — return the whole text as one "other" section.
        if text.strip():
            sections.append({
                "type": "other",
                "header": "",
                "content": text,
            })
        return sections

    # Walk through headers.
    current: dict | None = None
    for m in _SEC_HDR.finditer(text):
        if current is not None:
            current["content"] = text[current["start"]:m.start()]
            sections.append(current)

        header = m.group(1)
        if "原文" in header:
            stype = "body"
        elif "注释" in header:
            stype = "annotation"
        elif "译文" in header:
            stype = "translation"
        else:
            stype = "other"

        current = {
            "type": stype,
            "header": header,
            "start": m.end(),
        }

    if current is not None:
        current["content"] = text[current["start"]:]
        sections.append(current)

    return sections


def _strip_existing_anchors(text: str) -> str:
    """Remove any existing footnote anchor tags so we can re-anchor cleanly.

    Keeps the ``[n]`` text and any surrounding ``<sup>`` wrapper, only strips
    ``<a id=... href=...>`` / ``</a>``.
    """
    # Remove opening <a ...> and closing </a> around footnote anchors.
    # Handles old .pyc 3-number IDs (note_3_1_1 / ref_3_1_1) and our new
    # 2-number IDs (fn_1_2 / fnref_1_2).
    text = re.sub(
        r'<a\s[^>]*?\b(?:id|href)="#?(?:fnref_|fn_|ref_|note_)\d+_\d+(?:_\d+)?"[^>]*>',
        "", text,
    )
    text = text.replace("</a>", "")
    return text


def _format_footnotes_v2(text: str) -> str:
    """Convert isolated ``[n]`` markers in body sections to ``<sup>[n]</sup>``.

    This runs AFTER ``clean_characters`` (which does initial text reflow) and
    ensures that body footnote markers are properly wrapped before anchor
    insertion.  Annotation blocks are left untouched.
    """
    sections = _section_split(text)
    out: list[str] = []

    for sec in sections:
        content = sec["content"]
        if sec["type"] == "body":
            # Replace bare [n] with <sup>[n]</sup> inside body text.
            content = _BARE_FN.sub(r"<sup>[\1]</sup>", content)
        out.append(sec["header"] + content)

    return "".join(out)


def _add_footnote_links_v2(text: str) -> str:
    """Add bidirectional HTML anchors to footnote refs and annotation defs.

    Each 【原文】–【注释】 pair is processed independently.  Body refs
    (``<sup>[n]</sup>``) and annotation defs (``[n]`` at line start) are
    matched by sequential position within the pair and assigned matching
    ``fn_P_N`` / ``fnref_P_N`` IDs.

    Any section that already contains anchor IDs (from a previous run) is
    first stripped so re-processing is idempotent.
    """
    # Idempotency: strip any anchors from a previous run.
    if _FN_ID_RE.search(text):
        text = _strip_existing_anchors(text)

    sections = _section_split(text)
    pair_index = 0  # global counter for 【原文】–【注释】 pairs

    # We'll build the output by walking sections and replacing in-place.
    # Because sections may have different content after replacement, we
    # reconstruct the full text from the modified sections.
    result: list[str] = []
    i = 0

    while i < len(sections):
        sec = sections[i]

        if sec["type"] == "body" and i + 1 < len(sections) and sections[i + 1]["type"] == "annotation":
            body_sec = sec
            ann_sec = sections[i + 1]
            pair_index += 1

            body_content = body_sec["content"]
            ann_content = ann_sec["content"]

            # Collect body refs: <sup>[n]</sup> tokens.
            sup_pat = re.compile(r"<sup>\[(\d+)\]</sup>")
            body_refs: list[tuple[int, re.Match]] = []
            for m in sup_pat.finditer(body_content):
                body_refs.append((int(m.group(1)), m))

            # Collect annotation defs: [n] at line start.
            ann_defs: list[tuple[int, re.Match]] = []
            for m in _ANN_FN_LINE.finditer(ann_content):
                ann_defs.append((int(m.group(1)), m))

            # --- Replace body refs with anchored versions ---
            def _replace_body(match: re.Match, ref_pos: int) -> str:
                num = match.group(1)
                anchor_id = f"fnref_{pair_index}_{ref_pos}"
                note_id = f"fn_{pair_index}_{ref_pos}"
                return (
                    f'<sup><a href="#{note_id}" id="{anchor_id}">'
                    f"[{num}]</a></sup>"
                )

            # Only anchor up to min(refs, defs) — excess refs stay as
            # plain <sup>[n]</sup> because there is no matching annotation.
            paired = min(len(body_refs), len(ann_defs))
            total_refs = len(body_refs)

            # Process in reverse so earlier replacements don't shift later offsets.
            for rev_idx, (_, m) in enumerate(reversed(body_refs)):
                ref_pos = total_refs - rev_idx
                if ref_pos > paired:
                    continue  # excess ref — keep as plain <sup>[n]</sup>
                body_content = (
                    body_content[: m.start()]
                    + _replace_body(m, ref_pos)
                    + body_content[m.end() :]
                )

            # --- Replace annotation defs with anchored versions ---
            def _replace_ann(match: re.Match, ref_pos: int) -> str:
                num = match.group(1)
                anchor_id = f"fnref_{pair_index}_{ref_pos}"
                note_id = f"fn_{pair_index}_{ref_pos}"
                return (
                    f'<a id="{note_id}" href="#{anchor_id}">'
                    f"[{num}]</a>"
                )

            # Build list of annotation positions in original (still clean) ann_content.
            ann_defs2: list[re.Match] = list(_ANN_FN_LINE.finditer(ann_content))

            # Replace in reverse order, only up to paired count.
            for pos_in_block, m in enumerate(reversed(ann_defs2)):
                real_pos = len(ann_defs2) - pos_in_block
                if real_pos > paired:
                    continue  # excess def — keep as raw [n]
                ann_content = (
                    ann_content[: m.start()]
                    + _replace_ann(m, real_pos)
                    + ann_content[m.end() :]
                )

            result.append(body_sec["header"] + body_content)
            result.append(ann_sec["header"] + ann_content)
            i += 2
        else:
            result.append(sec["header"] + sec["content"])
            i += 1

    return "".join(result)


# Override the .pyc exports with our corrected implementations.
format_footnotes = _format_footnotes_v2
add_footnote_links = _add_footnote_links_v2

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
