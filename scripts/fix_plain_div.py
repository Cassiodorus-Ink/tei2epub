"""fix_plain_div.py — Fix TEI XML files that use plain <div> instead of <div1>/<div2>.

Some Corpus Corporum texts (e.g. Benedictus Nursiae, Regula cum commentariis)
have all their content as flat <div> children of <body>, rather than the
standard <div1>/<div2>/<div3> hierarchy expected by tei2epub.

This script:
  1. Wraps the <body> children in a single <div1>.
  2. Renames each top-level <div> to <div2>.

Only top-level <div> elements directly under <body> are touched; any <div>
elements nested inside them (if they exist) are left alone.

Usage:
    python scripts/fix_plain_div.py INPUT.xml OUTPUT.xml
"""

import re
import sys


def fix_file(text: str) -> str:
    # Locate <body> ... </body>
    body_open = re.search(r"<body\b[^>]*>", text)
    body_close = re.search(r"</body\s*>", text)
    if not body_open or not body_close:
        raise ValueError("No <body>...</body> found")

    before = text[: body_open.end()]
    body_content = text[body_open.end() : body_close.start()]
    after = text[body_close.start() :]

    # Sanity check: file must have plain <div> directly under body and no <div1>.
    if not re.search(r"<div\b", body_content):
        raise ValueError("No <div> elements found in <body>")
    if re.search(r"<div1\b", body_content):
        raise ValueError("<div1> already present — file may not need this fix")

    # Rename top-level <div> / </div> to <div2> / </div2>.
    # "Top-level" means not nested inside another element.  Since these files
    # have no <div> nesting (each <div> closes before the next opens), a simple
    # linear scan with a depth counter suffices.
    depth = 0
    result = []
    pos = 0
    tag_re = re.compile(r"<(/?)div(\b[^>]*)>")
    for m in tag_re.finditer(body_content):
        result.append(body_content[pos : m.start()])
        closing = m.group(1)  # "/" if closing tag
        attrs = m.group(2)
        if closing:
            depth -= 1
            if depth == 0:
                result.append("</div2>")
            else:
                result.append(f"</div{attrs}>")
        else:
            if depth == 0:
                result.append(f"<div2{attrs}>")
            else:
                result.append(f"<div{attrs}>")
            depth += 1
        pos = m.end()
    result.append(body_content[pos:])

    fixed_body = "".join(result)

    # Determine indentation of the first <div2> to use for the <div1> wrapper.
    first_div2 = re.search(r"^(\s*)<div2\b", fixed_body, re.MULTILINE)
    indent = first_div2.group(1) if first_div2 else ""
    # Use one level less indentation for <div1>; if none, use empty string.
    outer_indent = indent[: max(0, len(indent) - 1)]

    wrapped = f"\n{outer_indent}<div1>\n{fixed_body}\n{outer_indent}</div1>\n"

    return before + wrapped + after


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} INPUT.xml OUTPUT.xml", file=sys.stderr)
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    with open(in_path, encoding="utf-8") as f:
        text = f.read()

    fixed = fix_file(text)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(fixed)

    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
