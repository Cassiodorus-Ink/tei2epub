"""Build script for converting TEI XML files to EPUB.

Discovers TEI XML files under an input directory and converts each to
an EPUB under a parallel output directory, preserving the directory
structure and using the same filename (with .epub extension).

Only rebuilds files whose EPUB is missing or older than the source XML,
unless --force is given.

Usage::

    python scripts/build.py                        # build all (incremental)
    python scripts/build.py --force                # rebuild everything
    python scripts/build.py epistolae              # only files matching "epistolae"
    python scripts/build.py -i ~/tei -o ~/epubs    # custom directories
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Project root is the parent of the scripts/ directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_INPUT_DIR = PROJECT_ROOT / "local" / "input"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "local" / "output"


def _discover(
    input_dir: Path, output_dir: Path, filters: list[str]
) -> list[tuple[Path, Path]]:
    """Find all .xml files under *input_dir* and pair with output paths.

    Returns a list of (xml_path, epub_path) tuples.  If *filters* is
    non-empty, only files whose name contains at least one filter
    substring (case-insensitive) are included.
    """
    pairs: list[tuple[Path, Path]] = []
    for xml_path in sorted(input_dir.rglob("*.xml")):
        if filters:
            name_lower = xml_path.name.lower()
            if not any(f.lower() in name_lower for f in filters):
                continue
        rel = xml_path.relative_to(input_dir)
        epub_path = output_dir / rel.with_suffix(".epub")
        pairs.append((xml_path, epub_path))
    return pairs


def _needs_build(xml_path: Path, epub_path: Path) -> bool:
    """Return True if the EPUB needs to be (re)built."""
    if not epub_path.exists():
        return True
    return xml_path.stat().st_mtime > epub_path.stat().st_mtime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build EPUBs from TEI XML files.",
    )
    parser.add_argument(
        "filter",
        nargs="*",
        help="Only build files whose name contains this substring.",
    )
    parser.add_argument(
        "--input-dir", "-i",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing TEI XML files (default: local/input).",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for output EPUB files (default: local/output).",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Rebuild all files, ignoring timestamps.",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        dest="list_only",
        help="List files that would be built, without building.",
    )
    args = parser.parse_args(argv)

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not input_dir.is_dir():
        print(f"Error: input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    pairs = _discover(input_dir, output_dir, args.filter)
    if not pairs:
        print("No matching XML files found.")
        return

    # Determine which need building.
    if args.force:
        to_build = pairs
    else:
        to_build = [
            (xml, epub) for xml, epub in pairs
            if _needs_build(xml, epub)
        ]

    if args.list_only:
        for xml_path, epub_path in pairs:
            status = "STALE" if (xml_path, epub_path) in to_build else "ok"
            rel = xml_path.relative_to(input_dir)
            print(f"  [{status:>5}]  {rel}")
        print(f"\n{len(to_build)} of {len(pairs)} file(s) need building.")
        return

    if not to_build:
        print(f"All {len(pairs)} file(s) up to date.")
        return

    print(f"Building {len(to_build)} of {len(pairs)} file(s)...\n")

    # Import here so --list and --help are fast even without deps.
    from tei2epub import convert

    built = 0
    failed = 0
    for xml_path, epub_path in to_build:
        rel_in = xml_path.relative_to(input_dir)
        rel_out = epub_path.relative_to(output_dir)
        print(f"  {rel_in}")
        print(f"    -> {rel_out}", end="", flush=True)
        t0 = time.monotonic()
        try:
            epub_path.parent.mkdir(parents=True, exist_ok=True)
            convert(xml_path, epub_path)
            elapsed = time.monotonic() - t0
            size_kb = epub_path.stat().st_size / 1024
            print(f"  ({elapsed:.1f}s, {size_kb:.0f} KB)")
            built += 1
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"  FAILED ({elapsed:.1f}s)")
            print(f"    {exc}", file=sys.stderr)
            failed += 1

    print(f"\nDone: {built} built, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
