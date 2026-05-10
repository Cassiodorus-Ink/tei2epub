"""Parse a TEI XML file into the intermediate data model.

This module handles Corpus Corporum-style TEI files for the Patrologia
Latina, which use TEI P4-era numbered div elements (<div1>, <div2>,
<div3>) and contain body text with inline <hi>, <note>, and <pb/>
elements.

================================================================
!! WHEN EXTENDING THIS PARSER, UPDATE THE CONFORMANCE CHECKER !!
================================================================

If you teach the parser a new tag, or extend an existing tag to be
handled in a new parent context, you MUST also update the parser's
allow-lists in ``tei2epub/coverage.py``.  Those allow-lists are the
single source of truth used by the CV-1 conformance check
(``tei2epub/checks/coverage.py``); they tell the checker which
tag/parent combinations the parser actually handles.

Forgetting this has two failure modes:

  * If the parser starts handling a tag but coverage.py is not
    updated, the corpus checker will keep flagging legitimate uses
    as CV-1 findings — the report fills with false positives and
    real defects get drowned out.

  * If the parser DROPS support for a tag (or stops handling it in
    some context) and coverage.py is not updated, the checker will
    silently approve files that now produce broken output.

If you add a fundamentally new *kind* of defect that the existing
TL-/ST-/CV- checks won't catch, also add a new CheckSpec to
``tei2epub/checks/catalog.py`` and a check function in the
corresponding ``tei2epub/checks/`` module.
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from tei2epub.model import (
    Block,
    Chapter,
    ChapterGroup,
    Foreign,
    Hi,
    InlineContent,
    Note,
    PageBreak,
    Paragraph,
    Section,
    TeiList,
    TeiListItem,
    TopLevelItem,
    VerseBlock,
    VerseLine,
    Work,
)

TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _ns(tag: str) -> str:
    """Qualify a tag name with the TEI namespace."""
    return f"{{{TEI_NS}}}{tag}"


# -- Whitespace normalisation ------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _norm_ws(text: str | None) -> str:
    """Collapse runs of whitespace to a single space, and strip."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text)


# -- Author normalisation ----------------------------------------------------

_AUCTOR_RE = re.compile(r"Auctor incertus \((.+?)\)")


def _normalise_author(author: str) -> str:
    """Collapse repeated 'Auctor incertus' attributions into one.

    Transforms e.g.
        'Auctor incertus (Foo?); Auctor incertus (Bar?)'
    into
        'Auctor incertus (Foo? / Bar?)'

    Bare 'Auctor incertus' entries with no parenthesised attribution
    are dropped before collapsing (e.g. 'Auctor incertus (Foo?);
    Auctor incertus' becomes 'Auctor incertus (Foo?)').

    If not all remaining parts are 'Auctor incertus (…)' entries,
    the string is returned unchanged.
    """
    parts = [p.strip() for p in author.split(";")]
    # Drop bare "Auctor incertus" entries (no parenthesised name).
    parts = [p for p in parts if p != "Auctor incertus"]
    if len(parts) <= 1:
        return parts[0] if parts else author
    names: list[str] = []
    for part in parts:
        m = _AUCTOR_RE.fullmatch(part)
        if m is None:
            return author
        names.append(m.group(1))
    return "Auctor incertus (" + " / ".join(names) + ")"


# -- Inline content parsing --------------------------------------------------

def _inline_from_child(child: etree._Element) -> InlineContent:
    """Convert a single inline child element into InlineContent nodes.

    Does not include the child's tail text; callers append that
    separately.  Returns an empty list for elements that are dropped
    (e.g. ``<B>`` edition refs, digit-only ``<emph>``).
    """
    tag = etree.QName(child.tag).localname

    if tag in ("hi", "b", "i"):
        # <b> and <i> are HTML bold/italic that leak into some corpus files;
        # both are treated as <hi> (rendered as <em>/italic).
        return [Hi(content=_parse_inline(child))]
    if tag == "note":
        note_text = _norm_ws(_text_of(child))
        return [Note(text=note_text)] if note_text else []
    if tag == "pb":
        return [PageBreak(n=child.get("n", ""))]
    if tag == "l":
        indent = child.get("rend", "") == "indent"
        return [VerseLine(content=_parse_inline(child), indent=indent)]
    if tag == "lg":
        # <lg> inside a <p>: parse each <l> child as inline verse.
        result: InlineContent = []
        for lchild in child:
            ltag = etree.QName(lchild.tag).localname
            if ltag == "l":
                lg_indent = lchild.get("rend", "") == "indent"
                result.append(VerseLine(
                    content=_parse_inline(lchild), indent=lg_indent,
                ))
        return result
    if tag == "foreign":
        lang = child.get(f"{{{XML_NS}}}lang", "")
        return [Foreign(content=_parse_inline(child), lang=lang)]
    if tag == "emph":
        inner = _parse_inline(child)
        # Drop digit-only emph (inline edition page refs).
        if len(inner) == 1 and isinstance(inner[0], str) and inner[0].strip().isdigit():
            return []
        return [Hi(content=inner)]
    if tag == "B":
        # Uppercase <B> encodes edition page references (like digit-only
        # <emph>); drop entirely.
        return []
    if tag == "LACUNA":
        # Custom corpus element marking missing/illegible manuscript text.
        # Empty → render as "[...]"; with text → render text in brackets.
        inner_text = _norm_ws(_text_of(child)).strip()
        return [f"[{inner_text}]" if inner_text else "[...]"]
    # Unknown inline element: flatten its text content.
    inner_text = _norm_ws(child.text)
    return [inner_text] if inner_text else []


def _parse_inline(elem: etree._Element) -> InlineContent:
    """Parse the mixed content of an element into a flat InlineContent list.

    Handles the lxml mixed-content model where an element has:
      - elem.text: text before the first child
      - child elements, each with:
        - the element itself (recursively parsed)
        - child.tail: text after the child, before the next sibling

    Whitespace is normalised but not stripped, so that joining inline
    nodes produces correctly spaced text.
    """
    result: InlineContent = []

    head_text = _norm_ws(elem.text)
    if head_text:
        result.append(head_text)

    for child in elem:
        result.extend(_inline_from_child(child))
        tail = _norm_ws(child.tail)
        if tail:
            result.append(tail)

    return result


# -- List parsing ------------------------------------------------------------

def _parse_list(list_elem: etree._Element) -> TeiList:
    """Parse a TEI <list> element into a TeiList block.

    Recognises ``<head>`` (an optional caption) and ``<item>`` children;
    other children are ignored.
    """
    head: InlineContent = []
    items: list[TeiListItem] = []
    for child in list_elem:
        tag = etree.QName(child.tag).localname
        if tag == "head":
            head = _parse_inline(child)
        elif tag == "item":
            items.append(TeiListItem(content=_parse_inline(child)))
    return TeiList(items=items, head=head)


def _parse_p_to_blocks(p: etree._Element) -> list[Block]:
    """Parse a <p> element into a sequence of blocks.

    Most paragraphs return ``[Paragraph(...)]``.  When a ``<p>`` contains
    one or more ``<list>`` children, the paragraph is split so each
    list becomes a block-level sibling between two prose paragraphs —
    HTML ``<ul>`` cannot legally nest inside ``<p>``.
    """
    if not any(etree.QName(c.tag).localname == "list" for c in p):
        return [Paragraph(content=_parse_inline(p))]

    blocks: list[Block] = []
    current: InlineContent = []

    def _flush() -> None:
        if not current:
            return
        if all(isinstance(n, str) and not n.strip() for n in current):
            current.clear()
            return
        blocks.append(Paragraph(content=list(current)))
        current.clear()

    head_text = _norm_ws(p.text)
    if head_text:
        current.append(head_text)

    for child in p:
        tag = etree.QName(child.tag).localname
        if tag == "list":
            _flush()
            blocks.append(_parse_list(child))
        else:
            current.extend(_inline_from_child(child))
        tail = _norm_ws(child.tail)
        if tail:
            current.append(tail)

    _flush()
    return blocks


# -- Block-level parsing -----------------------------------------------------

def _parse_blocks(container: etree._Element) -> list[Block]:
    """Parse block-level children of a container element.

    A container (e.g. a <div3>) may hold <p>, <pb/>, <l>, and <lg>
    elements at the block level.  <pb/> elements that appear inside
    <p> are handled by _parse_inline; those directly under the
    container are separate block-level page breaks.

    Consecutive <l> elements are collected into a single VerseBlock.
    """
    blocks: list[Block] = []
    pending_lines: list[VerseLine] = []

    def _flush_verse() -> None:
        if pending_lines:
            blocks.append(VerseBlock(lines=list(pending_lines)))
            pending_lines.clear()

    for child in container:
        tag = etree.QName(child.tag).localname

        if tag in ("p", "P"):
            # <P> is an uppercase encoding error used in two corpus files;
            # treat identically to <p>.
            _flush_verse()
            blocks.extend(_parse_p_to_blocks(child))
        elif tag == "pb":
            _flush_verse()
            blocks.append(PageBreak(n=child.get("n", "")))
        elif tag == "l":
            indent = child.get("rend", "") == "indent"
            pending_lines.append(
                VerseLine(content=_parse_inline(child), indent=indent)
            )
        elif tag == "lg":
            _flush_verse()
            lg_lines: list[VerseLine] = []
            for lchild in child:
                ltag = etree.QName(lchild.tag).localname
                if ltag == "l":
                    lg_indent = lchild.get("rend", "") == "indent"
                    lg_lines.append(VerseLine(
                        content=_parse_inline(lchild), indent=lg_indent,
                    ))
            if lg_lines:
                blocks.append(VerseBlock(lines=lg_lines))
        elif tag == "list":
            _flush_verse()
            blocks.append(_parse_list(child))
        # <head> is handled separately by callers; skip it here.

    _flush_verse()
    return blocks


def _parse_blocks_before(
    container: etree._Element, stop_tag: str
) -> list[Block]:
    """Parse block-level children up to (but not including) the first
    child with local name *stop_tag*.

    This is used to collect <l>, <p>, <pb>, <lg> content that precedes
    the first <div3> (or similar) inside a container.
    """
    blocks: list[Block] = []
    pending_lines: list[VerseLine] = []

    def _flush() -> None:
        if pending_lines:
            blocks.append(VerseBlock(lines=list(pending_lines)))
            pending_lines.clear()

    for child in container:
        tag = etree.QName(child.tag).localname
        if tag == stop_tag:
            break
        if tag in ("p", "P"):
            _flush()
            blocks.extend(_parse_p_to_blocks(child))
        elif tag == "pb":
            _flush()
            blocks.append(PageBreak(n=child.get("n", "")))
        elif tag == "l":
            indent = child.get("rend", "") == "indent"
            pending_lines.append(
                VerseLine(content=_parse_inline(child), indent=indent)
            )
        elif tag == "lg":
            _flush()
            lg_lines: list[VerseLine] = []
            for lchild in child:
                ltag = etree.QName(lchild.tag).localname
                if ltag == "l":
                    lg_indent = lchild.get("rend", "") == "indent"
                    lg_lines.append(VerseLine(
                        content=_parse_inline(lchild), indent=lg_indent,
                    ))
            if lg_lines:
                blocks.append(VerseBlock(lines=lg_lines))
        elif tag == "list":
            _flush()
            blocks.append(_parse_list(child))

    _flush()
    return blocks


# -- Section parsing (div3) --------------------------------------------------

def _parse_section(div3: etree._Element) -> Section:
    """Parse a <div3> element into a Section."""
    head = div3.find(_ns("head"))
    number = ""
    if head is not None:
        number = _norm_ws(head.text).strip()

    return Section(number=number, blocks=_parse_blocks(div3))


# -- Chapter parsing ---------------------------------------------------------

def _parse_chapter_from_div(div: etree._Element) -> Chapter:
    """Parse a div element into a Chapter.

    This handles both <div2> elements (in simple works like De vera
    religione) and <div1> elements that represent individual epistles
    (in collections like Epistolae).

    Within the div, <div3> children become Sections.  If the div
    contains <div2> children (sub-chapters), each <div2> is flattened
    into Sections with the <div2> heading prepended.  Direct <p> and
    <pb> children (before any sub-div) are collected into an initial
    section.
    """
    head = div.find(_ns("head"))
    heading: InlineContent = []
    if head is not None:
        heading = _parse_inline(head)

    sections: list[Section] = []

    # Collect any direct <p>/<pb>/<div3>/<l>/<lg> children before the
    # first <div2> sub-chapter, and any <div3> children at this level.
    initial_blocks: list[Block] = []
    pending_lines: list[VerseLine] = []

    def _flush_verse_lines() -> None:
        """Flush accumulated verse lines into a VerseBlock."""
        if pending_lines:
            initial_blocks.append(VerseBlock(lines=list(pending_lines)))
            pending_lines.clear()

    for child in div:
        tag = etree.QName(child.tag).localname
        if tag == "div2":
            break
        if tag == "div3":
            # Flush any accumulated content first.
            _flush_verse_lines()
            if initial_blocks:
                sections.append(Section(number="", blocks=initial_blocks))
                initial_blocks = []
            sections.append(_parse_section(child))
        elif tag == "l":
            indent = child.get("rend", "") == "indent"
            pending_lines.append(
                VerseLine(content=_parse_inline(child), indent=indent)
            )
        elif tag == "lg":
            _flush_verse_lines()
            lines: list[VerseLine] = []
            for lchild in child:
                ltag = etree.QName(lchild.tag).localname
                if ltag == "l":
                    lg_indent = lchild.get("rend", "") == "indent"
                    lines.append(VerseLine(
                        content=_parse_inline(lchild), indent=lg_indent,
                    ))
            if lines:
                initial_blocks.append(VerseBlock(lines=lines))
        elif tag in ("p", "P"):
            _flush_verse_lines()
            initial_blocks.extend(_parse_p_to_blocks(child))
        elif tag == "pb":
            _flush_verse_lines()
            initial_blocks.append(PageBreak(n=child.get("n", "")))
        elif tag == "list":
            _flush_verse_lines()
            initial_blocks.append(_parse_list(child))

    _flush_verse_lines()
    if initial_blocks:
        sections.append(Section(number="", blocks=initial_blocks))

    # Now handle any <div2> sub-chapters.  Each <div2> contributes
    # a sub-heading section followed by its <div3> sections.
    for div2 in div.findall(_ns("div2")):
        div2_head = div2.find(_ns("head"))
        if div2_head is not None:
            div2_heading_text = _text_of(div2_head)
            if div2_heading_text:
                # Insert the sub-chapter heading as a section with
                # no number but a heading block.
                sections.append(Section(
                    number=div2_heading_text,
                    blocks=[],
                ))
        div3s = div2.findall(_ns("div3"))
        if div3s:
            # Collect block-level content before the first <div3>
            # (e.g. <l> verse lines preceding prose sections).
            pre_blocks = _parse_blocks_before(div2, "div3")
            if pre_blocks:
                sections.append(Section(number="", blocks=pre_blocks))
            for div3 in div3s:
                sections.append(_parse_section(div3))
        else:
            # No <div3> children: parse block-level content directly
            # from the <div2> (handles <p>, <pb>, <l>, <lg>).
            div2_blocks = _parse_blocks(div2)
            if div2_blocks:
                sections.append(Section(number="", blocks=div2_blocks))

    # Handle <div3> children that appear after <div2> elements
    # (unusual but possible in messy TEI).
    seen_div2 = False
    for child in div:
        tag = etree.QName(child.tag).localname
        if tag == "div2":
            seen_div2 = True
        elif tag == "div3" and seen_div2:
            sections.append(_parse_section(child))

    return Chapter(heading=heading, sections=sections)


# -- Metadata extraction -----------------------------------------------------

def _text_of(elem: etree._Element | None) -> str:
    """Get the stripped text content of an element, or ""."""
    if elem is None:
        return ""
    # itertext() gives all descendant text including tails.
    return " ".join("".join(str(t) for t in elem.itertext()).split()).strip()


def _parse_metadata(header: etree._Element) -> dict[str, str]:
    """Extract metadata fields from a <teiHeader>."""
    meta: dict[str, str] = {}

    file_desc = header.find(_ns("fileDesc"))
    if file_desc is None:
        return meta

    title_stmt = file_desc.find(_ns("titleStmt"))
    if title_stmt is not None:
        meta["title"] = _text_of(title_stmt.find(_ns("title")))
        author_elems = title_stmt.findall(_ns("author"))
        if author_elems:
            # Author text may include a <date> child; extract just the
            # name portion for each <author>, and capture the dates
            # separately.  Join multiple authors with "; " so that
            # _normalise_author can collapse multi-author attributions.
            author_parts: list[str] = []
            dates: list[str] = []
            for ae in author_elems:
                name = (ae.text or "").strip()
                if not name:
                    name = _text_of(ae)
                author_parts.append(name)
                date_elem = ae.find(_ns("date"))
                if date_elem is not None:
                    dates.append(_text_of(date_elem))
            meta["author"] = _normalise_author("; ".join(author_parts))
            if dates:
                meta["author_dates"] = dates[0]

    edition_stmt = file_desc.find(_ns("editionStmt"))
    if edition_stmt is not None:
        meta["editor"] = _text_of(edition_stmt.find(_ns("editor")))

    pub_stmt = file_desc.find(_ns("publicationStmt"))
    if pub_stmt is not None:
        meta["publisher"] = _text_of(pub_stmt.find(_ns("publisher")))
        meta["pub_place"] = _text_of(pub_stmt.find(_ns("pubPlace")))
        meta["date"] = _text_of(pub_stmt.find(_ns("date")))

    series_stmt = file_desc.find(_ns("seriesStmt"))
    if series_stmt is not None:
        meta["series"] = _text_of(series_stmt.find(_ns("title")))
        meta["identifier"] = _text_of(series_stmt.find(_ns("idno")))

    source_desc = file_desc.find(_ns("sourceDesc"))
    if source_desc is not None:
        meta["source_desc"] = _text_of(source_desc)

    profile = header.find(_ns("profileDesc"))
    if profile is not None:
        lang_usage = profile.find(_ns("langUsage"))
        if lang_usage is not None:
            lang = lang_usage.find(_ns("language"))
            if lang is not None:
                meta["language"] = lang.get("ident", "la")

    return meta


# -- Top-level parse entry point ---------------------------------------------

def parse(tei_path: str | Path) -> Work:
    """Parse a TEI XML file and return a Work model.

    Args:
        tei_path: Path to the TEI XML file.

    Returns:
        A fully populated Work instance.

    Raises:
        ValueError: If the TEI structure is missing expected elements.
    """
    tree = etree.parse(str(tei_path))
    root = tree.getroot()

    # -- Metadata --
    header = root.find(_ns("teiHeader"))
    meta = _parse_metadata(header) if header is not None else {}

    work = Work(
        title=meta.get("title", ""),
        author=meta.get("author", ""),
        author_dates=meta.get("author_dates", ""),
        editor=meta.get("editor", ""),
        publisher=meta.get("publisher", ""),
        pub_place=meta.get("pub_place", ""),
        date=meta.get("date", ""),
        series=meta.get("series", ""),
        identifier=meta.get("identifier", ""),
        source_desc=meta.get("source_desc", ""),
        language=meta.get("language", "la"),
    )

    # -- Body content --
    body = root.find(f".//{_ns('body')}")
    if body is None:
        raise ValueError("TEI file has no <body> element")

    # Preamble from <front>, if present.  This is done centrally so
    # that all structural paths (simple, flat, multi-book, collection)
    # pick up the front matter.
    front = root.find(f".//{_ns('front')}")
    if front is not None:
        work.preamble = _parse_front_preamble(front, work.title)

    all_div1s = body.findall(_ns("div1"))
    if not all_div1s:
        # Some texts skip div1 and have div2 directly under body.
        # Treat body as if it were a single div1 (simple/flat work).
        all_div2s = body.findall(_ns("div2"))
        if all_div2s:
            _parse_simple_work(work, body)
            return work

        # Some texts have div3 directly under body (no div1/div2).
        all_div3s = body.findall(_ns("div3"))
        if all_div3s:
            _parse_flat_work(work, body)
            return work

        # CC "Admonitio" pattern: a <note> child of body wraps the
        # structural content (div1/div2/div3).  The <note> contains
        # the Migne editor's introductory note followed by the actual
        # divs.  Extract the content and continue parsing normally.
        for note in body:
            if etree.QName(note.tag).localname != "note":
                continue
            inner_div1s = note.findall(_ns("div1"))
            if inner_div1s:
                all_div1s = inner_div1s
                break
            inner_div2s = note.findall(_ns("div2"))
            if inner_div2s:
                _parse_simple_work(work, note)
                return work
            inner_div3s = note.findall(_ns("div3"))
            if inner_div3s:
                _parse_flat_work(work, note)
                return work

        if not all_div1s:
            raise ValueError("TEI file has no <div1> element under <body>")

    div1 = all_div1s[0]

    # Determine the structural pattern:
    #
    # 1. Multiple sibling <div1> under <body>, each with <div2>
    #    children: multi-book work (e.g. Confessiones).  Each <div1>
    #    is a book (ChapterGroup), each <div2> is a chapter.
    #
    # 2. Single <div1> containing nested <div1> children: collection
    #    (e.g. Epistolae).  Grouping <div1>s become ChapterGroups,
    #    leaf <div1>s become chapters.
    #
    # 3. Single <div1> with <div2> children: simple work
    #    (e.g. De vera religione).
    #
    # 4. Single <div1> with only <div3>/<p> content: flat work.

    # Check what kind of children the top-level <div1> elements have.
    any_has_nested_div1 = any(
        d.find(_ns("div1")) is not None for d in all_div1s
    )

    if len(all_div1s) > 1 and any_has_nested_div1:
        # Collection pattern with grouping: sibling <div1> elements
        # that themselves contain <div1> children (e.g. Epistolae
        # with Prima Classis > Epistola I, II, ...).
        _parse_collection(work, root, body)
    elif len(all_div1s) > 1:
        # Multi-book pattern: sibling <div1> elements each with
        # <div2> chapters (e.g. Confessiones).
        _parse_multi_book(work, root, body, all_div1s)
    elif div1.find(_ns("div1")) is not None:
        # Collection pattern: single <div1> containing nested <div1>.
        _parse_collection(work, root, body)
    elif div1.find(_ns("div2")) is not None:
        # Simple work pattern.
        _parse_simple_work(work, div1)
    else:
        # Flat pattern.
        _parse_flat_work(work, div1)

    return work


def _parse_book(
    div1: etree._Element,
) -> tuple[InlineContent, Chapter | None, list[Chapter]]:
    """Parse a single book (a ``<div1>`` with ``<div2>`` children).

    Returns ``(heading, preamble, chapters)`` where *heading* is the
    inline content of the ``<div1>/<head>``, *preamble* is any text
    before the first ``<div2>`` (or ``None``), and *chapters* is the
    list of chapters parsed from ``<div2>`` children.
    """
    head = div1.find(_ns("head"))
    heading: InlineContent = []
    if head is not None:
        heading = _parse_inline(head)

    # Preamble: any <p>/<pb>/<list> elements before the first <div2>.
    preamble_blocks: list[Block] = []
    for child in div1:
        tag = etree.QName(child.tag).localname
        if tag == "div2":
            break
        if tag in ("p", "P"):
            preamble_blocks.extend(_parse_p_to_blocks(child))
        elif tag == "pb":
            preamble_blocks.append(PageBreak(n=child.get("n", "")))
        elif tag == "list":
            preamble_blocks.append(_parse_list(child))

    preamble: Chapter | None = None
    if preamble_blocks:
        preamble = Chapter(
            heading=heading,
            sections=[Section(number="", blocks=preamble_blocks)],
        )

    chapters = [
        _parse_chapter_from_div(div2)
        for div2 in div1.findall(_ns("div2"))
    ]

    return heading, preamble, chapters


def _parse_simple_work(work: Work, div1: etree._Element) -> None:
    """Parse a simple work with <div1>/<div2>/<div3> structure."""
    heading, preamble, chapters = _parse_book(div1)

    # Set work title from the div1 heading if not already set.
    if heading and not work.title:
        div1_head = div1.find(_ns("head"))
        if div1_head is not None:
            work.title = _text_of(div1_head)

    work.preamble = preamble
    work.chapters = chapters


def _parse_flat_work(work: Work, div1: etree._Element) -> None:
    """Parse a work with only <div3> or <p> content under <div1>."""
    work.chapters = [_parse_chapter_from_div(div1)]


def _parse_front_preamble(front: etree._Element, work_title: str) -> Chapter | None:
    """Parse a <front> element into a preamble Chapter.

    The heading is taken from the first <head> found inside <front>
    (whether directly or inside a div child).  Falls back to the work
    title, then to "Preamble" if no heading is found.  Returns None if
    the front contains no paragraph content.
    """
    heading_elem: etree._Element | None = None
    for child in front:
        tag = etree.QName(child.tag).localname
        if tag in ("div1", "div2", "div3", "div"):
            heading_elem = child.find(_ns("head"))
            if heading_elem is not None:
                break
    if heading_elem is None:
        heading_elem = front.find(_ns("head"))
    heading: InlineContent = (
        _parse_inline(heading_elem)
        if heading_elem is not None
        else ([work_title] if work_title else ["Preamble"])
    )

    front_blocks: list[Block] = []
    for p in front.iter(_ns("p")):
        front_blocks.extend(_parse_p_to_blocks(p))
    if not front_blocks:
        return None
    return Chapter(
        heading=heading,
        sections=[Section(number="", blocks=front_blocks)],
    )


def _parse_multi_book(
    work: Work,
    root: etree._Element,
    body: etree._Element,
    div1s: list[etree._Element],
) -> None:
    """Parse a multi-book work (e.g. Confessiones).

    Multiple sibling <div1> elements under <body> each represent a
    book.  Each book becomes a ChapterGroup whose chapters are the
    <div2> children of that <div1>.
    """
    for div1 in div1s:
        heading, preamble, chapters = _parse_book(div1)

        if chapters:
            work.chapters.append(ChapterGroup(
                heading=heading,
                chapters=chapters,
                preamble=preamble,
            ))
        else:
            # A <div1> with no <div2> children: treat it as a
            # standalone chapter (e.g. a preface or appendix).
            work.chapters.append(_parse_chapter_from_div(div1))


def _parse_div1_items(container: etree._Element) -> list[TopLevelItem]:
    """Parse <div1> children of a container, preserving group hierarchy.

    A <div1> that contains further <div1> children is a grouping
    element and becomes a ChapterGroup.  A <div1> without <div1>
    children is a leaf (an individual work/epistle) and becomes a
    Chapter.
    """
    items: list[TopLevelItem] = []
    for child in container:
        tag = etree.QName(child.tag).localname
        if tag != "div1":
            continue

        nested_div1s = child.findall(_ns("div1"))
        if nested_div1s:
            # Grouping div1: extract heading and recurse.
            head = child.find(_ns("head"))
            heading: InlineContent = []
            if head is not None:
                heading = _parse_inline(head)
            group_chapters: list[Chapter] = []
            for nested in nested_div1s:
                sub_nested = nested.findall(_ns("div1"))
                if sub_nested:
                    # Deeper nesting: recurse further and flatten
                    # into the group's chapter list.
                    for item in _parse_div1_items(nested):
                        if isinstance(item, ChapterGroup):
                            # Flatten deeper groups into this group.
                            group_chapters.extend(item.chapters)
                        else:
                            group_chapters.append(item)
                else:
                    group_chapters.append(_parse_chapter_from_div(nested))
            items.append(ChapterGroup(
                heading=heading,
                chapters=group_chapters,
            ))
        else:
            # Leaf div1: individual work/epistle.
            items.append(_parse_chapter_from_div(child))
    return items


def _parse_collection(
    work: Work, root: etree._Element, body: etree._Element
) -> None:
    """Parse a collection (e.g. Epistolae) with nested <div1> elements.

    Grouping <div1> elements become ChapterGroups; leaf <div1> elements
    become Chapters.
    """
    # Parse the body's <div1> children, preserving group hierarchy.
    work.chapters = _parse_div1_items(body)
