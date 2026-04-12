#!/usr/bin/env python3
"""
fix_foliot_cantica.py

Normalises verse-citation markup in Gilbertus Foliot's Expositio in Cantica
Canticorum to the canonical Jerome-style div3 form documented in
corpus/canonical_structure.md.

Four patterns are fixed, applied in order:

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

  Pattern 4  <div3> containing embedded numbered <p><emph>N. <hi>v</hi></emph></p>
             patterns for subsequent verses — multiple verses collapsed into one
             <div3>.  Split at each numbered verse boundary into separate <div3>s.
             Non-numbered <p><emph><hi> (continuation verse quotes without a
             leading number) stay in the current section.

  Pattern 5  <div3><head>M.</head><p><hi>text1. N. text2</hi></p>commentary</div3>
             — a verse number embedded mid-text inside a <hi> citation.  Split into:
             <div3><head>M.</head><p><hi>text1.</hi></p></div3>
             <div3><head>N.</head><p><hi>text2</hi></p>commentary</div3>
             Multiple embedded numbers handled recursively; all commentary goes
             to the last section.

Orphan <p><emph><pb/></emph></p> elements (page-break-only paragraphs that
appear between verse sections) are unwrapped to bare <pb/> siblings in <div2>.

Usage:
    poetry run python scripts/fix_foliot_cantica.py [--dry-run] [--output PATH]
                                                    [--only-pattern N]
"""

import argparse
import copy
import re
from pathlib import Path
from typing import Optional

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
    """Pattern 1 verse paragraph: <p> whose first child is <emph><hi>...</hi></emph>,
    with any remaining children being <pb/> elements only."""
    if tag(el) != "p":
        return False
    ch = list(el)
    if not ch or tag(ch[0]) != "emph":
        return False
    if ch[0].find(qn("hi")) is None:
        return False
    # Allow <pb/> siblings after the <emph>, but nothing else.
    return all(tag(c) == "pb" for c in ch[1:])


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


def _is_numbered_verse_p(el: etree._Element) -> bool:
    """Pattern 4 trigger: a verse <p> whose <emph> leading text starts with a
    digit — i.e. a numbered verse citation, not a bare continuation quote."""
    if not _is_verse_p(el):
        return False
    emph = el[0]
    return bool((emph.text or "").strip()) and (emph.text or "").strip()[0].isdigit()


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


# ── Pattern 4 ─────────────────────────────────────────────────────────────────

def fix_pattern4(root: etree._Element) -> int:
    """Split <div3> elements that contain embedded numbered verse citations.

    A <div3> whose body contains one or more <p><emph>N. <hi>v</hi></emph></p>
    children (where the emph text starts with a digit) is split at each such
    boundary into separate <div3> elements.  Non-numbered verse <p>s (bare
    continuation quotes) are left in the current section.
    """
    fixed = 0

    # Collect targets first to avoid mutating while iterating.
    targets = [
        div3 for div3 in root.iter(qn("div3"))
        if any(_is_numbered_verse_p(ch) for ch in div3)
    ]

    for div3 in targets:
        parent = div3.getparent()
        children = list(div3)
        div3_idx = list(parent).index(div3)

        # Build list of new <div3> elements by splitting at numbered verse <p>s.
        groups: list[tuple[str, list[etree._Element]]] = []  # (head_text, [children])

        # The first group inherits the original <div3>'s head.
        orig_head = div3.find(qn("head"))
        current_head = (orig_head.text or "").strip() if orig_head is not None else ""
        current_children: list[etree._Element] = []

        for ch in children:
            if tag(ch) == "head":
                # Skip — we'll reconstruct heads ourselves.
                continue
            if _is_numbered_verse_p(ch):
                # Save the current group and start a new one.
                groups.append((current_head, current_children))
                p_children = list(ch)
                emph = p_children[0]
                current_head = _verse_number(emph)
                # Convert the verse <p> to canonical form:
                # <p><emph>N. <hi>v</hi></emph>[<pb/>...]</p> → <p><hi>v</hi>[<pb/>...]</p>
                hi = emph.find(qn("hi"))
                pb_in_emph = emph.find(qn("pb"))
                new_p = etree.Element(qn("p"))
                if pb_in_emph is not None:
                    new_p.append(_clone(pb_in_emph))
                if hi is not None:
                    new_p.append(_clone(hi))
                # Carry any <pb> siblings that followed the <emph> inside the <p>.
                for sib in p_children[1:]:
                    if tag(sib) == "pb":
                        new_p.append(_clone(sib))
                current_children = [new_p]
            else:
                current_children.append(_clone(ch))

        groups.append((current_head, current_children))

        # Only proceed if there is actually more than one group.
        if len(groups) <= 1:
            continue

        # Build the new <div3> elements.
        new_div3s: list[etree._Element] = []
        for i, (head_text, group_children) in enumerate(groups):
            new_d3 = etree.Element(qn("div3"))
            new_d3.tail = div3.tail if i == len(groups) - 1 else None
            new_head = etree.SubElement(new_d3, qn("head"))
            new_head.text = head_text
            for ch in group_children:
                new_d3.append(ch)
            new_div3s.append(new_d3)
            fixed += 1

        # Replace original <div3> with the new set.
        parent.remove(div3)
        for offset, new_d3 in enumerate(new_div3s):
            parent.insert(div3_idx + offset, new_d3)

    # fix_pattern4 counts groups created, subtract the original div3 count.
    return fixed - len(targets)


# ── Pattern 5 ─────────────────────────────────────────────────────────────────

# Matches an interior verse number: requires at least one non-space character
# before the match so we don't fire on a number that leads the whole text.
# Captures (text_before, verse_num, text_after).
_INTERIOR_VERSE_RE = re.compile(
    r'^(.+?)\s+(\d+)\.\s+(.+)$',
    re.DOTALL,
)


def _split_hi_text(text: str) -> Optional[tuple[str, str, str]]:
    """If `text` contains an interior verse number, return (before, num, after).
    Returns None if no split point found."""
    m = _INTERIOR_VERSE_RE.match(text.strip())
    if m is None:
        return None
    before, num, after = m.group(1), m.group(2), m.group(3)
    # Reject if the number looks like part of a citation (preceded by a letter
    # without punctuation, e.g. "Cor 10 4"); require the character immediately
    # before the number to be punctuation or whitespace after punctuation.
    before_stripped = before.rstrip()
    if before_stripped and before_stripped[-1].isalpha():
        return None
    return before, num, after


def fix_pattern5(root: etree._Element) -> int:
    """Split <div3> elements whose verse <hi> citation contains an interior
    verse number (e.g. 'text1. N. text2').

    For each split point the original <div3> is broken into two:
      - first:  original head, verse part 1 in <p><hi>...</hi></p>, no commentary
      - second: head=N., verse part 2 in <p><hi>...</hi></p>, all commentary

    If the verse part 2 itself contains another interior number, the process
    repeats (handled by re-queuing).
    """
    fixed = 0

    # Collect first to avoid mutating while iterating; re-run until stable.
    changed = True
    while changed:
        changed = False
        targets = list(root.iter(qn("div3")))
        for div3 in targets:
            head_el = div3.find(qn("head"))
            children = list(div3)
            # Find the first <p> child (after head) that contains a bare <hi>.
            verse_p = None
            for ch in children:
                if tag(ch) == "p":
                    # The verse <hi> may be preceded by a <pb/> inside the <p>.
                    non_pb = [c for c in list(ch) if tag(c) != "pb"]
                    if non_pb and tag(non_pb[0]) == "hi":
                        verse_p = ch
                    break  # only inspect the first <p>

            if verse_p is None:
                continue

            hi_el = verse_p.find(qn("hi"))
            hi_text = "".join(hi_el.itertext())
            split = _split_hi_text(hi_text)
            if split is None:
                continue

            before_text, verse_num, after_text = split
            parent = div3.getparent()
            div3_idx = list(parent).index(div3)

            # Commentary: all <p> children of div3 after the verse_p, plus any
            # other non-head children.
            verse_p_idx = children.index(verse_p)
            commentary = [_clone(c) for c in children[verse_p_idx + 1:]
                          if tag(c) != "head"]

            # Build first <div3>: original head, truncated verse, no commentary.
            first = etree.Element(qn("div3"))
            f_head = etree.SubElement(first, qn("head"))
            f_head.text = (head_el.text or "").strip() if head_el is not None else ""
            f_p = etree.SubElement(first, qn("p"))
            # Preserve any leading <pb/> elements that preceded the <hi>.
            for leading in list(verse_p):
                if tag(leading) == "pb":
                    f_p.append(_clone(leading))
                else:
                    break
            f_hi = etree.SubElement(f_p, qn("hi"))
            f_hi.text = before_text.strip()

            # Build second <div3>: new head=N., remainder of verse, all commentary.
            second = etree.Element(qn("div3"))
            second.tail = div3.tail
            s_head = etree.SubElement(second, qn("head"))
            s_head.text = verse_num + "."
            s_p = etree.SubElement(second, qn("p"))
            s_hi = etree.SubElement(s_p, qn("hi"))
            s_hi.text = after_text.strip()
            for c in commentary:
                second.append(c)

            # Replace original div3 with the two new ones.
            parent.remove(div3)
            parent.insert(div3_idx, first)
            parent.insert(div3_idx + 1, second)
            fixed += 1
            changed = True
            break  # restart iteration after mutation

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

    # No numbered <p><emph><hi>…</hi></emph></p> inside a <div3>
    for div3 in root.iter(qn("div3")):
        for ch in div3:
            if _is_numbered_verse_p(ch):
                issues.append("Pattern 4 remnant: numbered verse <p> inside <div3>")

    # No interior verse numbers in <hi> verse citations
    for div3 in root.iter(qn("div3")):
        for ch in div3:
            if tag(ch) != "p":
                continue
            ch_children = list(ch)
            non_pb = [c for c in ch_children if tag(c) != "pb"]
            if not non_pb or tag(non_pb[0]) != "hi":
                continue
            hi_text = "".join(non_pb[0].itertext())
            if _split_hi_text(hi_text) is not None:
                issues.append("Pattern 5 remnant: interior verse number in <hi>")
            break

    return issues


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report counts without writing output")
    ap.add_argument("--output", type=Path, metavar="PATH",
                    help="Write to PATH instead of overwriting the source file")
    ap.add_argument("--only-pattern", type=int, metavar="N", choices=[1, 2, 3, 4, 5],
                    help="Apply only the specified pattern fix (1–5)")
    args = ap.parse_args()

    parser = etree.XMLParser(remove_blank_text=False)
    tree   = etree.parse(str(INPUT), parser)
    root   = tree.getroot()

    only = args.only_pattern

    n3 = fix_pattern3(root) if only in (None, 3) else 0
    n2 = fix_pattern2(root) if only in (None, 2) else 0
    n1 = fix_pattern1(root) if only in (None, 1) else 0
    n4 = fix_pattern4(root) if only in (None, 4) else 0
    n5 = fix_pattern5(root) if only in (None, 5) else 0

    print(f"Pattern 3 (div3-in-emph):              {n3:3d} fixed")
    print(f"Pattern 2 (bare hi in div3):            {n2:3d} fixed")
    print(f"Pattern 1 (flat verse p → div3):       {n1:3d} fixed")
    print(f"Pattern 4 (split multi-verse div3):    {n4:3d} new div3s created")
    print(f"Pattern 5 (split mid-hi verse number): {n5:3d} splits")

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
