"""Parse a TEI XML file into the intermediate data model.

This module handles Corpus Corporum-style TEI files for the Patrologia
Latina, which use TEI P4-era numbered div elements (<div1>, <div2>,
<div3>) and contain body text with inline <hi>, <note>, and <pb/>
elements.
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

    # Text before first child element.
    head_text = _norm_ws(elem.text)
    if head_text:
        result.append(head_text)

    for child in elem:
        tag = etree.QName(child.tag).localname

        if tag == "hi":
            result.append(Hi(content=_parse_inline(child)))
        elif tag == "note":
            note_text = _norm_ws(_text_of(child))
            if note_text:
                result.append(Note(text=note_text))
        elif tag == "pb":
            n = child.get("n", "")
            result.append(PageBreak(n=n))
        elif tag == "l":
            indent = child.get("rend", "") == "indent"
            result.append(VerseLine(content=_parse_inline(child), indent=indent))
        elif tag == "lg":
            # <lg> inside a <p>: parse each <l> child as inline verse.
            for lchild in child:
                ltag = etree.QName(lchild.tag).localname
                if ltag == "l":
                    lg_indent = lchild.get("rend", "") == "indent"
                    result.append(VerseLine(
                        content=_parse_inline(lchild), indent=lg_indent,
                    ))
        elif tag == "foreign":
            lang = child.get(f"{{{XML_NS}}}lang", "")
            result.append(Foreign(content=_parse_inline(child), lang=lang))
        elif tag == "emph":
            inner = _parse_inline(child)
            # Drop digit-only emph (inline edition page refs).
            if not (len(inner) == 1 and isinstance(inner[0], str) and inner[0].strip().isdigit()):
                result.append(Hi(content=inner))
        else:
            # Unknown inline element: flatten its text content.
            inner = _norm_ws(child.text)
            if inner:
                result.append(inner)

        # Tail text after this child element.
        tail = _norm_ws(child.tail)
        if tail:
            result.append(tail)

    return result


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

        if tag == "p":
            _flush_verse()
            blocks.append(Paragraph(content=_parse_inline(child)))
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
        if tag == "p":
            _flush()
            blocks.append(Paragraph(content=_parse_inline(child)))
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
        elif tag == "p":
            _flush_verse_lines()
            initial_blocks.append(Paragraph(content=_parse_inline(child)))
        elif tag == "pb":
            _flush_verse_lines()
            initial_blocks.append(PageBreak(n=child.get("n", "")))

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
        author_elem = title_stmt.find(_ns("author"))
        if author_elem is not None:
            # Author text may include a <date> child; extract just the
            # name portion, and capture the dates separately.
            parts = []
            if author_elem.text:
                parts.append(author_elem.text.strip())
            meta["author"] = _normalise_author(
                " ".join(parts) or _text_of(author_elem)
            )
            date_elem = author_elem.find(_ns("date"))
            if date_elem is not None:
                meta["author_dates"] = _text_of(date_elem)

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

    # Preamble: any <p>/<pb> elements before the first <div2>.
    preamble_blocks: list[Block] = []
    for child in div1:
        tag = etree.QName(child.tag).localname
        if tag == "div2":
            break
        if tag == "p":
            preamble_blocks.append(Paragraph(content=_parse_inline(child)))
        elif tag == "pb":
            preamble_blocks.append(PageBreak(n=child.get("n", "")))

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
    # Preamble from <front>, if present.
    front = root.find(f".//{_ns('front')}")
    if front is not None:
        front_blocks: list[Block] = []
        for p in front.iter(_ns("p")):
            front_blocks.append(Paragraph(content=_parse_inline(p)))
        if front_blocks:
            work.preamble = Chapter(
                heading=[work.title] if work.title else ["Preamble"],
                sections=[Section(number="", blocks=front_blocks)],
            )

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
    become Chapters.  A <front> section, if present, becomes the
    preamble.
    """
    # Preamble from <front>.
    front = root.find(f".//{_ns('front')}")
    if front is not None:
        front_blocks: list[Block] = []
        for p in front.iter(_ns("p")):
            front_blocks.append(Paragraph(content=_parse_inline(p)))
        if front_blocks:
            work.preamble = Chapter(
                heading=[work.title] if work.title else ["Preamble"],
                sections=[Section(number="", blocks=front_blocks)],
            )

    # Parse the body's <div1> children, preserving group hierarchy.
    work.chapters = _parse_div1_items(body)
