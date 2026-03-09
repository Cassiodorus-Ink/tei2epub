"""Intermediate data model for TEI to EPUB conversion.

The model represents the logical structure of a work extracted from
TEI XML, independent of both the source XML encoding and the target
EPUB format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias, Union


# -- Inline content nodes ----------------------------------------------------

# InlineContent is a list of inline nodes: plain str, Hi, Note, or
# PageBreak.  We use a type alias rather than a wrapper class so that
# building up content is just list appending.

InlineNode: TypeAlias = Union[str, "Hi", "Note", "PageBreak", "Foreign", "VerseLine"]
InlineContent: TypeAlias = list[InlineNode]


@dataclass
class Hi:
    """Highlighted (italic) inline span, corresponding to TEI <hi>."""
    content: InlineContent = field(default_factory=list)


@dataclass
class Note:
    """An inline citation reference, corresponding to TEI <note>.

    In the Corpus Corporum PL TEI files these are short Scripture or
    work references such as "(Joan. I, 1-3)".
    """
    text: str = ""


@dataclass
class PageBreak:
    """A Migne column/page marker, corresponding to TEI <pb n="..."/>.

    The `n` attribute is the Migne column number (e.g. "0122").  We
    strip the leading zero for display, but preserve the original
    value here.
    """
    n: str = ""

    @property
    def display_n(self) -> str:
        """Column number for display, without leading zeros."""
        return self.n.lstrip("0") or "0"


@dataclass
class Foreign:
    """An inline span of foreign-language text, corresponding to TEI <foreign>.

    In the Corpus Corporum PL files these are typically Greek words or
    phrases embedded in Latin text, marked with xml:lang="greek".
    """
    content: InlineContent = field(default_factory=list)
    lang: str = ""


@dataclass
class VerseLine:
    """A single verse line, corresponding to TEI <l>.

    When appearing inside a <p>, this represents an inline quotation of
    a verse line that should be rendered with a line break.  When inside
    a VerseBlock (<lg>), it is one line in a block of verse.

    The ``indent`` flag marks lines that have rend="indent" in the TEI,
    typically used for pentameter lines in elegiac couplets.
    """
    content: InlineContent = field(default_factory=list)
    indent: bool = False


# -- Block-level content nodes ----------------------------------------------

@dataclass
class Paragraph:
    """A paragraph of mixed inline content, corresponding to TEI <p>."""
    content: InlineContent = field(default_factory=list)


@dataclass
class VerseBlock:
    """A block of verse lines, corresponding to TEI <lg>.

    Contains a sequence of verse lines that should be rendered as a
    distinct block (indented, with each line on its own row).
    """
    lines: list[VerseLine] = field(default_factory=list)


Block: TypeAlias = Union[Paragraph, PageBreak, VerseBlock]


# -- Structural nodes --------------------------------------------------------

@dataclass
class Section:
    """A numbered section within a chapter, corresponding to TEI <div3>.

    Each section has a short heading (typically just a number like "1.")
    and a sequence of block-level elements.
    """
    number: str = ""
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Chapter:
    """A chapter of the work, corresponding to TEI <div2> or a leaf <div1>.

    The heading contains inline content because it may include <hi>
    elements (italic summaries).
    """
    heading: InlineContent = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)


@dataclass
class ChapterGroup:
    """A named group of chapters (e.g. "Prima Classis" in the Epistolae).

    Corresponds to a non-leaf <div1> that contains further <div1>
    children.  Used to build hierarchical TOC entries in the EPUB.
    """
    heading: InlineContent = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)


# A top-level content item is either a standalone Chapter or a
# ChapterGroup containing multiple Chapters.
TopLevelItem: TypeAlias = Union[Chapter, ChapterGroup]


@dataclass
class Work:
    """A complete work parsed from a TEI file.

    Metadata comes from the <teiHeader>; structural content from
    <text>/<body>.

    The ``chapters`` list contains either bare Chapter instances (for
    simple works) or ChapterGroup instances (for collections like the
    Epistolae where chapters are organised into named groups).
    """
    title: str = ""
    author: str = ""
    author_dates: str = ""
    editor: str = ""
    publisher: str = ""
    pub_place: str = ""
    date: str = ""
    series: str = ""
    identifier: str = ""
    source_desc: str = ""
    language: str = "la"
    preamble: Chapter | None = None
    chapters: list[TopLevelItem] = field(default_factory=list)


@dataclass
class Bundle:
    """A built EPUB together with its metadata and cover image.

    This is the intermediate artefact produced by the conversion
    pipeline: it holds everything needed to write the EPUB file to
    disk and to generate a catalog entry on a website.

    Attributes:
        work: The Work model (metadata and structure).
        epub_bytes: The EPUB file content as bytes.
        cover_png: The cover image as PNG bytes.
        source_filename: The original TEI XML filename (stem only,
            e.g. ``"031_Augustinus-Hipponensis_Confessiones"``).
    """
    work: Work
    epub_bytes: bytes
    cover_png: bytes
    source_filename: str = ""
