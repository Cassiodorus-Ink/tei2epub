"""Check TEI XML corpus for files where CAPUT headings are embedded in <p> elements.

This detects the structural fault where chapter divisions appear as text at
the start of <p> elements (e.g. "<p>CAPUT II.--3. ...</p>") rather than as
proper <div2><head>CAPUT II.</head> structure.

Usage::

    poetry run python scripts/check_caput_structure.py [corpus_dir]

corpus_dir defaults to ../../corpus/xml relative to this script.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_CORPUS = _SCRIPT_DIR / ".." / ".." / "corpus" / "xml"

# A <p> that starts (after optional <pb/>) with CAPUT followed by a Roman
# numeral / ordinal word.
_CAPUT_IN_P = re.compile(
    r"<p\b[^>]*>\s*(?:<pb\s[^/]*/>\s*)?CAPUT\b",
)


def check_file(path: Path) -> int:
    """Return number of CAPUT-in-<p> occurrences, or 0 if clean."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return len(_CAPUT_IN_P.findall(text))


def main() -> None:
    corpus_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_CORPUS
    corpus_dir = corpus_dir.resolve()

    xml_files = sorted(corpus_dir.rglob("*.xml"))
    if not xml_files:
        print(f"No XML files found under {corpus_dir}")
        sys.exit(1)

    flagged: list[tuple[Path, int]] = []
    for path in xml_files:
        n = check_file(path)
        if n:
            flagged.append((path, n))

    print(f"Checked {len(xml_files)} file(s). "
          f"{len(flagged)} have CAPUT-in-<p> (need fix_caput_structure.py):\n")
    for path, n in flagged:
        print(f"  {path.relative_to(corpus_dir)}  ({n} occurrence(s))")


if __name__ == "__main__":
    main()
