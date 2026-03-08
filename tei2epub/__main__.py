"""CLI entry point for TEI to EPUB conversion.

Usage::

    python -m tei2epub input.xml output.epub
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tei2epub import convert


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="tei2epub",
        description="Convert a Corpus Corporum TEI XML file to EPUB.",
    )
    parser.add_argument(
        "tei_file",
        type=Path,
        help="Path to the input TEI XML file.",
    )
    parser.add_argument(
        "epub_file",
        type=Path,
        help="Path for the output EPUB file.",
    )
    args = parser.parse_args(argv)

    if not args.tei_file.exists():
        print(f"Error: {args.tei_file} not found", file=sys.stderr)
        sys.exit(1)

    convert(args.tei_file, args.epub_file)
    print(f"Written: {args.epub_file}")


if __name__ == "__main__":
    main()
