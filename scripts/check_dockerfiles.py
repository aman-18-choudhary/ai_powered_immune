#!/usr/bin/env python3
"""Static Dockerfile checks (no Docker daemon needed).

For every Dockerfile in the repo (services/*, infra/docker/*) this verifies, relative to the
build context (the repository root):

* COPY / ADD sources (not --from=...) exist after .dockerignore is ignored for the check
  (we only check existence on disk; .dockerignore exclusions are reported separately),
* a COPY source is not excluded by .dockerignore (that would break the build),
* the final stage sets a non-root USER and declares a HEALTHCHECK,
* no FROM uses ``:latest`` or an untagged image,
* no ENV/ARG line looks like it embeds a secret.

Exit code 1 on any error. Usage: python scripts/check_dockerfiles.py
"""

from __future__ import annotations

import fnmatch
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_RE = re.compile(
    r"(SECRET|PASSWORD|TOKEN|PRIVATE_KEY|API_KEY)\w*\s*=\s*\S+", re.IGNORECASE
)


def logical_lines(text: str) -> list[str]:
    out, cur = [], ""
    for raw in text.splitlines():
        s = raw.strip()
        if not cur and (not s or s.startswith("#")):
            continue
        if s.endswith("\\"):
            cur += s[:-1] + " "
            continue
        out.append((cur + s).strip())
        cur = ""
    if cur:
        out.append(cur.strip())
    return out


def ignore_patterns() -> list[str]:
    f = ROOT / ".dockerignore"
    if not f.exists():
        return []
    return [
        ln.strip()
        for ln in f.read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def ignored(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    for pat in patterns:
        pat = pat.rstrip("/")
        if pat.startswith("**/"):
            tail = pat[3:]
            if any(fnmatch.fnmatch(p, tail) for p in parts) or fnmatch.fnmatch(
                rel, tail
            ):
                return True
        elif fnmatch.fnmatch(rel, pat) or rel == pat or rel.startswith(pat + "/"):
            return True
    return False


def check(path: Path, patterns: list[str]) -> list[str]:
    errs: list[str] = []
    name = path.relative_to(ROOT)
    stages: list[dict] = []
    for ln in logical_lines(path.read_text()):
        word, _, rest = ln.partition(" ")
        word = word.upper()
        if word == "FROM":
            toks = shlex.split(rest)
            toks = [t for t in toks if not t.startswith("--")]
            image = toks[0]
            is_stage_ref = len(toks) > 2 and image in {s["name"] for s in stages}
            untagged = ":" not in image.split("/")[-1] or image.endswith(":latest")
            if image.lower() != "scratch" and not is_stage_ref and untagged:
                errs.append(f"{name}: FROM {image} is untagged or :latest")
            stages.append(
                {
                    "name": toks[-1] if len(toks) >= 3 else None,
                    "user": None,
                    "hc": False,
                }
            )
        elif not stages:
            continue
        elif word == "USER":
            stages[-1]["user"] = rest.strip()
        elif word == "HEALTHCHECK":
            stages[-1]["hc"] = not rest.strip().upper().startswith("NONE")
        elif word in ("COPY", "ADD"):
            toks = shlex.split(rest)
            flags = [t for t in toks if t.startswith("--")]
            args = [t for t in toks if not t.startswith("--")]
            if any(f.startswith("--from=") for f in flags) or len(args) < 2:
                continue
            for src in args[:-1]:
                if src.startswith("http"):
                    errs.append(f"{name}: ADD from URL {src}")
                    continue
                matches = list(ROOT.glob(src))
                if not matches:
                    errs.append(
                        f"{name}: {word} source {src!r} does not exist in the build context"
                    )
                elif ignored(src.rstrip("/"), patterns):
                    errs.append(
                        f"{name}: {word} source {src!r} is excluded by .dockerignore"
                    )
        elif word in ("ENV", "ARG") and SECRET_RE.search(rest):
            errs.append(f"{name}: {word} looks like it embeds a secret: {rest[:40]}")
    if not stages:
        return [f"{name}: no FROM"]
    last = stages[-1]
    if last["user"] in (None, "root", "0"):
        errs.append(f"{name}: final stage runs as root (no USER)")
    if not last["hc"]:
        errs.append(f"{name}: final stage has no HEALTHCHECK")
    return errs


def main() -> int:
    files = sorted(ROOT.glob("services/*/Dockerfile")) + sorted(
        ROOT.glob("infra/docker/*Dockerfile*")
    )
    if not files:
        print("no Dockerfiles found")
        return 1
    patterns = ignore_patterns()
    errs: list[str] = []
    for f in files:
        e = check(f, patterns)
        print(("FAIL " if e else "ok   ") + str(f.relative_to(ROOT)))
        errs += e
    for e in errs:
        print("  - " + e)
    print(f"{len(files)} Dockerfiles, {len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
