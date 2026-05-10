"""Conformance checks for TEI XML files consumed by tei2epub.

This package is the library half of the corpus checker.  The thin
runner ``scripts/check_corpus.py`` walks a corpus directory and calls
:func:`check_file` on each XML file.

Categories:
  * ``coverage``    — CV-1: tags the parser doesn't handle, by parent context
  * ``text_loss``   — TL-1..TL-7: known encoding faults that drop text
  * ``structure``   — ST-1..ST-2: structural anti-patterns

When parse.py grows new tag support, update :mod:`tei2epub.coverage`
in the same change so CV-1 stays accurate.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

from . import coverage as _coverage
from . import structure as _structure
from . import text_loss as _text_loss
from ._common import Finding, ns

__all__ = ["Finding", "check_file", "CATEGORIES"]


CATEGORIES = ("coverage", "text_loss", "structure")


def check_file(
    path: Path,
    categories: tuple[str, ...] = CATEGORIES,
) -> list[Finding]:
    """Run the requested checks on a single TEI XML file.

    Returns a (possibly empty) list of :class:`Finding`.  XML parse
    errors are reported on stderr and produce an empty result list.

    Pass a subset of :data:`CATEGORIES` to skip categories.
    """
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        print(f"  PARSE ERROR {path.name}: {exc}", file=sys.stderr)
        return []

    root = tree.getroot()
    body = root.find(f".//{ns('body')}")
    if body is None:
        return []

    findings: list[Finding] = []
    if "coverage" in categories:
        findings.extend(_coverage.check(body))
    if "text_loss" in categories:
        findings.extend(_text_loss.check(root, body))
    if "structure" in categories:
        findings.extend(_structure.check(root, body))

    for f in findings:
        f.path = path
    return findings
