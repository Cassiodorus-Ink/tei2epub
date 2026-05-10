#!/usr/bin/env python3
"""Run conformance checks on TEI XML files.

Thin runner over :mod:`tei2epub.checks`.  All check logic lives in the
package; this script just walks the filesystem and formats output.

Usage::

    poetry run python scripts/check_corpus.py [options] [path ...]

By default runs every category over the entire default corpus.  Pass
specific paths (files or directories) to restrict the scan, and any
combination of ``--coverage``, ``--text-loss``, ``--structure`` to
restrict the categories.

Categories:
  --coverage    CV-1: parser does not handle a tag in its parent context
  --text-loss   TL-1..TL-6: known encoding faults that drop text
  --structure   ST-1..ST-2: structural anti-patterns

Exit code is 0 when no findings, 1 when there are findings or fatal
errors.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from tei2epub.checks import CATEGORIES, Finding, check_file
from tei2epub.checks.catalog import BY_CODE

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_CORPUS = _SCRIPT_DIR / ".." / ".." / "corpus" / "xml"


def _gather_xml_files(paths: list[Path]) -> list[Path]:
    """Expand a mix of files and directories into a sorted file list."""
    files: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_dir():
            files.extend(sorted(p.rglob("*.xml")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"warning: not found: {p}", file=sys.stderr)
    return files


def _wrap_description(description: str, width: int = 72) -> list[str]:
    """Collapse whitespace, then wrap to *width* chars for the report header."""
    return textwrap.wrap(" ".join(description.split()), width=width) or [""]


def _print_findings(rel: Path, findings: list[Finding], verbose: bool) -> None:
    print(f"\n{rel}")
    limit = None if verbose else 5
    for i, f in enumerate(findings):
        if limit is not None and i >= limit:
            print(f"  … and {len(findings) - limit} more finding(s) "
                  f"(use --verbose to see all)")
            break
        print(f"  [{f.check_id}] {f.message}")
        if f.excerpt:
            print(f"         at {f.location}")
            print(f"         \"{f.excerpt}\"")


def _write_report(
    out_path: Path,
    cwd: Path,
    findings: list[Finding],
    n_checked: int,
    categories: tuple[str, ...],
) -> None:
    """Write a Vim-friendly report.

    Layout::

        # tei2epub corpus check report
        # generated 2026-...
        # checked N file(s); M file(s) with findings
        # categories: ...
        #
        # Summary
        # -------
        # CV-1  18888 findings  151 files
        # ...
        #
        # Each entry below uses:  path:line: [CODE] message
        # so editors can jump with `gf`, `:cfile`, or quickfix.

        ## relative/path/to/file.xml  (N findings)
        relative/path/to/file.xml:123: [CV-1] unhandled <i> inside <p>
            at body[0]/div1[0]/p[3]/i[0]
            "italicised text..."

    Paths are emitted relative to *cwd* so that `gf` works when the
    report is opened from that directory.
    """
    files: dict[Path, list[Finding]] = defaultdict(list)
    for f in findings:
        files[f.path].append(f)
    counts: dict[str, int] = defaultdict(int)
    files_per: dict[str, set[Path]] = defaultdict(set)
    for f in findings:
        counts[f.check_id] += 1
        files_per[f.check_id].add(f.path)

    def relpath(p: Path) -> str:
        try:
            return os.path.relpath(p, cwd)
        except ValueError:
            return str(p)

    with out_path.open("w", encoding="utf-8") as fp:
        fp.write("# tei2epub corpus check report\n")
        fp.write(f"# generated {datetime.now().isoformat(timespec='seconds')}\n")
        fp.write(f"# checked {n_checked} file(s); "
                 f"{len(files)} file(s) with findings\n")
        fp.write(f"# categories: {', '.join(categories)}\n")
        fp.write("#\n# Summary\n# -------\n")
        if counts:
            fp.write(f"# {'Check':<8} {'Findings':>10} {'Files':>8}  Title\n")
            for cid in sorted(counts):
                spec = BY_CODE.get(cid)
                title = spec.title if spec else ""
                fp.write(f"# {cid:<8} {counts[cid]:>10} "
                         f"{len(files_per[cid]):>8}  {title}\n")
            fp.write("#\n# Check descriptions\n# ------------------\n")
            for cid in sorted(counts):
                spec = BY_CODE.get(cid)
                if spec is None:
                    continue
                fp.write(f"# {spec.code}: {spec.title}\n")
                for line in _wrap_description(spec.description):
                    fp.write(f"#   {line}\n")
                fp.write("#\n")
        else:
            fp.write("# (no findings)\n")
        fp.write("# Each entry uses  path:line: [CODE] message\n")
        fp.write("# Open in Vim and use `gf` on a path, or `:cfile` for quickfix.\n")
        fp.write("\n")

        for path in sorted(files):
            rp = relpath(path)
            fs = files[path]
            fp.write(f"\n## {rp}  ({len(fs)} finding(s))\n")
            for f in fs:
                line = f.line or 1
                fp.write(f"{rp}:{line}: [{f.check_id}] {f.message}\n")
                if f.location:
                    fp.write(f"    at {f.location}\n")
                if f.excerpt:
                    fp.write(f'    "{f.excerpt}"\n')


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--coverage", action="store_true",
                    help="Run CV-1 (parser tag coverage)")
    ap.add_argument("--text-loss", action="store_true",
                    help="Run TL-1..TL-6 (text-loss anti-patterns)")
    ap.add_argument("--structure", action="store_true",
                    help="Run ST-1..ST-2 (structural anti-patterns)")
    ap.add_argument("--verbose", action="store_true",
                    help="Show all findings; default suppresses after 5 per file")
    ap.add_argument("--report", type=Path, metavar="FILE",
                    help="Write a full Vim-friendly report to FILE "
                         "(path:line: format; suppresses per-file stdout)")
    ap.add_argument("paths", nargs="*", type=Path,
                    help="Files or directories to check (default: corpus root)")
    args = ap.parse_args()

    selected: list[str] = []
    if args.coverage:
        selected.append("coverage")
    if args.text_loss:
        selected.append("text_loss")
    if args.structure:
        selected.append("structure")
    categories = tuple(selected) if selected else CATEGORIES

    paths = args.paths or [_DEFAULT_CORPUS]
    xml_files = _gather_xml_files(paths)
    if not xml_files:
        print("No XML files found.", file=sys.stderr)
        sys.exit(1)

    # For relative-path display, use the first directory argument as
    # base if there is one; otherwise fall back to the file's parent.
    base = next((p.resolve() for p in paths if p.is_dir()), None)

    all_findings: list[Finding] = []
    n_checked = 0

    for path in xml_files:
        file_findings = check_file(path, categories)
        if file_findings and not args.report:
            rel = path.relative_to(base) if base else path.name
            _print_findings(Path(rel), file_findings, args.verbose)
        all_findings.extend(file_findings)
        n_checked += 1

    if args.report:
        _write_report(
            args.report.resolve(),
            Path.cwd(),
            all_findings,
            n_checked,
            categories,
        )
        print(f"\nReport written to {args.report}")

    counts: dict[str, int] = defaultdict(int)
    files_affected: dict[str, set[Path]] = defaultdict(set)
    for f in all_findings:
        counts[f.check_id] += 1
        files_affected[f.check_id].add(f.path)

    print(f"\n{'─' * 60}")
    print(f"Checked {n_checked} file(s).  "
          f"{len({f.path for f in all_findings})} file(s) with findings.")
    print(f"Categories: {', '.join(categories)}\n")

    if counts:
        print(f"{'Check':<8} {'Findings':>10} {'Files':>8}  Title")
        print(f"{'─'*8}  {'─'*10}  {'─'*8}  {'─'*40}")
        for check_id in sorted(counts):
            spec = BY_CODE.get(check_id)
            title = spec.title if spec else ""
            print(f"{check_id:<8} {counts[check_id]:>10} "
                  f"{len(files_affected[check_id]):>8}  {title}")
        sys.exit(1)
    else:
        print("No findings.")


if __name__ == "__main__":
    main()
