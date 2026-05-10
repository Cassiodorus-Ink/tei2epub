"""Coverage check: report XML elements whose tag isn't handled by the parser.

CV-1 walks every element under <body> and looks at its parent context.
If the parent is one whose handled-children set is enumerated in
:mod:`tei2epub.coverage`, and the element's tag is *not* in that set,
the element is flagged — its content will be flattened, dropped, or
silently ignored by the parser.

This subsumes the old TL-7 check (unknown direct children of <div2>
and <div3>) and extends it to inline contexts (<p>, <hi>, <emph>,
<foreign>, <head>, <l>, <item>) and to <list> and <lg>.

Parents not enumerated in :data:`tei2epub.coverage.PARENT_ALLOWLISTS`
(e.g. <teiHeader>, <front>, <back>, <note>) are out of scope for this
check.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from tei2epub.coverage import PARENT_ALLOWLISTS

from ._common import (
    Finding,
    elem_location,
    excerpt as _excerpt,
    is_trivial,
    localname,
)
from .catalog import CV_1


def check(body: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    for elem in body.iter():
        parent = elem.getparent()
        if parent is None:
            continue
        parent_tag = localname(parent)
        allowed = PARENT_ALLOWLISTS.get(parent_tag)
        if allowed is None:
            continue
        tag = localname(elem)
        if tag in allowed:
            continue
        if is_trivial(elem):
            continue
        findings.append(Finding(
            check_id=CV_1.code,
            message=(f"unhandled <{tag}> inside <{parent_tag}> "
                     f"(content not parsed by tei2epub)"),
            location=elem_location(elem),
            excerpt=_excerpt(elem),
            path=Path(),
            line=elem.sourceline or 0,
        ))
    return findings
