#!/usr/bin/env python3
"""Check relative links and image paths in README.md and docs/*.md (and service READMEs).

For every Markdown link or image `[text](target)`: skips http(s)/mailto targets, resolves the
path relative to the file, and requires it to exist; for `file.md#anchor` / `#anchor` it also
requires a heading with that GitHub-style slug. Fenced code blocks are ignored.
Exit 1 on any broken link. Usage: python scripts/check_links.py [files...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def strip_fences(text: str) -> list[str]:
    out, fenced = [], False
    for ln in text.splitlines():
        if FENCE_RE.match(ln):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else ln)
    return out


def slug(heading: str) -> str:
    h = re.sub(r"[`*_]", "", heading).strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out: set[str] = set()
    for ln in strip_fences(path.read_text()):
        m = HEAD_RE.match(ln)
        if m:
            s = slug(m.group(2))
            n = seen.get(s, 0)
            seen[s] = n + 1
            out.add(s if n == 0 else f"{s}-{n}")
    return out


def check(path: Path) -> list[str]:
    errs: list[str] = []
    for lineno, ln in enumerate(strip_fences(path.read_text()), 1):
        ln = re.sub(r"`[^`]*`", "", ln)  # inline code
        for m in LINK_RE.finditer(ln):
            target = m.group(1)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("<"):
                continue
            file_part, _, frag = target.partition("#")
            dest = path if not file_part else (path.parent / unquote(file_part)).resolve()
            where = f"{path.relative_to(ROOT)}:{lineno}"
            if not dest.exists():
                errs.append(f"{where}: missing target {target}")
                continue
            if frag and dest.suffix == ".md" and slug(unquote(frag)) not in anchors(dest):
                errs.append(f"{where}: no heading for #{frag} in {dest.relative_to(ROOT)}")
    return errs


def main() -> int:
    if len(sys.argv) > 1:
        files = [Path(a).resolve() for a in sys.argv[1:]]
    else:
        files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
        files += sorted((ROOT / "services").glob("*/README.md"))
        files += [ROOT / "web-console" / "README.md", ROOT / "tests/e2e/bench/README.md"]
    errs: list[str] = []
    n_links = 0
    for f in files:
        if f.exists():
            e = check(f)
            errs += e
            n_links += len(LINK_RE.findall(f.read_text()))
    for e in errs:
        print("BROKEN", e)
    print(f"{len(files)} files, ~{n_links} links, {len(errs)} broken")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
