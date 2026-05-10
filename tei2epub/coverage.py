"""Tag allowlists describing what tei2epub's parser handles.

Single source of truth for the conformance checker.  When parse.py
gains support for a new tag, update the relevant allowlist here in
the same change so the coverage check stays accurate.

Each entry in :data:`PARENT_ALLOWLISTS` says "for child elements
appearing directly under a parent of this local name, this is the set
of child local names the parser walks".  An element appearing under a
listed parent whose tag is not in the corresponding allowlist is
silently flattened, dropped, or otherwise unhandled — the coverage
check flags it.

Parents not listed in :data:`PARENT_ALLOWLISTS` are treated as
out-of-scope and not enforced.  In particular, ``<teiHeader>`` and
its children are handled by a small bespoke metadata extractor and
not by the structural walker, so we don't enforce children there.
"""

from __future__ import annotations


# Inline tags accepted by _parse_inline / _inline_from_child.
INLINE_TAGS: frozenset[str] = frozenset({
    "hi", "b", "i", "emph", "note", "pb", "foreign", "l", "lg",
    "B", "LACUNA",
})

# Children of <p>: inline tags plus <list>, which triggers the
# option-(a) block split in _parse_p_to_blocks.
P_CHILDREN: frozenset[str] = INLINE_TAGS | frozenset({"list"})

# Block tags accepted directly under <div1> (also as preamble before
# the first <div2>, and in the flat-work path where <div3> sits
# directly inside <div1>).
BLOCK_TAGS_DIV1: frozenset[str] = frozenset({
    "head", "div1", "div2", "div3", "p", "P", "pb", "list", "l", "lg",
})

# Block tags accepted directly under <div2>.
BLOCK_TAGS_DIV2: frozenset[str] = frozenset({
    "head", "div3", "p", "P", "pb", "list", "l", "lg",
})

# Block tags accepted directly under <div3>.
BLOCK_TAGS_DIV3: frozenset[str] = frozenset({
    "head", "p", "P", "pb", "list", "l", "lg",
})

# Children of <body> in fallback parse paths (no <div1>, or <note>
# wrapper, etc.).  We allow anything the parser tries to find here.
BODY_CHILDREN: frozenset[str] = frozenset({
    "div1", "div2", "div3", "p", "P", "pb", "head",
    "front", "back", "note", "list", "l", "lg",
})

# Children of <list>: the parser recognises only <head> (caption) and
# <item> (entry).  Other children are silently ignored.
LIST_CHILDREN: frozenset[str] = frozenset({"head", "item"})

# Children of <lg> (verse group).
LG_CHILDREN: frozenset[str] = frozenset({"l"})


PARENT_ALLOWLISTS: dict[str, frozenset[str]] = {
    "body":    BODY_CHILDREN,
    "div1":    BLOCK_TAGS_DIV1,
    "div2":    BLOCK_TAGS_DIV2,
    "div3":    BLOCK_TAGS_DIV3,
    "p":       P_CHILDREN,
    "P":       P_CHILDREN,
    "list":    LIST_CHILDREN,
    "lg":      LG_CHILDREN,
    "head":    INLINE_TAGS,
    "l":       INLINE_TAGS,
    "item":    INLINE_TAGS,
    "hi":      INLINE_TAGS,
    "b":       INLINE_TAGS,
    "i":       INLINE_TAGS,
    "emph":    INLINE_TAGS,
    "foreign": INLINE_TAGS,
}
