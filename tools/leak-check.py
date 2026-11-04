#!/usr/bin/env python3
"""Fails (exit 1) when the repository contains anything that identifies the private deployment it
was extracted from, or any real contact detail:
  - a term from tools/leak-terms.txt (case-insensitive regexes),
  - an email address not at example.com / example.org / users.noreply.github.com / anthropic.com,
  - a private or public IPv4 address other than loopback, 0.0.0.0 and documentation ranges
    (192.0.2.x, 198.51.100.x, 203.0.113.x),
  - a non-example hostname under a private-looking TLD (.internal, .local, .lan, .corp).
Scans every file git tracks (or, outside git, every file), text and binary names alike.
  python3 tools/leak-check.py [paths…]
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TERMS = [re.compile(l.strip(), re.I) for l in (ROOT / "tools" / "leak-terms.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
OK_EMAIL = re.compile(r"@(([a-z0-9-]+\.)*example\.(com|org|net)|users\.noreply\.github\.com|anthropic\.com)$", re.I)
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
# A host under a private-looking TLD; not a file name like `workspace.local.md`.
PRIVATE_HOST = re.compile(r"\b[a-z0-9-]+(\.[a-z0-9-]+)*\.(internal|local|lan|corp)\b(?!\.[a-z0-9])", re.I)
SKIP = {"tools/leak-terms.txt", "tools/leak-check.py"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache"}


def ip_ok(m: re.Match) -> bool:
    a, b, c, d = (int(x) for x in m.groups())
    if max(a, b, c, d) > 255:
        return True                                     # a version number, not an address
    if (a, b, c, d) in ((127, 0, 0, 1), (0, 0, 0, 0)) or a == 127:
        return True
    if (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113)):
        return True                                     # RFC 5737 documentation ranges
    if a in (255,) or (a, b) == (255, 255):
        return True
    return False


def files(args: list[str]) -> list[Path]:
    if args:
        out: list[Path] = []
        for a in args:
            p = Path(a)
            if not p.is_absolute() and not p.exists():
                p = ROOT / a
            if p.is_dir():
                out += [f for f in sorted(p.rglob("*")) if f.is_file() and not SKIP_DIRS & set(f.parts)]
            else:
                out.append(p)
        return out
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"], capture_output=True,
                             text=True, check=True).stdout.split("\n")
        return [ROOT / f for f in out if f]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def main(argv: list[str]) -> int:
    bad = []
    for p in files(argv):
        rel = str(p.relative_to(ROOT)) if p.is_absolute() and ROOT in p.parents else str(p)
        if rel in SKIP or not p.is_file():
            continue
        for t in TERMS:
            if t.search(rel):
                bad.append(f"{rel}: file name matches {t.pattern!r}")
        try:
            text = p.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for t in TERMS:
                if t.search(line):
                    bad.append(f"{rel}:{n}: term {t.pattern!r}")
            for m in EMAIL.finditer(line):
                if not OK_EMAIL.search(m.group(0)):
                    bad.append(f"{rel}:{n}: email {m.group(0)}")
            for m in IPV4.finditer(line):
                if not ip_ok(m):
                    bad.append(f"{rel}:{n}: address {m.group(0)}")
            for m in PRIVATE_HOST.finditer(line):
                if "example" not in m.group(0).lower() and m.group(0).lower() != "host.docker.internal":
                    bad.append(f"{rel}:{n}: private host {m.group(0)}")
    for b in bad[:300]:
        print(b)
    if bad:
        print(f"\nleak-check: {len(bad)} problem(s). Replace with generic names (see CONTRIBUTING.md).", file=sys.stderr)
        return 1
    print("leak-check: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
