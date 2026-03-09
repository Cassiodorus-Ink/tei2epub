"""Render the intermediate data model to XHTML chapter strings.

Each Chapter is rendered to a standalone XHTML document suitable for
inclusion as a chapter file in an EPUB.  Notes are rendered both as
inline superscript references and as footnote asides at the end of
the chapter.
"""

from __future__ import annotations

import io
import re
from html import escape
from pathlib import Path as _Path

from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-untyped]

from tei2epub.model import (
    Chapter,
    Foreign,
    Hi,
    InlineContent,
    InlineNode,
    Note,
    PageBreak,
    Paragraph,
    Section,
    VerseBlock,
    VerseLine,
    Work,
)

XHTML_PREAMBLE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml"'
    ' xmlns:epub="http://www.idpf.org/2007/ops">\n'
    "<head>\n"
    '<meta charset="utf-8"/>\n'
    '<link rel="stylesheet" href="style.css" type="text/css"/>\n'
    "<title>{title}</title>\n"
    "</head>\n"
    "<body>\n"
)

XHTML_POSTAMBLE = "</body>\n</html>\n"


class _RenderContext:
    """Tracks per-chapter state during rendering, notably note numbering."""

    def __init__(self, chapter_id: str) -> None:
        self.chapter_id = chapter_id
        self._note_counter = 0
        self.notes: list[tuple[str, str]] = []  # (id, note_text)

    def add_note(self, text: str) -> str:
        """Register a note and return its HTML id."""
        self._note_counter += 1
        note_id = f"n-{self.chapter_id}-{self._note_counter}"
        self.notes.append((note_id, text))
        return note_id


# -- Inline rendering --------------------------------------------------------

# Punctuation that should be moved before a footnote superscript.
# Excludes dashes (en-dash, em-dash, hyphen).
_NOTE_PUNCT = frozenset(".,;:?!)\u00bb\u201d\u2019")

# Suffix that marks a noteref superscript in rendered HTML.
_NOTEREF_SUFFIX = "</sup></a>"


def _pull_punct(nodes: list[InlineNode], i: int) -> str:
    """Pull leading punctuation from the node after index *i*.

    If ``nodes[i+1]`` is a text node whose content (after stripping
    leading whitespace) starts with punctuation characters from
    :data:`_NOTE_PUNCT`, those characters are removed from the node
    and returned.  Otherwise returns ``""``.

    This is used to move punctuation before a footnote superscript so
    that e.g. ``word<sup>1</sup>,`` becomes ``word,<sup>1</sup>``.
    """
    if i + 1 >= len(nodes):
        return ""
    nxt = nodes[i + 1]
    if not isinstance(nxt, str):
        return ""
    # Strip leading whitespace so e.g. "\n," is recognised.
    nxt_stripped = nxt.lstrip()
    if not nxt_stripped or nxt_stripped[0] not in _NOTE_PUNCT:
        return ""
    j = 0
    while j < len(nxt_stripped) and nxt_stripped[j] in _NOTE_PUNCT:
        j += 1
    pulled = nxt_stripped[:j]
    nodes[i + 1] = nxt_stripped[j:]
    return pulled


def _ends_with_noteref(html: str) -> bool:
    """True if *html* ends with a noteref superscript.

    Also matches when the noteref is followed by closing container
    tags like ``</em>`` or ``</span>``, since the noteref is still
    the last meaningful inline content.
    """
    # Strip closing container tags to expose the noteref.
    s = html
    while s.endswith("</em>") or s.endswith("</span>"):
        if s.endswith("</em>"):
            s = s[:-len("</em>")]
        elif s.endswith("</span>"):
            s = s[:-len("</span>")]
    return s.endswith(_NOTEREF_SUFFIX)


def _render_inline(nodes: InlineContent, ctx: _RenderContext) -> str:
    """Render a list of inline nodes to an HTML string.

    When a Note is followed by a text node starting with punctuation
    (other than a dash), the punctuation is moved before the
    superscript so that e.g. ``word.^1`` renders as ``word.¹`` rather
    than ``word¹.``.

    This also works when the Note is the last child of a container
    node (``<hi>``, ``<foreign>``, etc.) — the punctuation after the
    closing tag is still pulled before the superscript.
    """
    parts: list[str] = []
    # Copy so we can safely mutate when swapping punctuation past notes.
    nodes = list(nodes)
    # We need lookahead for note-punctuation swapping, so work with
    # an index rather than a plain for-each.
    i = 0
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, str):
            parts.append(escape(node.replace("--", "\u2014")))
        elif isinstance(node, Hi):
            inner = _render_inline(node.content, ctx)
            # Strip trailing whitespace (often XML indentation between
            # </note> and </hi>) so we can detect a trailing noteref.
            inner_stripped = inner.rstrip()
            if _ends_with_noteref(inner_stripped):
                pulled = _pull_punct(nodes, i)
                # Insert pulled punctuation before the noteref inside
                # the <em> tag: ...punct<a ...>...</a></em>
                insert_pos = inner_stripped.rfind("<a ")
                inner = inner_stripped[:insert_pos] + escape(pulled) + inner_stripped[insert_pos:]
            parts.append(f"<em>{inner}</em>")
        elif isinstance(node, Note):
            # Strip trailing whitespace from the preceding part so
            # the superscript sits tight against the preceding word.
            if parts:
                parts[-1] = parts[-1].rstrip()

            # Look ahead: if the next node is a string starting with
            # punctuation, pull that punctuation before the superscript.
            pulled = _pull_punct(nodes, i)

            parts.append(escape(pulled))
            note_id = ctx.add_note(node.text)
            ref_id = f"ref-{note_id}"
            parts.append(
                f'<a epub:type="noteref" id="{ref_id}"'
                f' href="#{note_id}" role="doc-noteref">'
                f"<sup>{ctx._note_counter}</sup></a>"
            )
        elif isinstance(node, PageBreak):
            display = node.display_n
            parts.append(
                f'<span class="pb" title="Migne col. {escape(display)}">'
                f"{escape(display)}</span>"
            )
        elif isinstance(node, Foreign):
            inner = _render_inline(node.content, ctx)
            inner_stripped = inner.rstrip()
            if _ends_with_noteref(inner_stripped):
                pulled = _pull_punct(nodes, i)
                insert_pos = inner_stripped.rfind("<a ")
                inner = inner_stripped[:insert_pos] + escape(pulled) + inner_stripped[insert_pos:]
            lang_attr = f' lang="{escape(node.lang)}"' if node.lang else ""
            parts.append(f'<span class="foreign"{lang_attr}>{inner}</span>')
        elif isinstance(node, VerseLine):
            inner = _render_inline(node.content, ctx)
            cls = "verse-line verse-indent" if node.indent else "verse-line"
            parts.append(f'<span class="{cls}">{inner}</span><br/>')
        i += 1
    return "".join(parts)


# -- Heading rendering -------------------------------------------------------

def _fix_heading_dash(text: str) -> str:
    """Replace '-- ' in chapter headings with an em-dash."""
    return text.replace("-- ", "\u2014 ")


# -- Block rendering ---------------------------------------------------------

def _para_ends_with_verse(para: Paragraph) -> bool:
    """Return True if *para*'s last significant content is verse.

    A paragraph "ends with verse" when, ignoring trailing whitespace
    strings and Note nodes, the last node is a VerseLine.  This is
    used to suppress the text-indent on the following paragraph, since
    the verse-group block already provides visual separation.
    """
    for node in reversed(para.content):
        if isinstance(node, str) and not node.strip():
            continue
        if isinstance(node, Note):
            continue
        return isinstance(node, VerseLine)
    return False


def _render_paragraph(
    para: Paragraph, ctx: _RenderContext, *, no_indent: bool = False,
) -> str:
    """Render a Paragraph to an HTML <p> string.

    If the paragraph contains VerseLine nodes, consecutive verse lines
    are wrapped in a <span class="verse-group"> so they can be styled
    as a visually distinct indented block within the prose.

    When *no_indent* is True the paragraph is given a ``no-indent``
    class so that CSS suppresses the first-line indent — used for
    paragraphs that continue after a displayed verse quotation.
    """
    p_tag = '<p class="no-indent">' if no_indent else "<p>"
    if not any(isinstance(n, VerseLine) for n in para.content):
        inner = _render_inline(para.content, ctx)
        return f"{p_tag}{inner}</p>\n"

    # Split content into runs of verse vs non-verse, rendering each
    # run appropriately.
    parts: list[str] = []
    parts.append(p_tag)

    verse_buf: list[VerseLine] = []

    def _is_ws(node: object) -> bool:
        return isinstance(node, str) and not node.strip()

    def _extract_leading_note(container: Hi | Foreign) -> Note | None:
        """If *container* starts with optional whitespace then a Note,
        remove and return that Note (mutating the container's content).
        Returns None if the container doesn't start with a Note.
        """
        children = container.content
        # Skip leading whitespace-only strings.
        k = 0
        while k < len(children):
            child = children[k]
            if isinstance(child, str) and not child.strip():
                k += 1
            else:
                break
        if k < len(children) and isinstance(children[k], Note):
            note = children[k]
            assert isinstance(note, Note)
            # Remove leading whitespace + the Note from the container.
            container.content = children[k + 1:]
            return note
        return None

    def _flush_verse(after_idx: int) -> int:
        """Flush the verse buffer and return how many nodes were consumed.

        When the verse group ends, we look ahead from *after_idx* for
        trailing whitespace-only strings and Note nodes.  Any such notes
        are rendered inside the last verse line so the superscript
        appears inline with the verse text rather than on a new line
        after the display:block verse-group span.

        Also handles the case where a Note is the first child of a
        container node (Hi/Foreign) that follows the verse — the Note
        is extracted from the container and absorbed into the verse
        line, while the container keeps its remaining content.

        Returns the number of additional nodes consumed (so the caller
        can skip them).
        """
        if not verse_buf:
            return 0

        # Collect trailing notes (and interleaved whitespace) that
        # should be absorbed into the last verse line.
        trailing: list[str | Note] = []
        consumed = 0
        j = after_idx
        while j < len(nodes):
            n = nodes[j]
            if _is_ws(n):
                assert isinstance(n, str)
                trailing.append(n)
                j += 1
                consumed += 1
            elif isinstance(n, Note):
                trailing.append(n)
                j += 1
                consumed += 1
                # Stop after absorbing the note (and any preceding
                # whitespace).  Further notes would need their own
                # whitespace+note pair.
                break
            elif isinstance(n, (Hi, Foreign)):
                # Check if the container starts with a Note — if so,
                # extract it for the verse line without consuming the
                # container itself.
                note = _extract_leading_note(n)
                if note is not None:
                    trailing.append(note)
                    break
                # Container doesn't start with a Note; stop.
                trailing = [t for t in trailing if isinstance(t, Note)]
                consumed = len(trailing)
                break
            else:
                # Next real node is not a Note; don't consume the
                # whitespace we tentatively collected.
                trailing = [t for t in trailing if isinstance(t, Note)]
                consumed = len(trailing)
                break

        parts.append('<span class="verse-group">')
        for vi, vl in enumerate(verse_buf):
            inner = _render_inline(vl.content, ctx)
            cls = "verse-line verse-indent" if vl.indent else "verse-line"
            if vi == len(verse_buf) - 1 and trailing:
                # Render absorbed trailing notes inside the last line.
                suffix = _render_inline(list(trailing), ctx)
                parts.append(f'<span class="{cls}">{inner}{suffix}</span>')
            else:
                parts.append(f'<span class="{cls}">{inner}</span>')
        parts.append("</span>")
        verse_buf.clear()
        return consumed

    nodes = para.content
    idx = 0
    while idx < len(nodes):
        node = nodes[idx]
        if isinstance(node, VerseLine):
            verse_buf.append(node)
            idx += 1
        elif verse_buf and _is_ws(node):
            # Whitespace between verse lines: skip it (the verse-group
            # display:block handles spacing).  But only if the next
            # non-whitespace node is also a VerseLine or end-of-content.
            rest = nodes[idx + 1:]
            next_real = next((n for n in rest if not _is_ws(n)), None)
            if next_real is None or isinstance(next_real, VerseLine):
                idx += 1
                continue
            # Next real node is not verse; flush and emit the space.
            skip = _flush_verse(idx)
            idx += skip
            if not skip:
                parts.append(_render_inline([node], ctx))
                idx += 1
        else:
            skip = _flush_verse(idx)
            idx += skip
            if idx < len(nodes):
                rendered = _render_inline([nodes[idx]], ctx)
                # If the rendered node ends with a noteref (e.g. a
                # bare Note, or Hi/Foreign containing a trailing
                # Note), pull punctuation from the next node in the
                # paragraph — the one-at-a-time rendering can't do
                # this lookahead itself.
                if _ends_with_noteref(rendered.rstrip()):
                    pulled = _pull_punct(nodes, idx)
                    if pulled:
                        r = rendered.rstrip()
                        pos = r.rfind("<a ")
                        rendered = r[:pos] + escape(pulled) + r[pos:]
                parts.append(rendered)
                idx += 1

    _flush_verse(len(nodes))
    parts.append("</p>\n")
    return "".join(parts)


def _render_page_break(pb: PageBreak) -> str:
    """Render a block-level page break as a standalone marker."""
    display = pb.display_n
    return (
        f'<p class="pb-block">'
        f'<span class="pb" title="Migne col. {escape(display)}">'
        f"{escape(display)}</span></p>\n"
    )


_LEADING_NUMBER_RE = re.compile(r"^(\d+)\.\s+")


def _render_verse_block(
    vb: VerseBlock,
    ctx: _RenderContext,
    trailing_notes: InlineContent | None = None,
) -> str:
    """Render a VerseBlock (<lg>) as a block quotation of verse lines.

    If the first verse line begins with a paragraph number (e.g.
    ``3. Arcanum...``), the number is extracted and rendered as a
    section-number paragraph before the verse block.  This handles
    Migne paragraph numbers that were embedded in the TEI verse text.

    *trailing_notes*, if given, are inline nodes (whitespace and/or
    Note instances) to render inside the last verse line.  This is
    used when the next block-level element starts with a note that
    logically belongs to the final line of this verse.
    """
    parts: list[str] = []

    lines = list(vb.lines)

    # Check for a leading paragraph number in the first line.
    extracted_number = ""
    if lines and lines[0].content:
        first_node = lines[0].content[0]
        if isinstance(first_node, str):
            m = _LEADING_NUMBER_RE.match(first_node)
            if m:
                extracted_number = m.group(1) + "."
                remainder = first_node[m.end():]
                # Replace the first node with the remainder.
                new_content = list(lines[0].content)
                new_content[0] = remainder
                lines[0] = VerseLine(
                    content=new_content, indent=lines[0].indent,
                )

    if extracted_number:
        parts.append(f'<h3>{escape(extracted_number.replace("--", "\u2014"))}</h3>\n')

    parts.append('<blockquote class="verse">\n')
    for li, line in enumerate(lines):
        inner = _render_inline(line.content, ctx)
        cls = "verse-line verse-indent" if line.indent else "verse-line"
        if li == len(lines) - 1 and trailing_notes:
            suffix = _render_inline(trailing_notes, ctx)
            parts.append(f'<p class="{cls}">{inner}{suffix}</p>\n')
        else:
            parts.append(f'<p class="{cls}">{inner}</p>\n')
    parts.append("</blockquote>\n")
    return "".join(parts)


# -- Section rendering -------------------------------------------------------

def _render_section(section: Section, ctx: _RenderContext) -> str:
    """Render a Section (<div3>) to HTML."""
    parts: list[str] = []
    parts.append("<section>\n")

    if section.number:
        parts.append(f'<h3>{escape(section.number.replace("--", "\u2014"))}</h3>\n')

    blocks = section.blocks
    bi = 0
    # Track whether the previous block was verse (block-level or a
    # paragraph ending with inline verse), so we can suppress the
    # text-indent on the next paragraph.
    prev_was_verse = False
    while bi < len(blocks):
        block = blocks[bi]
        if isinstance(block, Paragraph):
            parts.append(
                _render_paragraph(block, ctx, no_indent=prev_was_verse)
            )
            prev_was_verse = _para_ends_with_verse(block)
        elif isinstance(block, PageBreak):
            parts.append(_render_page_break(block))
            # A page-break marker doesn't reset the verse flag — the
            # continuation paragraph still logically follows the verse.
        elif isinstance(block, VerseBlock):
            # Look ahead: if the next block is a Paragraph starting
            # with optional whitespace + Note, absorb that note into
            # the last verse line so the superscript is inline.
            trailing: InlineContent = []
            next_para_trimmed = False
            if bi + 1 < len(blocks):
                nb = blocks[bi + 1]
                if isinstance(nb, Paragraph):
                    content = nb.content
                    ci = 0
                    while ci < len(content):
                        node_ci = content[ci]
                        if not isinstance(node_ci, str) or node_ci.strip():
                            break
                        ci += 1
                    if ci < len(content) and isinstance(content[ci], Note):
                        # Absorb whitespace + note.
                        trailing = list(content[: ci + 1])
                        remainder = content[ci + 1 :]
                        if remainder:
                            nb.content = remainder
                        else:
                            # The paragraph was only whitespace + note;
                            # skip it entirely.
                            bi += 1
                        next_para_trimmed = True
            parts.append(
                _render_verse_block(block, ctx, trailing or None)
            )
            prev_was_verse = True
            if next_para_trimmed:
                pass  # trailing already handled; fall through
        bi += 1

    parts.append("</section>\n")
    return "".join(parts)


# -- Footnotes rendering -----------------------------------------------------

def _strip_parens(text: str) -> str:
    """Strip surrounding parentheses from note text, if present."""
    s = text.strip()
    if s.startswith("(") and s.endswith(")"):
        return s[1:-1].strip()
    return s


def _render_notes(ctx: _RenderContext) -> str:
    """Render the collected footnotes as an endnotes section."""
    if not ctx.notes:
        return ""

    parts: list[str] = []
    parts.append('<hr class="notes-sep"/>\n')
    parts.append('<section epub:type="endnotes" role="doc-endnotes">\n')
    for num, (note_id, note_text) in enumerate(ctx.notes, 1):
        ref_id = f"ref-{note_id}"
        display_text = _strip_parens(note_text)
        parts.append(
            f'<aside epub:type="footnote" id="{note_id}"'
            f' role="doc-footnote">\n'
            f'<p><a href="#{ref_id}">'
            f"{num}. {escape(display_text)}</a></p>\n"
            f"</aside>\n"
        )
    parts.append("</section>\n")
    return "".join(parts)


# -- Chapter rendering -------------------------------------------------------

def _heading_plain_text(nodes: InlineContent) -> str:
    """Extract plain text from inline content, for use in <title>."""
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, Hi):
            parts.append(_heading_plain_text(node.content))
        elif isinstance(node, Foreign):
            parts.append(_heading_plain_text(node.content))
        elif isinstance(node, VerseLine):
            parts.append(_heading_plain_text(node.content))
        elif isinstance(node, Note):
            pass
        elif isinstance(node, PageBreak):
            pass
    return "".join(parts).strip()


def render_group_title(heading: InlineContent, page_id: str) -> str:
    """Render a ChapterGroup heading as a standalone title-page XHTML document.

    This produces a minimal page with just the group heading, used as
    a divider page between groups (e.g. books, sermons) in the EPUB
    spine.

    Args:
        heading: The group heading inline content.
        page_id: A short identifier for this page (e.g. "grp01").

    Returns:
        A complete XHTML document as a string.
    """
    ctx = _RenderContext(page_id)
    plain_title = _heading_plain_text(heading)
    heading_html = _render_inline(heading, ctx)
    heading_html = _fix_heading_dash(heading_html)

    parts: list[str] = []
    parts.append(XHTML_PREAMBLE.format(title=escape(plain_title)))
    parts.append('<div class="group-title">\n')
    parts.append(f"<h1>{heading_html}</h1>\n")
    parts.append("</div>\n")
    parts.append(XHTML_POSTAMBLE)
    return "".join(parts)


# -- Cover rendering ---------------------------------------------------------

# House colours (RGB tuples for Pillow).
_COV_BG = (43, 47, 57)
_COV_RULE = (143, 55, 51)
_COV_TEXT = (242, 236, 226)
_COV_SUBTLE = (174, 170, 160)

# Standard EPUB cover dimensions (portrait).
_COV_W = 600
_COV_H = 900

# Font paths (URW P052, a Palatino clone).
_FONT_DIR = _Path("/usr/share/fonts/opentype/urw-base35")
_FONT_ROMAN = str(_FONT_DIR / "P052-Roman.otf")
_FONT_BOLD = str(_FONT_DIR / "P052-Bold.otf")
_FONT_ITALIC = str(_FONT_DIR / "P052-Italic.otf")


def _wrap_text_px(
    text: str, font: ImageFont.FreeTypeFont, max_width: int,
) -> list[str]:
    """Word-wrap *text* to fit within *max_width* pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _draw_centred_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    y_start: int,
    leading: int,
    fill: tuple[int, int, int],
    cx: int,
) -> int:
    """Draw centred text lines and return the y after the last line."""
    y = y_start
    for line in lines:
        bbox = font.getbbox(line)
        w = bbox[2] - bbox[0]
        draw.text((cx - w // 2, y), line, font=font, fill=fill)
        y += leading
    return y


def render_cover_png(work: Work) -> bytes:
    """Generate a PNG cover image for the work.

    Returns the PNG file content as bytes.
    """
    img = Image.new("RGB", (_COV_W, _COV_H), _COV_BG)
    draw = ImageDraw.Draw(img)

    cx = _COV_W // 2
    margin = 40
    text_width = _COV_W - 2 * margin

    # Fonts — sized for thumbnail legibility.
    title_font = ImageFont.truetype(_FONT_BOLD, 60)
    author_font = ImageFont.truetype(_FONT_ROMAN, 35)
    series_font = ImageFont.truetype(_FONT_ITALIC, 24)
    imprint_font = ImageFont.truetype(_FONT_ROMAN, 20)

    # Decorative border.
    draw.rectangle(
        [18, 18, _COV_W - 19, _COV_H - 19],
        outline=_COV_RULE, width=4,
    )

    # -- Title (upper area) --
    title_lines = _wrap_text_px(work.title, title_font, text_width)
    title_leading = 72
    title_block_h = len(title_lines) * title_leading
    title_y = 220 - title_block_h // 2
    y = _draw_centred_lines(
        draw, title_lines, title_font, title_y, title_leading,
        _COV_TEXT, cx,
    )

    # -- Author --
    author_lines = _wrap_text_px(work.author, author_font, text_width)
    author_y = y + 50
    y = _draw_centred_lines(
        draw, author_lines, author_font, author_y, 40,
        _COV_TEXT, cx,
    )

    # -- Decorative rule --
    rule_y = y + 35
    draw.line(
        [(cx - 80, rule_y), (cx + 80, rule_y)],
        fill=_COV_RULE, width=3,
    )

    # -- Series info --
    series_lines: list[str] = []
    if work.series:
        series_lines.extend(_wrap_text_px(work.series, series_font, text_width))
    if work.editor:
        series_lines.extend(
            _wrap_text_px(work.editor, series_font, text_width)
        )

    if series_lines:
        _draw_centred_lines(
            draw, series_lines, series_font, rule_y + 35, 30,
            _COV_SUBTLE, cx,
        )

    # -- Imprint at bottom --
    imprint = "CASSIODORUS"
    bbox = imprint_font.getbbox(imprint)
    iw = bbox[2] - bbox[0]
    draw.text(
        (cx - iw // 2, _COV_H - 58), imprint,
        font=imprint_font, fill=_COV_SUBTLE,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# -- Front matter rendering --------------------------------------------------

def render_title_page(work: Work) -> str:
    """Render the work's title page as an XHTML document.

    Displays the title, author, series, editor, and original
    publication details.
    """
    parts: list[str] = []
    parts.append(XHTML_PREAMBLE.format(title=escape(work.title)))
    parts.append('<div class="title-page">\n')

    parts.append(f'<p class="tp-title">{escape(work.title)}</p>\n')
    if work.author:
        parts.append(f'<p class="tp-author">{escape(work.author)}</p>\n')

    if work.series:
        parts.append('<div class="tp-series">\n')
        parts.append(f"<p>{escape(work.series)}</p>\n")
        if work.editor:
            parts.append(
                f"<p>{escape(work.editor)}</p>\n"
            )
        # Publication line: "Publisher, Place, Date"
        pub_parts = [
            p for p in [work.publisher, work.pub_place, work.date] if p
        ]
        if pub_parts:
            parts.append(f"<p>{escape(', '.join(pub_parts))}</p>\n")
        parts.append("</div>\n")

    parts.append("</div>\n")
    parts.append(XHTML_POSTAMBLE)
    return "".join(parts)


def render_colophon(work: Work, *, version_date: str = "") -> str:
    """Render the colophon (credits) page as an XHTML document.

    Acknowledges the source (Corpus Corporum) and the EPUB conversion
    tool (Cassiodorus).  If *version_date* is given (a ``YYYY-MM-DD``
    string), it is shown as the edition date.

    The version_date should be **omitted** in probe builds (used for
    hash-based change detection) and **included** in final builds
    (written to disk for publication).  See ``siteman/doc/versioning.md``.
    """
    parts: list[str] = []
    parts.append(XHTML_PREAMBLE.format(title="Colophon"))
    parts.append('<div class="colophon">\n')

    parts.append("<p>Source text from</p>\n")
    parts.append(
        '<p class="co-source">Corpus Corporum</p>\n'
    )
    parts.append(
        '<p>University of Zurich</p>\n'
    )
    parts.append(
        '<p><em>www.mlat.uzh.ch</em></p>\n'
    )

    parts.append('<p class="co-sep">\u2014</p>\n')

    parts.append("<p>EPUB produced by</p>\n")
    parts.append(
        '<p class="co-source">Cassiodorus</p>\n'
    )
    parts.append(
        '<p><em>cassiodorus.ink</em></p>\n'
    )

    if version_date:
        parts.append('<p class="co-sep">\u2014</p>\n')
        parts.append(f'<p class="co-date">Edition: {escape(version_date)}</p>\n')

    parts.append("</div>\n")
    parts.append(XHTML_POSTAMBLE)
    return "".join(parts)


def render_chapter(
    chapter: Chapter, chapter_id: str
) -> str:
    """Render a Chapter to a complete XHTML document string.

    Args:
        chapter: The Chapter model to render.
        chapter_id: A short identifier for this chapter (e.g. "ch01"),
            used for note id generation.

    Returns:
        A complete XHTML document as a string.
    """
    ctx = _RenderContext(chapter_id)

    plain_title = _heading_plain_text(chapter.heading)
    parts: list[str] = []
    parts.append(XHTML_PREAMBLE.format(title=escape(plain_title)))

    parts.append('<section epub:type="chapter" role="doc-chapter">\n')

    # Chapter heading.
    if chapter.heading:
        heading_html = _render_inline(chapter.heading, ctx)
        heading_html = _fix_heading_dash(heading_html)
        parts.append(f"<h2>{heading_html}</h2>\n")

    # Sections.
    for section in chapter.sections:
        parts.append(_render_section(section, ctx))

    parts.append("</section>\n")

    # Footnotes at end of chapter.
    parts.append(_render_notes(ctx))

    parts.append(XHTML_POSTAMBLE)
    return "".join(parts)
