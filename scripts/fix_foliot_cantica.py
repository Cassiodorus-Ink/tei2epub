#!/usr/bin/env python3
"""
fix_foliot_cantica.py

Normalises verse-citation markup in Gilbertus Foliot's Expositio in Cantica
Canticorum to the canonical Jerome-style div3 form documented in
corpus/canonical_structure.md.

Three patterns are fixed, applied in order:

  Pattern 3  <p><emph><pb/><div3><head>N.</head><hi>v</hi></div3></emph></p>
             (block <div3> wrongly nested inside inline <emph>)
             → <div3><head>N.</head><p><pb/><hi>v</hi></p></div3>
               as a direct sibling in <div2>

  Pattern 2  <div3><head>N.</head><hi>v</hi><p>...</p>...</div3>
             (bare <hi> verse not wrapped in <p>)
             → <div3><head>N.</head><p><hi>v</hi></p><p>...</p>...</div3>

  Pattern 1  <p><emph>N. <hi>v</hi></emph></p> followed by flat <p>commentary</p>
             siblings in <div2>  (dominant pattern, 162 occurrences)
             → <div3><head>N.</head><p><hi>v</hi></p>commentary...</div3>

Orphan <p><emph><pb/></emph></p> elements (page-break-only paragraphs that
appear between verse sections) are unwrapped to bare <pb/> siblings in <div2>.

Usage:
    poetry run python scripts/fix_foliot_cantica.py [--dry-run] [--output PATH]
"""

import argparse
import copy
import re
from pathlib import Path

from lxml import etree

CORPUS = Path(__file__).resolve().parent.parent.parent / "corpus"
INPUT  = CORPUS / "xml/gilbertus-foliot/202_Gilbertus-Foliot_Expositio-in-Cantica-canticorum.xml"

NS = "http://www.tei-c.org/ns/1.0"


def qn(name: str) -> str:
    return f"{{{NS}}}{name}"


def tag(el: etree._Element) -> str:
    return etree.QName(el.tag).localname


def _clone(el: etree._Element) -> etree._Element:
    """Deep-copy an element, clearing its tail (caller sets tail as needed)."""
    c = copy.deepcopy(el)
    c.tail = None
    return c


# ── classifiers ──────────────────────────────────────────────────────────────

def _is_verse_p(el: etree._Element) -> bool:
    """Pattern 1 verse paragraph: <p> whose sole child is <emph><hi>...</hi></emph>."""
    if tag(el) != "p":
        return False
    ch = list(el)
    if len(ch) != 1 or tag(ch[0]) != "emph":
        return False
    return ch[0].find(qn("hi")) is not None


def _is_pb_only_p(el: etree._Element) -> bool:
    """Orphan page-break paragraph: <p><emph>...</emph></p> with no <hi>."""
    if tag(el) != "p":
        return False
    ch = list(el)
    if len(ch) != 1 or tag(ch[0]) != "emph":
        return False
    return ch[0].find(qn("hi")) is None


def _verse_number(emph: etree._Element) -> str:
    """Return the verse-number string from emph leading text, e.g. '1. ' → '1.'
    Returns '' when there is no number."""
    return (emph.text or "").strip()


# ── Pattern 3 ─────────────────────────────────────────────────────────────────

def fix_pattern3(root: etree._Element) -> int:
    """Fix <p><emph><pb/><div3><head>N.</head><hi>v</hi></div3></emph></p>.

    Also collects the following <p> commentary siblings (up to the next verse,
    div3, or head) into the new <div3>, matching Pattern 2 behaviour.
    """
    fixed = 0
    # Collect first so we don't mutate while iterating.
    targets = [
        emph for emph in root.iter(qn("emph"))
        if emph.find(qn("div3")) is not None
    ]
    for emph in targets:
        inner_div3 = emph.find(qn("div3"))
        p_el       = emph.getparent()   # <p>
        div2       = p_el.getparent()   # <div2>
        pb         = emph.find(qn("pb"))
        head       = inner_div3.find(qn("head"))
        hi         = inner_div3.find(qn("hi"))

        new_div3             = etree.Element(qn("div3"))
        new_div3.tail        = p_el.tail
        new_head             = etree.SubElement(new_div3, qn("head"))
        new_head.text        = (head.text or "").strip()
        new_p                = etree.SubElement(new_div3, qn("p"))
        if pb is not None:
            new_p.append(_clone(pb))
        new_p.append(_clone(hi))

        # Collect following commentary <p> siblings until the next verse,
        # div3, head, or end of div2.
        div2_children = list(div2)
        p_idx = div2_children.index(p_el)
        commentary = []
        for sib in div2_children[p_idx + 1:]:
            if (_is_verse_p(sib) or _is_pb_only_p(sib)
                    or tag(sib) in ("div3", "head")):
                break
            commentary.append(sib)
        for p in commentary:
            new_div3.append(_clone(p))

        # Replace <p> and consumed commentary siblings with the new <div3>.
        idx = list(div2).index(p_el)
        div2.remove(p_el)
        for p in commentary:
            div2.remove(p)
        div2.insert(idx, new_div3)
        fixed += 1
    return fixed


# ── Pattern 2 ─────────────────────────────────────────────────────────────────

def fix_pattern2(root: etree._Element) -> int:
    """Fix bare <hi> direct child of <div3 in div2>: wrap it in <p>."""
    fixed = 0
    for div3 in root.iter(qn("div3")):
        if tag(div3.getparent()) != "div2":
            continue
        hi = div3.find(qn("hi"))
        if hi is None or hi.getparent() is not div3:
            continue

        idx   = list(div3).index(hi)
        new_p = etree.Element(qn("p"))
        new_p.append(_clone(hi))
        new_p.tail = hi.tail
        div3.remove(hi)
        div3.insert(idx, new_p)
        fixed += 1
    return fixed


# ── Pattern 1 ─────────────────────────────────────────────────────────────────

def fix_pattern1(root: etree._Element) -> int:
    """Group flat verse <p> + commentary <p>s in each <div2> into <div3>s."""
    fixed = 0
    for div2 in root.iter(qn("div2")):
        children = list(div2)
        if not any(_is_verse_p(c) for c in children):
            continue

        new_children: list[etree._Element] = []
        i = 0
        while i < len(children):
            el = children[i]

            if _is_pb_only_p(el):
                # Unwrap to bare <pb/> (or drop if truly empty).
                pb = el[0].find(qn("pb"))
                if pb is not None:
                    bare_pb      = _clone(pb)
                    bare_pb.tail = el.tail
                    new_children.append(bare_pb)
                i += 1
                continue

            if _is_verse_p(el):
                emph       = el[0]
                pb_in_emph = emph.find(qn("pb"))
                hi         = emph.find(qn("hi"))

                div3      = etree.Element(qn("div3"))
                div3.tail = el.tail

                head_el      = etree.SubElement(div3, qn("head"))
                head_el.text = _verse_number(emph)

                first_p = etree.SubElement(div3, qn("p"))
                if pb_in_emph is not None:
                    first_p.append(_clone(pb_in_emph))
                first_p.append(_clone(hi))

                # Consume following commentary paragraphs until the next
                # verse, orphan-pb, existing div3, or chapter head.
                j = i + 1
                while j < len(children):
                    nxt = children[j]
                    if (_is_verse_p(nxt) or _is_pb_only_p(nxt)
                            or tag(nxt) in ("div3", "head")):
                        break
                    div3.append(_clone(nxt))
                    j += 1

                new_children.append(div3)
                fixed += 1
                i = j
                continue

            # Anything else (chapter <head>, bare <pb/>, existing <div3>):
            # keep as-is.
            new_children.append(_clone(el))
            i += 1

        for ch in list(div2):
            div2.remove(ch)
        for ch in new_children:
            div2.append(ch)

    return fixed


# ── verification ─────────────────────────────────────────────────────────────

def verify(root: etree._Element) -> list[str]:
    """Return a list of remaining anomalies (should be empty after fix)."""
    issues = []

    # No <div3> should remain inside <emph>
    for emph in root.iter(qn("emph")):
        if emph.find(qn("div3")) is not None:
            issues.append("Pattern 3 remnant: <div3> inside <emph>")

    # No bare <hi> as direct child of <div3>
    for div3 in root.iter(qn("div3")):
        for ch in div3:
            if tag(ch) == "hi":
                issues.append(f"Pattern 2 remnant: bare <hi> in <div3>")

    # No <p><emph><hi>…</hi></emph></p> flat in <div2>
    for div2 in root.iter(qn("div2")):
        for ch in div2:
            if _is_verse_p(ch):
                issues.append("Pattern 1 remnant: verse <p> not wrapped in <div3>")

    return issues


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report counts without writing output")
    ap.add_argument("--output", type=Path, metavar="PATH",
                    help="Write to PATH instead of overwriting the source file")
    args = ap.parse_args()

    parser = etree.XMLParser(remove_blank_text=False)
    tree   = etree.parse(str(INPUT), parser)
    root   = tree.getroot()

    n3 = fix_pattern3(root)
    n2 = fix_pattern2(root)
    n1 = fix_pattern1(root)

    print(f"Pattern 3 (div3-in-emph):        {n3:3d} fixed")
    print(f"Pattern 2 (bare hi in div3):      {n2:3d} fixed")
    print(f"Pattern 1 (flat verse p → div3): {n1:3d} fixed")

    issues = verify(root)
    if issues:
        print(f"\n{len(issues)} verification issue(s):")
        for iss in issues:
            print(f"  - {iss}")
    else:
        print("\nVerification passed — no anomalies remaining.")

    if args.dry_run:
        print("Dry run — not writing.")
        return

    out = args.output or INPUT
    tree.write(str(out), pretty_print=True, xml_declaration=True, encoding="UTF-8")
    print(f"Written: {out}")


if __name__ == "__main__":
    main()
