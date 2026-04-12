"""Fix TEI XML files where CAPUT headings are embedded in <p> elements.

In some Corpus Corporum TEI files, chapter divisions (CAPUT I, II, etc.)
appear as text at the start of <p> elements rather than as proper
<div2>/<div3> structure.  Existing <div3> wrappers incorrectly span
chapter boundaries.

This script restructures each <div1> so that:
- Each CAPUT becomes a <div2> with a <head>
- Each numbered section becomes a <div3> with a <head>
- Paragraph content is preserved verbatim

Usage::

    python scripts/fix_caput_structure.py INPUT.xml OUTPUT.xml
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Match CAPUT prefix at the start of a <p> element's inner text.
#   Group 1: leading whitespace + optional <pb.../>
#   Group 2: chapter identifier (Roman numeral or ordinal like PRIMUM)
#   Group 3: section number (digits), or None
#   Group 4: remaining paragraph text
_CAPUT_RE = re.compile(
    r"^(\s*(?:<pb\s[^/]*/>\s*)?)"     # (1) optional leading <pb/>
    r"CAPUT\.?\s+([A-Z]+)\.?"          # (2) CAPUT + id + optional "."
    r"(?:\s*--\s*|\s+)"               # separator: "--" or plain whitespace
    r"(?:(\d+)\.\s*)?"                 # (3) optional section number
    r"(.*)",                           # (4) rest of paragraph
    re.DOTALL,
)

# Extract attributes and inner content of a <p> element.
_P_INNER_RE = re.compile(r"^<p\b([^>]*)>(.*)</p>$", re.DOTALL)

# Extract a <pb .../> element from text.
_PB_RE = re.compile(r"(<pb\s[^/]*/\s*>)")

# Extract inner content of a <head> element.
_HEAD_INNER_RE = re.compile(r"^<head\b[^>]*>(.*?)</head>$", re.DOTALL)

# Tokenise flat div1 body into top-level elements.
_ELEM_RE = re.compile(
    r"(<head\b[^>]*>.*?</head>)"
    r"|(<p\b[^>]*>.*?</p>)"
    r"|(<pb\b[^/]*/\s*>)",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _fix_div1_body(body: str) -> str:
    """Restructure the body of a <div1> (everything between book head and closing tag).

    Strips wrong <div3> wrappers, identifies CAPUT boundaries,
    and rebuilds with proper <div2>/<div3> nesting.
    """
    # Phase 1 — flatten: remove existing <div3> and </div3> tags.
    body = re.sub(r"\s*</?div3>\s*", "\n", body)

    # Phase 2 — tokenise into (type, text) pairs.
    tokens: list[tuple[str, str]] = []
    for m in _ELEM_RE.finditer(body):
        if m.group(1):
            tokens.append(("head", m.group(1)))
        elif m.group(2):
            tokens.append(("p", m.group(2)))
        elif m.group(3):
            tokens.append(("pb", m.group(3)))

    # Phase 3 — rebuild with correct div2/div3 nesting.
    out: list[str] = []
    div2_open = False
    div3_open = False
    pending_pb: list[str] = []        # <pb> seen before first div2

    def _close_div3() -> None:
        nonlocal div3_open
        if div3_open:
            out.append("        </div3>")
            div3_open = False

    def _close_div2() -> None:
        nonlocal div2_open
        _close_div3()
        if div2_open:
            out.append("        </div2>")
            div2_open = False

    for tok_type, tok_text in tokens:
        # --- <pb/> --------------------------------------------------------
        if tok_type == "pb":
            if not div2_open:
                pending_pb.append(tok_text)
            else:
                out.append(tok_text)
            continue

        # --- <head> (section head from former div3) -----------------------
        if tok_type == "head":
            hm = _HEAD_INNER_RE.match(tok_text)
            if hm:
                _close_div3()
                out.append("          <div3>")
                out.append(f"            <head>{hm.group(1)}</head>")
                div3_open = True
            continue

        # --- <p> ----------------------------------------------------------
        if tok_type == "p":
            pm = _P_INNER_RE.match(tok_text)
            if not pm:
                # Malformed — keep as-is.
                out.append(tok_text)
                continue

            p_attrs, p_body = pm.group(1), pm.group(2)
            cm = _CAPUT_RE.match(p_body)

            if cm:
                leading, chap_id, sec_num, rest = cm.groups()

                # Extract <pb/> from leading content (to hoist to div2 level).
                pb_el = None
                if leading:
                    pb_m = _PB_RE.search(leading)
                    if pb_m:
                        pb_el = pb_m.group(1)

                _close_div2()

                # Open new <div2>.
                out.append("        <div2>")
                for pb in pending_pb:
                    out.append(f"          {pb}")
                pending_pb.clear()
                if pb_el:
                    out.append(f"          {pb_el}")
                out.append(f"          <head>CAPUT {chap_id}.</head>")
                div2_open = True

                # Open <div3> if a section number was present.
                if sec_num:
                    out.append("          <div3>")
                    out.append(f"            <head>{sec_num}.</head>")
                    div3_open = True

                # Emit paragraph with remaining text (CAPUT prefix stripped).
                rest = rest.rstrip() if rest else ""
                if rest:
                    indent = "            " if div3_open else "          "
                    out.append(f"{indent}<p{p_attrs}>{rest}</p>")
            else:
                # Regular paragraph — preserve verbatim.
                out.append(tok_text)

    _close_div2()
    return "\n".join(out)


def fix_file(text: str) -> str:
    """Fix all <div1> blocks in *text* that contain CAPUT-in-<p> issues."""
    # Locate <div1>...</div1> blocks (non-nested in these files).
    starts = [m for m in re.finditer(r"<div1>", text)]
    ends = [m for m in re.finditer(r"</div1>", text)]

    if len(starts) != len(ends):
        print("Warning: mismatched <div1>/<div1> tags", file=sys.stderr)
        return text

    result: list[str] = []
    pos = 0

    for s_match, e_match in zip(starts, ends):
        # Copy everything before this <div1>.
        result.append(text[pos:s_match.start()])

        div1 = text[s_match.start():e_match.end()]

        # Locate book <head>...</head>.
        head_m = re.match(
            r"(<div1>\s*<head\b[^>]*>.*?</head>)", div1, re.DOTALL,
        )
        # Locate </div1> and its leading whitespace.
        suffix_m = re.search(r"\s*</div1>$", div1)

        if not head_m or not suffix_m:
            result.append(div1)
            pos = e_match.end()
            continue

        body = div1[head_m.end():suffix_m.start()]

        # Only transform if CAPUT-in-<p> is present.
        if not re.search(
            r"<p\b[^>]*>\s*(?:<pb\s[^/]*/>\s*)?CAPUT", body,
        ):
            result.append(div1)
            pos = e_match.end()
            continue

        new_body = _fix_div1_body(body)
        result.append(head_m.group(1) + "\n" + new_body + suffix_m.group())
        pos = e_match.end()

    result.append(text[pos:])
    return "".join(result)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} INPUT.xml OUTPUT.xml", file=sys.stderr)
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    text = in_path.read_text(encoding="utf-8")
    fixed = fix_file(text)
    out_path.write_text(fixed, encoding="utf-8")

    print(f"  {in_path}")
    print(f"    -> {out_path}")


if __name__ == "__main__":
    main()
