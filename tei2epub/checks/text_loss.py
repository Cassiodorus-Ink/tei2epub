"""Text-loss anti-pattern checks (TL-1..TL-7).

These detect specific encoding faults in the corpus that cause
tei2epub to silently drop real text or treat content out of context.
See ``corpus/anti-patterns.md`` for full descriptions.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from lxml import etree

from ._common import (
    Finding,
    XML_NS,
    elem_location,
    excerpt,
    is_trivial,
    localname,
    ns,
)
from .catalog import TL_1, TL_2, TL_3, TL_5, TL_6, TL_7


def _check_tl1(body: etree._Element) -> list[Finding]:
    """TL-1: <p> children of <div1> appearing between or after <div2>
    siblings (interstitial / postscript paragraphs that the multi-book
    parse path silently drops).  Pre-div2 preamble is fine."""
    findings: list[Finding] = []
    for div1 in body.iter(ns("div1")):
        div2s = div1.findall(ns("div2"))
        if not div2s:
            continue
        total = len(div2s)
        seen = 0
        for child in div1:
            tag = localname(child)
            if tag == "div2":
                seen += 1
            elif tag == "p" and seen > 0 and not is_trivial(child):
                position = "between" if seen < total else "after"
                head = div1.find(ns("head"))
                head_text = excerpt(head, 50) if head is not None else "(no head)"
                findings.append(Finding(
                    check_id=TL_1.code,
                    message=(f"<p> {position} <div2> siblings in <div1> "
                             f'"{head_text}"'),
                    location=elem_location(child),
                    excerpt=excerpt(child),
                    path=Path(),
                    line=child.sourceline or 0,
                ))
    return findings


def _check_tl2(body: etree._Element) -> list[Finding]:
    """TL-2: <div3> nested inside <emph> or <p> — block content
    trapped in inline context, invisible to the block-level parser."""
    findings: list[Finding] = []
    for div3 in body.iter(ns("div3")):
        parent = div3.getparent()
        if parent is None:
            continue
        pt = localname(parent)
        if pt in ("emph", "p"):
            hi = div3.find(ns("hi"))
            ex = excerpt(hi) if hi is not None else excerpt(div3)
            findings.append(Finding(
                check_id=TL_2.code,
                message=f"<div3> nested inside <{pt}> (block content in inline context)",
                location=elem_location(div3),
                excerpt=ex,
                path=Path(),
                line=div3.sourceline or 0,
            ))
    return findings


def _check_tl3(body: etree._Element) -> list[Finding]:
    """TL-3: bare <hi> as direct child of <div3> (not wrapped in
    <p>) — _parse_blocks doesn't handle bare block-level <hi>."""
    findings: list[Finding] = []
    for div3 in body.iter(ns("div3")):
        for child in div3:
            if localname(child) == "hi" and not is_trivial(child):
                findings.append(Finding(
                    check_id=TL_3.code,
                    message="bare <hi> as direct child of <div3> (not wrapped in <p>)",
                    location=elem_location(child),
                    excerpt=excerpt(child),
                    path=Path(),
                    line=child.sourceline or 0,
                ))
    return findings


def _check_tl5(body: etree._Element) -> list[Finding]:
    """TL-5: plain <div> directly under <body> (not div1/div2/div3)."""
    findings: list[Finding] = []
    for child in body:
        if localname(child) == "div" and not is_trivial(child):
            findings.append(Finding(
                check_id=TL_5.code,
                message="plain <div> directly under <body> (not recognised by tei2epub)",
                location=elem_location(child),
                excerpt=excerpt(child),
                path=Path(),
                line=child.sourceline or 0,
            ))
    return findings


def _check_tl6(root: etree._Element, body: etree._Element,
               declared_lang: str) -> list[Finding]:
    """TL-6: <div1>/<div2> siblings in different xml:lang values,
    indicating parallel-text content (e.g. Greek alongside Latin)
    that will be merged unlabelled."""
    findings: list[Finding] = []
    lang_attr = f"{{{XML_NS}}}lang"

    for parent in (body, root):
        for container in parent.iter():
            if localname(container) not in ("body", "div1", "div2"):
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
                        head = el.find(ns("head"))
                        ex = excerpt(head) if head is not None else ""
                        findings.append(Finding(
                            check_id=TL_6.code,
                            message=(f"<{localname(el)}> with xml:lang={lang!r} "
                                     f"(document language: {declared_lang!r}); "
                                     f"parallel text: {all_langs}"),
                            location=elem_location(el),
                            excerpt=ex,
                            path=Path(),
                            line=el.sourceline or 0,
                        ))

    seen: set[str] = set()
    deduped: list[Finding] = []
    for f in findings:
        if f.location not in seen:
            seen.add(f.location)
            deduped.append(f)
    return deduped


def _check_tl7(body: etree._Element) -> list[Finding]:
    """TL-7: <note> elements containing block-level structural children.

    The parser's Admonitio pattern only handles <note> when it is a
    direct child of <body> and wraps <div1>/<div2>/<div3>.  Any other
    <note> containing <div*>, <head>, <list>, or <lg> is flattened to
    plain text — structure is lost.
    """
    findings: list[Finding] = []
    _STRUCTURAL = frozenset({"div1", "div2", "div3", "head", "list", "lg"})

    for note in body.iter(ns("note")):
        # Determine if this note is handled by the Admonitio pattern.
        parent = note.getparent()
        is_body_child = parent is not None and localname(parent) == "body"
        has_div_wrapper = any(
            localname(child) in ("div1", "div2", "div3")
            for child in note
        )
        if is_body_child and has_div_wrapper:
            continue  # Handled by parse() Admonitio path.

        # Look for structural children.
        for child in note:
            tag = localname(child)
            if tag in _STRUCTURAL:
                findings.append(Finding(
                    check_id=TL_7.code,
                    message=(
                        f"<{tag}> inside <note> (structure will be flattened; "
                        f"use <front> for editorial prefaces)"
                    ),
                    location=elem_location(note),
                    excerpt=excerpt(note, max_len=120),
                    path=Path(),
                    line=note.sourceline or 0,
                ))
                break  # One finding per note is enough.

    return findings


def check(root: etree._Element, body: etree._Element) -> list[Finding]:
    lang_el = root.find(f".//{ns('language')}")
    declared_lang = lang_el.get("ident", "la") if lang_el is not None else "la"
    findings: list[Finding] = []
    findings.extend(_check_tl1(body))
    findings.extend(_check_tl2(body))
    findings.extend(_check_tl3(body))
    findings.extend(_check_tl5(body))
    findings.extend(_check_tl6(root, body, declared_lang))
    findings.extend(_check_tl7(body))
    return findings
