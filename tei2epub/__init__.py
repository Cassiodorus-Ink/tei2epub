"""Convert TEI XML files to EPUB format.

This package handles Corpus Corporum-style TEI files for the
Patrologia Latina, converting them through an intermediate data
model into EPUB 3 e-books.

Usage::

    from pathlib import Path
    from tei2epub import convert

    convert(Path("input.xml"), Path("output.epub"))

Or from the command line::

    python -m tei2epub input.xml output.epub
"""

from __future__ import annotations

from pathlib import Path

from tei2epub.parse import parse
from tei2epub.epub import write_epub


def convert(tei_path: str | Path, epub_path: str | Path) -> None:
    """Convert a TEI XML file to an EPUB file.

    Args:
        tei_path: Path to the input TEI XML file.
        epub_path: Path for the output EPUB file.
    """
    work = parse(tei_path)
    write_epub(work, epub_path)
