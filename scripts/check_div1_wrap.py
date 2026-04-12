"""Check TEI XML files for the single-div1 wrapping pattern.

A file has the wrapping pattern when the <body> contains exactly one <div1>
(which holds a <head> for the book title and <div2> children for chapters).
This causes the book title to appear as the first chapter in the EPUB TOC.

Usage::

    poetry run python scripts/check_div1_wrap.py [corpus_dir]

corpus_dir defaults to ../../corpus/xml relative to this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

_TEI_NS = "http://www.tei-c.org/ns/1.0"
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_CORPUS = _SCRIPT_DIR / ".." / ".." / "corpus" / "xml"


def _localname(element: etree._Element) -> str:
    tag = element.tag
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def check_file(path: Path) -> bool:
    """Return True if the file has a single wrapping <div1>."""
    try:
        tree = etree.parse(path)
    except etree.XMLSyntaxError as e:
        print(f"  PARSE ERROR: {e}", file=sys.stderr)
        return False

    root = tree.getroot()

    # Find <body> — with or without TEI namespace.
    bodies = (
        root.findall(f".//{{{_TEI_NS}}}body")
        or root.findall(".//body")
    )
    if not bodies:
        return False
    body = bodies[0]

    div1s = [el for el in body.iter() if _localname(el) == "div1"]
    return len(div1s) == 1


def main() -> None:
    corpus_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_CORPUS
    corpus_dir = corpus_dir.resolve()

    xml_files = sorted(corpus_dir.rglob("*.xml"))
    if not xml_files:
        print(f"No XML files found under {corpus_dir}")
        sys.exit(1)

    flagged: list[Path] = []
    for path in xml_files:
        if check_file(path):
            flagged.append(path)

    print(f"Checked {len(xml_files)} file(s). "
          f"{len(flagged)} have the single-div1 wrapping pattern:\n")
    for path in flagged:
        # Print author/filename relative to corpus root for readability.
        print(f"  {path.relative_to(corpus_dir)}")


if __name__ == "__main__":
    main()
