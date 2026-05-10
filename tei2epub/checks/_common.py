"""Shared helpers and the Finding dataclass for tei2epub.checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lxml import etree

TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def ns(name: str) -> str:
    return f"{{{TEI_NS}}}{name}"


def localname(el: etree._Element) -> str:
    t = el.tag
    return t.split("}", 1)[1] if isinstance(t, str) and t.startswith("{") else t


def text_content(el: etree._Element) -> str:
    return "".join(el.itertext()).strip()


def excerpt(el: etree._Element, max_len: int = 72) -> str:
    text = " ".join(text_content(el).split())
    return text[:max_len] + ("…" if len(text) > max_len else "")


def is_trivial(el: etree._Element) -> bool:
    """True if an element is empty, all whitespace, only a page-break
    marker, or a bare digit (edition column number)."""
    text = text_content(el)
    if not text:
        return True
    if text.isdigit():
        return True
    children = list(el)
    non_pb = [c for c in children if localname(c) != "pb"]
    if not non_pb and not (el.text or "").strip():
        return True
    return False


def elem_location(el: etree._Element) -> str:
    """A compact path like body/div1[1]/div2[3]/p[0] for diagnostics."""
    parts: list[str] = []
    node: etree._Element | None = el
    while node is not None:
        parent = node.getparent()
        t = localname(node)
        if parent is not None:
            siblings = [c for c in parent if c.tag == node.tag]
            idx = siblings.index(node)
            parts.append(f"{t}[{idx}]")
        else:
            parts.append(t)
        node = parent
    parts.reverse()
    for i, p in enumerate(parts):
        if p.startswith(("body", "div1", "div2", "div3")):
            return "/".join(parts[i:])
    return "/".join(parts)


@dataclass
class Finding:
    """A single conformance issue.

    ``check_id`` is a stable short code (e.g. ``"CV-1"``, ``"TL-1"``)
    that lets reports group, count, and reference findings.  Codes
    follow the convention: ``CV-`` coverage, ``TL-`` text-loss,
    ``ST-`` structural.

    ``line`` is the source line number from lxml (1-based), or 0 if
    not available.  Used in reports to emit ``path:line:`` so editors
    can jump directly to the offending element.
    """
    check_id: str
    message: str
    location: str
    excerpt: str
    path: Path
    line: int = 0
