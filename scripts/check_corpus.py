#!/usr/bin/env python3
"""
check_corpus.py — Validate TEI XML corpus files for encoding problems.

Usage::

    poetry run python scripts/check_corpus.py [--text-loss] [--structure]
                                               [--verbose] [corpus_dir]

At least one of --text-loss or --structure must be specified.

corpus_dir defaults to ../../corpus/xml relative to this script.

Anti-patterns checked (see corpus/anti-patterns.md for full descriptions):

  Text-loss (--text-loss):
    TL-1  Direct <p> children of <div1> between or after <div2> siblings
    TL-2  Block-level <div3> nested inside inline context (<emph> or <p>)
    TL-3  Bare <hi> as direct child of <div3> (not wrapped in <p>)
    TL-5  Plain <div> elements directly under <body> (not div1/div2/div3)
    TL-6  Parallel-language siblings (div1 with xml:lang differing from
          the document's declared language)
    TL-7  Unknown block-level elements with non-trivial text content
          (direct children of <div2> or <div3> not in the expected tag set)

    Note: TL-4 (<emph> child elements silently dropped) was fixed in
    tei2epub in April 2026 and is no longer a runtime text-loss issue.
    It is not checked here but may be worth auditing for corpus cleanliness.

    The following corpus-specific encodings are now handled by tei2epub
    and are therefore excluded from TL-7 findings:
      <P>     treated as <p> (uppercase encoding error)
      <B>     dropped as edition page reference (like digit-only <emph>)
      <b>     treated as <hi>
      <LACUNA> rendered as [...] (empty) or [text] (with content)

  Structural (--structure):
    Not yet implemented (see skeleton below).
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

_TEI_NS  = "http://www.tei-c.org/ns/1.0"
_XML_NS  = "http://www.w3.org/XML/1998/namespace"

_SCRIPT_DIR     = Path(__file__).resolve().parent
_DEFAULT_CORPUS = _SCRIPT_DIR / ".." / ".." / "corpus" / "xml"

# Tags that _parse_blocks accepts at block level inside <div2>.
# "P" is an uppercase encoding error treated as <p> by tei2epub.
_KNOWN_DIV2_CHILDREN = frozenset({"head", "pb", "p", "P", "l", "lg", "div3"})

# Tags that _parse_blocks accepts at block level inside <div3>.
_KNOWN_DIV3_CHILDREN = frozenset({"head", "pb", "p", "P", "l", "lg"})

# Tags that _parse_inline accepts as inline elements.
# "B" (dropped as edition page ref), "b" (→ <hi>), and "LACUNA" (→ [...])
# are corpus-specific encodings handled by tei2epub.
_KNOWN_INLINE_TAGS = frozenset({
    "hi", "emph", "note", "pb", "foreign", "l", "lg",
    "B", "b", "LACUNA",
})


# ── helpers ──────────────────────────────────────────────────────────────────

def _ns(name: str) -> str:
    return f"{{{_TEI_NS}}}{name}"


def _localname(el: etree._Element) -> str:
    t = el.tag
    return t.split("}", 1)[1] if t.startswith("{") else t


def _text_content(el: etree._Element) -> str:
    return "".join(el.itertext()).strip()


def _excerpt(el: etree._Element, max_len: int = 72) -> str:
    text = " ".join(_text_content(el).split())
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _is_trivial(el: etree._Element) -> bool:
    """True if an element contains only page-break markers, whitespace, or
    bare digit strings (edition column numbers)."""
    text = _text_content(el)
    if not text:
        return True
    if text.isdigit():
        return True
    children = list(el)
    non_pb = [c for c in children if _localname(c) != "pb"]
    if not non_pb and not (el.text or "").strip():
        return True  # only <pb/> children, no direct text
    return False


def _elem_location(el: etree._Element) -> str:
    """Return a compact path like div1[1]/div2[3]/p[0] for error messages."""
    parts: list[str] = []
    node = el
    while node is not None:
        parent = node.getparent()
        t = _localname(node)
        if parent is not None:
            siblings = [c for c in parent if c.tag == node.tag]
            idx = siblings.index(node)
            parts.append(f"{t}[{idx}]")
        else:
            parts.append(t)
        node = parent
    parts.reverse()
    # Drop the leading TEI/text/body boilerplate; start from body or div1.
    for i, p in enumerate(parts):
        if p.startswith(("body", "div1", "div2", "div3")):
            return "/".join(parts[i:])
    return "/".join(parts)


# ── findings ─────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    check_id: str    # e.g. "TL-1"
    message: str     # one-line description
    location: str    # element path
    excerpt: str     # text context
    path: Path       # file path (set by caller)


# ── TL-1: direct <p> between/after <div2> siblings in <div1> ─────────────────

def _check_tl1(body: etree._Element) -> list[Finding]:
    """TL-1: <p> children of <div1> that are interstitial or postscript
    (i.e. appear between or after <div2> siblings).  Pre-div2 preamble is
    handled correctly by the parser and is not flagged."""
    findings: list[Finding] = []

    for div1 in body.iter(_ns("div1")):
        children = list(div1)
        div2s = div1.findall(_ns("div2"))
        if not div2s:
            continue

        total_div2 = len(div2s)
        seen_div2 = 0

        for child in children:
            t = _localname(child)
            if t == "div2":
                seen_div2 += 1
            elif t == "p" and seen_div2 > 0 and not _is_trivial(child):
                position = "between" if seen_div2 < total_div2 else "after"
                head = div1.find(_ns("head"))
                head_text = _excerpt(head, 50) if head is not None else "(no head)"
                findings.append(Finding(
                    check_id="TL-1",
                    message=(f"<p> {position} <div2> siblings in <div1> "
                             f'"{head_text}"'),
                    location=_elem_location(child),
                    excerpt=_excerpt(child),
                    path=Path(),
                ))

    return findings


# ── TL-2: block-level <div3> inside inline context ───────────────────────────

def _check_tl2(body: etree._Element) -> list[Finding]:
    """TL-2: <div3> whose parent is <emph> or <p> — block content trapped
    inside inline markup and invisible to the block-level parser."""
    findings: list[Finding] = []

    for div3 in body.iter(_ns("div3")):
        parent = div3.getparent()
        if parent is None:
            continue
        pt = _localname(parent)
        if pt in ("emph", "p"):
            hi = div3.find(_ns("hi"))
            excerpt = _excerpt(hi) if hi is not None else _excerpt(div3)
            findings.append(Finding(
                check_id="TL-2",
                message=f"<div3> nested inside <{pt}> (block content in inline context)",
                location=_elem_location(div3),
                excerpt=excerpt,
                path=Path(),
            ))

    return findings


# ── TL-3: bare <hi> as direct child of <div3> ────────────────────────────────

def _check_tl3(body: etree._Element) -> list[Finding]:
    """TL-3: <hi> is a direct child of <div3> rather than being wrapped in
    <p>.  _parse_blocks does not handle bare block-level <hi>."""
    findings: list[Finding] = []

    for div3 in body.iter(_ns("div3")):
        for child in div3:
            if _localname(child) == "hi" and not _is_trivial(child):
                findings.append(Finding(
                    check_id="TL-3",
                    message="bare <hi> as direct child of <div3> (not wrapped in <p>)",
                    location=_elem_location(child),
                    excerpt=_excerpt(child),
                    path=Path(),
                ))

    return findings


# ── TL-5: plain <div> elements directly under <body> ─────────────────────────

def _check_tl5(body: etree._Element) -> list[Finding]:
    """TL-5: plain unnumbered <div> elements directly under <body> that
    tei2epub does not recognise (expects div1/div2/div3)."""
    findings: list[Finding] = []

    for child in body:
        t = _localname(child)
        if t == "div" and not _is_trivial(child):
            findings.append(Finding(
                check_id="TL-5",
                message="plain <div> directly under <body> (not recognised by tei2epub)",
                location=_elem_location(child),
                excerpt=_excerpt(child),
                path=Path(),
            ))

    return findings


# ── TL-6: parallel-language <div1> siblings ──────────────────────────────────

def _check_tl6(root: etree._Element, body: etree._Element,
               declared_lang: str) -> list[Finding]:
    """TL-6: <div1> (or <div2>) siblings with an xml:lang attribute that
    differs from the document's declared language, indicating parallel-text
    content (e.g. Greek alongside Latin) that will be merged without
    labelling."""
    findings: list[Finding] = []
    lang_attr = f"{{{_XML_NS}}}lang"

    for parent in (body, root):
        for container in parent.iter():
            t = _localname(container)
            if t not in ("body", "div1", "div2"):
                continue
            langs_seen: dict[str, list[etree._Element]] = defaultdict(list)
            for child in container:
                lang = child.get(lang_attr)
                if lang:
                    langs_seen[lang].append(child)
            if len(langs_seen) > 1 or (langs_seen and declared_lang
                                        and declared_lang not in langs_seen):
                all_langs = sorted(langs_seen)
                for lang, els in langs_seen.items():
                    if lang != declared_lang:
                        el = els[0]
                        head = el.find(_ns("head"))
                        excerpt = _excerpt(head) if head is not None else ""
                        findings.append(Finding(
                            check_id="TL-6",
                            message=(f"<{_localname(el)}> with xml:lang={lang!r} "
                                     f"(document language: {declared_lang!r}); "
                                     f"parallel text: {all_langs}"),
                            location=_elem_location(el),
                            excerpt=excerpt,
                            path=Path(),
                        ))

    # Deduplicate (same location can appear from multiple passes).
    seen: set[str] = set()
    deduped: list[Finding] = []
    for f in findings:
        key = f.location
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped


# ── TL-7: unknown block-level elements with text content ─────────────────────

def _check_tl7(body: etree._Element) -> list[Finding]:
    """TL-7: elements at block level (direct children of <div2> or <div3>)
    that are not in the set the parser recognises, and that have non-trivial
    text content (so the text would be silently dropped)."""
    findings: list[Finding] = []

    for div2 in body.iter(_ns("div2")):
        for child in div2:
            t = _localname(child)
            if t not in _KNOWN_DIV2_CHILDREN and not _is_trivial(child):
                findings.append(Finding(
                    check_id="TL-7",
                    message=(f"unknown block-level element <{t}> in <div2> "
                             f"(text would be dropped)"),
                    location=_elem_location(child),
                    excerpt=_excerpt(child),
                    path=Path(),
                ))

    for div3 in body.iter(_ns("div3")):
        for child in div3:
            t = _localname(child)
            if t not in _KNOWN_DIV3_CHILDREN and not _is_trivial(child):
                findings.append(Finding(
                    check_id="TL-7",
                    message=(f"unknown block-level element <{t}> in <div3> "
                             f"(text would be dropped)"),
                    location=_elem_location(child),
                    excerpt=_excerpt(child),
                    path=Path(),
                ))

    return findings


# ── text-loss runner ─────────────────────────────────────────────────────────

def check_text_loss(path: Path, verbose: bool) -> list[Finding]:
    """Run all text-loss checks on a single file.  Returns a (possibly empty)
    list of findings."""
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        print(f"  PARSE ERROR {path.name}: {exc}", file=sys.stderr)
        return []

    root = tree.getroot()

    body = root.find(f".//{_ns('body')}")
    if body is None:
        return []

    # Determine declared language for TL-6.
    lang_el = root.find(f".//{_ns('language')}")
    declared_lang = lang_el.get("ident", "la") if lang_el is not None else "la"

    all_findings: list[Finding] = []
    all_findings.extend(_check_tl1(body))
    all_findings.extend(_check_tl2(body))
    all_findings.extend(_check_tl3(body))
    all_findings.extend(_check_tl5(body))
    all_findings.extend(_check_tl6(root, body, declared_lang))
    all_findings.extend(_check_tl7(body))

    for f in all_findings:
        f.path = path

    return all_findings


# ── structure checks (skeleton) ──────────────────────────────────────────────

def check_structure(path: Path, verbose: bool) -> list[Finding]:
    """Run structural checks on a single file.

    NOT YET IMPLEMENTED.

    Planned checks (see corpus/anti-patterns.md for full descriptions):

    ST-1  CAPUT headings embedded in <p> text
          Detection: regex match for <p> beginning (after optional <pb/>)
          with the word CAPUT followed by a Roman numeral or ordinal.
          Already implemented as a standalone script in check_caput_structure.py;
          fold that logic in here.

    ST-2  Single wrapping <div1> causes book title to appear as a chapter
          Detection: body has exactly one <div1> whose <head> text matches
          the work title from <teiHeader>.  Requires triage — not always
          a defect.  Already implemented in check_div1_wrap.py.

    ST-3  Verse citations not wrapped in <div3>
          Detection: within a <div2> that has no <div3> children, look for
          <p> elements whose sole child is <emph> or <hi> — strong signal
          of an unwrapped lemma in a commentary work.  Will produce false
          positives in non-commentary texts; may need a heuristic threshold
          (e.g. ≥3 such <p>s in one <div2>) or a per-author allowlist.

    ST-4  <head> child elements (inline structure silently flattened)
          Detection: any <head> element that has child elements (not just
          text).  Very common across the corpus (Rupertus Tuitiensis,
          Hugo de S. Victore, etc.) — likely too noisy to enable by default;
          consider a --pedantic flag.

    ST-5  Inconsistent use of <emph> vs <hi> for verse citations
          Detection: within a single <div2>, a mix of <p><emph><hi>…
          and <p><hi>… patterns.  Low priority; rendering is now identical
          after the April 2026 <emph> fix.
    """
    print("--structure: not yet implemented", file=sys.stderr)
    return []


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Validate TEI XML corpus files for encoding problems.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See corpus/anti-patterns.md for full pattern descriptions.",
    )
    ap.add_argument("--text-loss", action="store_true",
                    help="Check for content silently dropped by tei2epub")
    ap.add_argument("--structure", action="store_true",
                    help="Check for structural anti-patterns (not yet implemented)")
    ap.add_argument("--verbose", action="store_true",
                    help="Show all findings; default suppresses after 5 per file")
    ap.add_argument("corpus_dir", nargs="?", type=Path,
                    default=_DEFAULT_CORPUS,
                    help="Root directory of corpus XML files")
    args = ap.parse_args()

    if not args.text_loss and not args.structure:
        ap.error("specify at least one of --text-loss or --structure")

    corpus_dir = Path(args.corpus_dir).resolve()
    xml_files  = sorted(corpus_dir.rglob("*.xml"))
    if not xml_files:
        print(f"No XML files found under {corpus_dir}", file=sys.stderr)
        sys.exit(1)

    all_findings: list[Finding] = []
    n_checked = 0

    for path in xml_files:
        file_findings: list[Finding] = []

        if args.text_loss:
            file_findings.extend(check_text_loss(path, args.verbose))
        if args.structure:
            file_findings.extend(check_structure(path, args.verbose))

        if file_findings:
            rel = path.relative_to(corpus_dir)
            print(f"\n{rel}")
            limit = None if args.verbose else 5
            for i, f in enumerate(file_findings):
                if limit is not None and i >= limit:
                    remaining = len(file_findings) - limit
                    print(f"  … and {remaining} more finding(s) "
                          f"(use --verbose to see all)")
                    break
                print(f"  [{f.check_id}] {f.message}")
                if f.excerpt:
                    print(f"         at {f.location}")
                    print(f"         \"{f.excerpt}\"")

        all_findings.extend(file_findings)
        n_checked += 1

    # Summary
    counts: dict[str, int] = defaultdict(int)
    files_affected: dict[str, set[Path]] = defaultdict(set)
    for f in all_findings:
        counts[f.check_id] += 1
        files_affected[f.check_id].add(f.path)

    print(f"\n{'─' * 60}")
    print(f"Checked {n_checked} file(s).  "
          f"{len({f.path for f in all_findings})} file(s) with findings.\n")

    if counts:
        print(f"{'Check':<8} {'Findings':>10} {'Files':>8}")
        print(f"{'─'*8}  {'─'*10}  {'─'*8}")
        for check_id in sorted(counts):
            print(f"{check_id:<8} {counts[check_id]:>10} "
                  f"{len(files_affected[check_id]):>8}")
    else:
        print("No findings.")


if __name__ == "__main__":
    main()
