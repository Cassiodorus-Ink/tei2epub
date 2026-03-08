"""Package rendered XHTML chapters into an EPUB file.

Uses ebooklib to handle the EPUB 3 container format: OPF manifest,
navigation document, spine ordering, and ZIP packaging.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from ebooklib import epub  # type: ignore[import-untyped]

from tei2epub.model import Chapter, ChapterGroup, InlineContent, Work
from tei2epub.render import (
    render_chapter,
    render_colophon,
    render_cover_png,
    render_group_title,
    render_title_page,
    _heading_plain_text,
)


def _load_css() -> str:
    """Load the bundled stylesheet."""
    css_ref = resources.files("tei2epub").joinpath("style.css")
    return css_ref.read_text(encoding="utf-8")


def _plain_heading(chapter: Chapter) -> str:
    """Extract a plain-text heading string for TOC entries."""
    text = _heading_plain_text(chapter.heading)
    # Replace -- with em-dash for display.
    text = text.replace("-- ", "\u2014 ")
    return text or "Untitled"


def _plain_heading_inline(heading: list[Any]) -> str:
    """Extract plain-text from an InlineContent list for group headings."""
    text = _heading_plain_text(heading)
    text = text.replace("-- ", "\u2014 ")
    return text or "Untitled"


def write_epub(work: Work, output_path: str | Path) -> None:
    """Write a Work to an EPUB 3 file.

    Args:
        work: The parsed and modelled work.
        output_path: Filesystem path for the output .epub file.
    """
    book = epub.EpubBook()

    # -- Metadata ------------------------------------------------------------

    identifier = work.identifier or "cassiodorus-unknown"
    book.set_identifier(identifier)
    book.set_title(work.title)
    book.set_language(work.language)
    if work.author:
        book.add_author(work.author)
    if work.editor:
        book.add_metadata("DC", "contributor", work.editor)
    if work.publisher:
        book.add_metadata("DC", "publisher", work.publisher)
    if work.date:
        book.add_metadata("DC", "date", work.date)
    if work.series:
        book.add_metadata(
            None,
            "meta",
            work.series,
            {"property": "belongs-to-collection"},
        )

    # -- CSS -----------------------------------------------------------------

    css_content = _load_css()
    css_item = epub.EpubItem(
        uid="stylesheet",
        file_name="style.css",
        media_type="text/css",
        content=css_content.encode("utf-8"),
    )
    book.add_item(css_item)

    # -- Front matter --------------------------------------------------------

    def _make_front_page(
        file_name: str, title: str, xhtml: str,
    ) -> epub.EpubHtml:
        """Create an EPUB item for a front matter page."""
        item = epub.EpubHtml(
            title=title,
            file_name=file_name,
            lang=work.language,
        )
        item.set_content(xhtml.encode("utf-8"))
        item.add_item(css_item)
        book.add_item(item)
        return item

    front_pages: list[epub.EpubHtml] = []

    # Cover image (PNG) and cover page.
    cover_png = render_cover_png(work)
    book.set_cover("cover.png", cover_png, create_page=True)

    # Title page.
    tp_xhtml = render_title_page(work)
    front_pages.append(_make_front_page("title.xhtml", work.title, tp_xhtml))

    # Colophon.
    co_xhtml = render_colophon(work)
    front_pages.append(_make_front_page("colophon.xhtml", "Colophon", co_xhtml))

    # -- Chapters ------------------------------------------------------------

    # We accumulate epub chapter items (for the spine) and TOC entries
    # separately, since the TOC may be hierarchical while the spine
    # is always flat.

    epub_chapters: list[epub.EpubHtml] = []
    chapter_counter = 0
    group_counter = 0

    def _add_chapter(chapter: Chapter) -> tuple[epub.EpubHtml, epub.Link]:
        """Add a Chapter to the book and return (epub_item, toc_link)."""
        nonlocal chapter_counter
        chapter_counter += 1
        ch_id = f"ch{chapter_counter:03d}"
        file_name = f"{ch_id}.xhtml"

        xhtml = render_chapter(chapter, ch_id)
        epub_ch = epub.EpubHtml(
            title=_plain_heading(chapter),
            file_name=file_name,
            lang=work.language,
        )
        epub_ch.set_content(xhtml.encode("utf-8"))
        epub_ch.add_item(css_item)
        book.add_item(epub_ch)
        epub_chapters.append(epub_ch)

        link = epub.Link(file_name, _plain_heading(chapter), ch_id)
        return epub_ch, link

    def _add_group_title(
        heading: InlineContent,
    ) -> tuple[epub.EpubHtml, str]:
        """Add a group title page and return (epub_item, file_name)."""
        nonlocal group_counter
        group_counter += 1
        grp_id = f"grp{group_counter:03d}"
        file_name = f"{grp_id}.xhtml"

        title_text = _heading_plain_text(heading)
        title_text = title_text.replace("-- ", "\u2014 ")
        xhtml = render_group_title(heading, grp_id)
        epub_item = epub.EpubHtml(
            title=title_text,
            file_name=file_name,
            lang=work.language,
        )
        epub_item.set_content(xhtml.encode("utf-8"))
        epub_item.add_item(css_item)
        book.add_item(epub_item)
        epub_chapters.append(epub_item)

        return epub_item, file_name

    # Build TOC entries.  The TOC structure mirrors the Work structure:
    # bare Chapters become flat epub.Link entries; ChapterGroups become
    # (epub.Section, [epub.Link, ...]) tuples for nested TOC.  Each
    # ChapterGroup gets a title page that appears in the spine and
    # serves as the TOC anchor for the group.

    toc_entries: list[Any] = []

    # Preamble.
    if work.preamble is not None:
        _, link = _add_chapter(work.preamble)
        toc_entries.append(link)

    # Body chapters.
    for item in work.chapters:
        if isinstance(item, ChapterGroup):
            # Title page for the group (in the spine, but not as a
            # separate TOC child — the parent Section links to it).
            _, grp_file = _add_group_title(item.heading)
            group_links: list[epub.Link] = []
            for chapter in item.chapters:
                _, link = _add_chapter(chapter)
                group_links.append(link)
            section_title = _plain_heading_inline(item.heading)
            toc_entries.append(
                (epub.Section(section_title, href=grp_file), group_links)
            )
        else:
            # Bare chapter (no grouping).
            _, link = _add_chapter(item)
            toc_entries.append(link)

    # -- Navigation ----------------------------------------------------------

    # Find the cover page created by set_cover and include it in the
    # spine as a non-linear item (the EPUB convention — e-readers
    # display it as the first page but it's not part of the linear
    # reading flow).
    cover_page = next(
        (item for item in book.items if isinstance(item, epub.EpubCoverHtml)),
        None,
    )
    cover_spine: list[tuple[epub.EpubHtml, str] | epub.EpubHtml | str] = []
    if cover_page is not None:
        cover_spine = [(cover_page, "no")]

    book.toc = toc_entries  # type: ignore[assignment]
    book.spine = [*cover_spine, *front_pages, "nav", *epub_chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # -- Write ---------------------------------------------------------------

    epub.write_epub(str(output_path), book, {"epub3_pages": False})
