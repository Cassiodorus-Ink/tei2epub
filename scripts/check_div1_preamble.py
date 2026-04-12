"""Detect <p> elements that are direct children of <div1> alongside <div2> children.

In the multi-book parse path (_parse_multi_book), only <div2> children of each
<div1> are collected as chapters.  Any direct <p> children are silently dropped.
This script finds files and div1 elements where that silent drop loses real text.

For each flagged <div1> it reports:
  - position of the div1 (index among siblings)
  - the structural pattern: multi-book (multiple sibling div1s) or simple/other
  - where the stray <p>s sit: BEFORE first div2 (preamble), AFTER last div2
    (postscript), or BETWEEN div2s (interstitial)
  - a short excerpt of the first stray paragraph

Usage::

    poetry run python scripts/check_div1_preamble.py [corpus_dir]

corpus_dir defaults to ../../corpus/xml relative to this script.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

_TEI_NS = "http://www.tei-c.org/ns/1.0"
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_CORPUS = _SCRIPT_DIR / ".." / ".." / "corpus" / "xml"


def _ns(tag: str) -> str:
    return f"{{{_TEI_NS}}}{tag}"


def _localname(el: etree._Element) -> str:
    tag = el.tag
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _text_content(el: etree._Element) -> str:
    """Return all text content of an element, stripping whitespace."""
    return "".join(el.itertext()).strip()


def _is_trivial(p: etree._Element) -> bool:
    """True if a <p> contains only page-break markers or whitespace."""
    text = _text_content(p)
    if not text:
        return True
    # A page break marker like <pb n="0123"/> may produce only digits/punctuation
    # in itertext; treat paragraphs whose only non-whitespace content comes from
    # <pb> or <emph> page-number children as trivial.
    children = list(p)
    non_pb = [c for c in children if _localname(c) not in ("pb",)]
    direct_text = (p.text or "").strip()
    if not direct_text and not non_pb:
        return True  # only <pb> children, no direct text
    # If the only content is a bare page number (all digits), treat as trivial.
    if text.isdigit():
        return True
    return False


def _excerpt(p: etree._Element, max_len: int = 80) -> str:
    text = " ".join(_text_content(p).split())
    return text[:max_len] + ("…" if len(text) > max_len else "")


@dataclass
class StrayPs:
    position: str          # "before", "after", or "between"
    count: int
    first_excerpt: str


@dataclass
class FlaggedDiv1:
    div1_index: int        # 0-based index among sibling div1s
    head_text: str
    stray: list[StrayPs] = field(default_factory=list)


@dataclass
class FlaggedFile:
    path: Path
    pattern: str           # "multi-book", "simple", "collection", "other"
    flagged_div1s: list[FlaggedDiv1] = field(default_factory=list)


def _detect_pattern(body: etree._Element) -> str:
    div1s = body.findall(_ns("div1"))
    if not div1s:
        return "other"
    if len(div1s) > 1:
        return "multi-book"
    # Single div1
    div1 = div1s[0]
    if div1.find(_ns("div1")) is not None:
        return "collection"
    if div1.find(_ns("div2")) is not None:
        return "simple"
    return "other"


def _check_div1(div1: etree._Element, index: int) -> FlaggedDiv1 | None:
    """Return a FlaggedDiv1 if this div1 has non-trivial direct <p>s alongside <div2>s."""
    div2s = div1.findall(_ns("div2"))
    if not div2s:
        return None  # no div2s — flat structure, handled elsewhere

    head = div1.find(_ns("head"))
    head_text = _text_content(head)[:60] if head is not None else ""

    # Walk direct children, classify each <p> relative to div2 positions.
    children = list(div1)
    seen_div2 = False
    last_div2_passed = False
    between_div2s = False

    before_ps: list[etree._Element] = []
    after_ps: list[etree._Element] = []
    between_ps: list[etree._Element] = []

    div2_count_seen = 0
    total_div2 = len(div2s)

    for child in children:
        tag = _localname(child)
        if tag == "div2":
            seen_div2 = True
            div2_count_seen += 1
            between_div2s = div2_count_seen < total_div2
            last_div2_passed = True
        elif tag == "p":
            if not seen_div2:
                before_ps.append(child)
            elif between_div2s:
                between_ps.append(child)
            else:
                after_ps.append(child)

    # Filter to non-trivial paragraphs.
    before_ps = [p for p in before_ps if not _is_trivial(p)]
    after_ps  = [p for p in after_ps  if not _is_trivial(p)]
    between_ps = [p for p in between_ps if not _is_trivial(p)]

    if not (before_ps or after_ps or between_ps):
        return None

    result = FlaggedDiv1(div1_index=index, head_text=head_text)
    if before_ps:
        result.stray.append(StrayPs("before", len(before_ps), _excerpt(before_ps[0])))
    if between_ps:
        result.stray.append(StrayPs("between", len(between_ps), _excerpt(between_ps[0])))
    if after_ps:
        result.stray.append(StrayPs("after", len(after_ps), _excerpt(after_ps[0])))
    return result


def check_file(path: Path) -> FlaggedFile | None:
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError as e:
        print(f"  PARSE ERROR {path.name}: {e}", file=sys.stderr)
        return None

    root = tree.getroot()
    bodies = root.findall(f".//{_ns('body')}") or root.findall(".//body")
    if not bodies:
        return None
    body = bodies[0]

    pattern = _detect_pattern(body)

    # Check all div1s anywhere under body (handles nested collection structures too).
    all_div1s = body.findall(_ns("div1"))
    if not all_div1s:
        return None

    flagged_div1s: list[FlaggedDiv1] = []
    for i, div1 in enumerate(all_div1s):
        result = _check_div1(div1, i)
        if result is not None:
            flagged_div1s.append(result)

    if not flagged_div1s:
        return None

    return FlaggedFile(path=path, pattern=pattern, flagged_div1s=flagged_div1s)


def main() -> None:
    corpus_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_CORPUS
    corpus_dir = corpus_dir.resolve()

    xml_files = sorted(corpus_dir.rglob("*.xml"))
    if not xml_files:
        print(f"No XML files found under {corpus_dir}")
        sys.exit(1)

    flagged: list[FlaggedFile] = []
    for path in xml_files:
        result = check_file(path)
        if result is not None:
            flagged.append(result)

    print(f"Checked {len(xml_files)} file(s).  "
          f"{len(flagged)} have direct <p> children in <div1> alongside <div2>s.\n")

    for ff in flagged:
        rel = ff.path.relative_to(corpus_dir)
        print(f"{rel}  [{ff.pattern}]")
        for fd in ff.flagged_div1s:
            head_label = f'"{fd.head_text}"' if fd.head_text else "(no head)"
            print(f"  div1[{fd.div1_index}] {head_label}")
            for sp in fd.stray:
                print(f"    {sp.count} <p>(s) {sp.position} div2s: \"{sp.first_excerpt}\"")
        print()


if __name__ == "__main__":
    main()
