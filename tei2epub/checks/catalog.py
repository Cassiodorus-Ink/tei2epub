"""Central registry of all conformance check specs.

Every check that produces a Finding is described here exactly once,
with its code, a one-line title, and a short description.  Check
modules import the :class:`CheckSpec` instance from this module and
use ``spec.code`` when constructing Findings — that keeps the codes
in sync with the registry and gives reports a place to look up
human-readable descriptions.

Adding a new check:
  1. Define a new ``CheckSpec`` constant below.
  2. Add it to :data:`ALL` (preserves declaration order in reports).
  3. Reference it from the check module via ``from .catalog import …``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckSpec:
    code: str           # short stable identifier, e.g. "CV-1"
    title: str          # one-line summary fit for a column header
    description: str    # 2-4 line description for the report header


# ── Coverage ────────────────────────────────────────────────────────────────

CV_1 = CheckSpec(
    code="CV-1",
    title="Unhandled tag in parent context",
    description=(
        "An XML element appears inside a parent that the parser walks, "
        "but the element's tag is not in the allow-list for that parent "
        "(see tei2epub/coverage.py).  The element's content will be "
        "flattened, dropped, or otherwise not rendered as intended."
    ),
)


# ── Text loss ───────────────────────────────────────────────────────────────

TL_1 = CheckSpec(
    code="TL-1",
    title="<p> sibling of <div2> in <div1>",
    description=(
        "In the multi-book parse path only <div2> children of each "
        "<div1> become chapters.  Direct <p> children appearing "
        "between or after <div2> siblings are silently dropped.  "
        "Wrap them in a <div2><head>, or move into an existing <div2>."
    ),
)

TL_2 = CheckSpec(
    code="TL-2",
    title="<div3> nested inside <emph> or <p>",
    description=(
        "Block-level <div3> trapped inside inline markup is invisible "
        "to the block-level parser and its content is lost.  Move "
        "the <div3> out to be a sibling of its enclosing block."
    ),
)

TL_3 = CheckSpec(
    code="TL-3",
    title="Bare <hi> as child of <div3>",
    description=(
        "_parse_blocks does not handle bare block-level <hi>; it "
        "expects <hi> only inside <p> as inline emphasis.  Wrap the "
        "<hi> in a <p>."
    ),
)

TL_5 = CheckSpec(
    code="TL-5",
    title="Plain <div> directly under <body>",
    description=(
        "tei2epub recognises numbered <div1>/<div2>/<div3> only.  "
        "Plain <div> elements directly under <body> are not picked "
        "up by any parse path and their content is dropped."
    ),
)

TL_6 = CheckSpec(
    code="TL-6",
    title="Parallel-language siblings",
    description=(
        "Sibling <div1>/<div2> elements have differing xml:lang values, "
        "indicating parallel-text content (e.g. Greek alongside Latin).  "
        "tei2epub merges the languages without labelling them."
    ),
)

TL_7 = CheckSpec(
    code="TL-7",
    title="<note> with block-level structural children",
    description=(
        "A <note> element contains <div1>, <div2>, <div3>, or <head> "
        "children but is not the Admonitio-pattern wrapper (direct child "
        "of <body> containing <div*>).  The parser flattens <note> to "
        "plain text, so headings and divisions are lost.  Editorial "
        "prefaces should use <front>; footnotes should not contain "
        "block-level markup."
    ),
)


# ── Structural ──────────────────────────────────────────────────────────────

ST_1 = CheckSpec(
    code="ST-1",
    title="CAPUT heading embedded in <p>",
    description=(
        "A <p> begins with the word CAPUT — a chapter heading was "
        "encoded inline rather than wrapped in <div2><head>CAPUT …</head>.  "
        "The heading will appear as run-on text in the chapter body."
    ),
)

ST_2 = CheckSpec(
    code="ST-2",
    title="Single wrapping <div1>",
    description=(
        "<body> contains exactly one <div1> whose <head> matches the "
        "work title.  This causes the book title to appear as the "
        "first chapter in the EPUB TOC.  Triage required: not always "
        "a defect (some single-work files use this layout legitimately)."
    ),
)


ALL: tuple[CheckSpec, ...] = (CV_1, TL_1, TL_2, TL_3, TL_5, TL_6, TL_7, ST_1, ST_2)

BY_CODE: dict[str, CheckSpec] = {spec.code: spec for spec in ALL}
