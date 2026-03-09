"""Convert TEI XML files to EPUB format.

This package handles Corpus Corporum-style TEI files for the
Patrologia Latina, converting them through an intermediate data
model into EPUB 3 e-books.

Usage::

    from pathlib import Path
    from tei2epub import convert

    convert(Path("input.xml"), Path("output.epub"))

Or build a bundle (EPUB bytes + cover + metadata) for further use::

    from tei2epub import build_bundle

    bundle = build_bundle(Path("input.xml"))
    # bundle.epub_bytes, bundle.cover_png, bundle.work.title, ...

Or from the command line::

    python -m tei2epub input.xml output.epub
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tei2epub.epub import build_epub_bytes, write_epub
from tei2epub.model import Bundle
from tei2epub.parse import parse


def convert(tei_path: str | Path, epub_path: str | Path) -> None:
    """Convert a TEI XML file to an EPUB file.

    Args:
        tei_path: Path to the input TEI XML file.
        epub_path: Path for the output EPUB file.
    """
    work = parse(tei_path)
    write_epub(work, epub_path)


def build_bundle(
    tei_path: str | Path,
    mtime: datetime | None = None,
    version_date: str = "",
) -> Bundle:
    """Parse a TEI XML file and build an EPUB bundle in memory.

    Returns a :class:`~tei2epub.model.Bundle` containing the EPUB
    file bytes, the cover PNG image bytes, and the full Work metadata.

    Args:
        tei_path: Path to the input TEI XML file.
        mtime: If given, used as the ``dcterms:modified`` timestamp
            in the EPUB.  Defaults to the current time.
        version_date: If given, shown on the colophon page.  Should
            be omitted for probe builds (change detection) and
            included for final builds (publication).

    Returns:
        A Bundle ready to be written to disk or used for site
        generation.
    """
    tei_path = Path(tei_path)
    work = parse(tei_path)
    epub_bytes, cover_png = build_epub_bytes(
        work, mtime=mtime, version_date=version_date,
    )
    return Bundle(
        work=work,
        epub_bytes=epub_bytes,
        cover_png=cover_png,
        source_filename=tei_path.stem,
    )
