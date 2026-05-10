"""Structural anti-pattern checks (ST-*).

These detect higher-level structural defects that don't lose text but
produce wrong-looking output (e.g. headings in the wrong place, the
book title appearing as a chapter).
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from ._common import (
    Finding,
    elem_location,
    excerpt,
    localname,
    ns,
    text_content,
)
from .catalog import ST_1, ST_2


# ── ST-1: CAPUT heading embedded in <p> text ────────────────────────────────

# A <p> that begins (after optional <pb/>) with the word CAPUT followed
# by Roman-numeral / ordinal characters — i.e. a chapter heading that
# was encoded inline rather than wrapped in <div2><head>.
_CAPUT_PATTERN = re.compile(r"^\s*CAPUT\b", re.IGNORECASE | re.UNICODE)


def _check_st1(body: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    for p in body.iter(ns("p")):
        # Strip any leading <pb/> when looking at the head text.
        head_text = (p.text or "").lstrip()
        if not head_text:
            for child in p:
                if localname(child) == "pb":
                    head_text = (child.tail or "").lstrip()
                break
        if _CAPUT_PATTERN.match(head_text):
            findings.append(Finding(
                check_id=ST_1.code,
                message="<p> begins with CAPUT — chapter heading encoded inline rather than as <div2><head>",
                location=elem_location(p),
                excerpt=excerpt(p),
                path=Path(),
                line=p.sourceline or 0,
            ))
    return findings


# ── ST-2: single wrapping <div1> ────────────────────────────────────────────

def _check_st2(body: etree._Element) -> list[Finding]:
    """ST-2: <body> contains exactly one <div1> whose <head> matches the
    work title — produces a TOC where the book title appears as a
    chapter.  Triage required: not always a defect."""
    div1s = body.findall(ns("div1"))
    if len(div1s) != 1:
        return []
    div1 = div1s[0]
    head = div1.find(ns("head"))
    if head is None:
        return []
    head_text = text_content(head)
    if not head_text:
        return []
    return [Finding(
        check_id=ST_2.code,
        message=f'single wrapping <div1> with head "{head_text[:60]}" — book title may render as a chapter',
        location=elem_location(div1),
        excerpt=head_text[:72] + ("…" if len(head_text) > 72 else ""),
        path=Path(),
        line=div1.sourceline or 0,
    )]


def check(root: etree._Element, body: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_check_st1(body))
    findings.extend(_check_st2(body))
    return findings
