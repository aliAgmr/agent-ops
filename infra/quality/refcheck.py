#!/usr/bin/env python3
"""Checks that the agents' instructions only name things that exist, so a renamed script or skill
can't leave a prompt pointing at nothing. Run by CI (`tools/lint.sh`, the `lint` job) and by hand:

  python3 infra/quality/refcheck.py            exit 1 and one line per problem, or "refcheck: clean"

What it reads: every SKILL.md and agent prompt in plugins/ and service/, the service agents'
CLAUDE.md and tick.md, the reference sections and the docs in docs/. What it checks:
- repo paths in backticks (`infra/bin/traps`, `../../plugins/core/skills/x/SKILL.md`,
  `/srv/agent-ops/infra/bin/mr-scan …`) exist; globs must match at least one file;
- skills named as "`x` skill", "skill `x`" or `plugin:skill` exist;
- every protected path without a wildcard (plugins/core/protected-paths.txt) exists in this repo
  when it names one of its folders;
- trap baseline entries name real checks; edit-check ids are unique;
- the plugin versions in the reference's plugin table match each plugin.json.
Placeholders (`<repo>`, `…`), per-machine files (people.yaml, `*.local.*`) and paths in other repos
are skipped.
"""
from __future__ import annotations

import fnmatch
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Folders only this repo has. `docs/` and `tests/` also exist in the product repos the skills talk
# about (the backend's tests/e2e/BUGS.md), so only this repo's own parts of them are checked.
OWN_DIRS = ("infra/", "plugins/", "service/", "knowledge/", "clients/", "desks/", "tools/", "docs/reference/",
            "tests/agent-mcp/")
OWN_TEST = re.compile(r"^(tests/[a-z0-9-]+\.test\.(py|sh|mjs)|docs/[a-z0-9-]+\.md)$")
# Git-ignored files each install writes for itself (SETUP.md): named in prompts, never in git.
LOCAL_FILES = {"people.yaml", "setup.answers.yaml"}
TRAP_CHECKS = ROOT / "plugins/core/skills/data-traps/checks.yaml"
SOURCES = ["plugins/*/skills/*/SKILL.md", "plugins/*/agents/*.md", "service/*/CLAUDE.md", "service/*/tick.md",
           "service/*/.claude/skills/*/SKILL.md", "service/*/.claude/agents/*.md", "docs/reference/sections/*.md",
           "docs/*.md"]
BACKTICK = re.compile(r"`([^`\n]{2,200})`")
SKILL_REF = re.compile(r"`([a-z0-9][a-z0-9-]*(?::[a-z0-9-]+)?)`\s+skill\b|\bskills?\s+`([a-z0-9][a-z0-9-]*(?::[a-z0-9-]+)?)`")
PLUGIN_SKILL = re.compile(r"`((?:core|role-[a-z]+|[a-z]+-tools):[a-z0-9-]+)`")


def skill_names() -> set:
    out = set()
    for p in glob.glob(str(ROOT / "plugins/*/skills/*/SKILL.md")) + glob.glob(str(ROOT / "service/*/.claude/skills/*/SKILL.md")):
        name = Path(p).parent.name
        plugin = Path(p).parents[2].name
        out |= {name, f"{plugin}:{name}"}
    for p in glob.glob(str(ROOT / "plugins/*/agents/*.md")) + glob.glob(str(ROOT / "service/*/.claude/agents/*.md")):
        out.add(Path(p).stem)
    return out


def path_candidates(token: str, src: Path):
    """Repo-relative paths a backticked token names, or nothing when it isn't a path of this repo."""
    t = token.strip().split()[0] if token.strip() else ""
    t = t.rstrip(".,;)")
    if t.endswith(":*"):          # a permission rule: Bash(/srv/agent-ops/infra/bin/x.sh:*)
        t = t[:-2]
    t = t.rstrip(":")
    if ".local." in t:            # per-machine files, not in git
        return None
    if not t or any(c in t for c in "<>{}$…|=\"'") or t.startswith(("http", "~", "-")):
        return None
    if t.startswith("/srv/agent-ops/"):
        return [t[len("/srv/agent-ops/"):]]
    if t.startswith("../") or t.startswith("./"):
        try:
            rel = (src.parent / t).resolve().relative_to(ROOT)
        except ValueError:
            return None
        return None if str(rel) in LOCAL_FILES else [str(rel)]
    if (t.startswith(OWN_DIRS) and "/" in t) or OWN_TEST.match(t):
        return [t]
    return None


_TRACKED = None


def tracked():
    """(files, folders) git tracks here, so a file that exists only on this machine doesn't count;
    None outside a git checkout (then the file system decides)."""
    global _TRACKED
    if _TRACKED is None:
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
            files = {f for f in out.decode("utf-8", "replace").split("\0") if f}
            dirs = {str(Path(f).parents[i]) for f in files for i in range(len(Path(f).parents) - 1)}
            _TRACKED = (files, dirs)
        except (OSError, subprocess.CalledProcessError):
            _TRACKED = False
    return _TRACKED or None


def ignored(rel: str) -> bool:
    """A git-ignored path (a file setup generates, like service/<agent>/.mcp.json): documenting it is
    fine even though no checkout has it. A glob is tested with a sample name."""
    sample = re.sub(r"\*+|\?|\[[^]]*\]", "x", rel)
    try:
        return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", sample],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def exists(rel: str) -> bool:
    rel = rel.rstrip("/")
    t = tracked()
    if t is None:
        if any(c in rel for c in "*?["):
            return bool(glob.glob(str(ROOT / rel), recursive=True))
        return (ROOT / rel).exists()
    files, dirs = t
    if any(c in rel for c in "*?["):
        found = any(fnmatch.fnmatchcase(f, rel) for f in files) or any(fnmatch.fnmatchcase(d, rel) for d in dirs)
    else:
        found = rel in files or rel in dirs
    return found or ignored(rel)


def main() -> int:
    problems = []
    skills = skill_names()
    for pattern in SOURCES:
        for p in sorted(glob.glob(str(ROOT / pattern))):
            src = Path(p)
            rel_src = src.relative_to(ROOT)
            text = src.read_text(errors="replace")
            for m in BACKTICK.finditer(text):
                cands = path_candidates(m.group(1), src)
                if cands and not any(exists(c) for c in cands):
                    line = text.count("\n", 0, m.start()) + 1
                    problems.append(f"{rel_src}:{line}: names `{cands[0]}`, which doesn't exist")
            for m in list(SKILL_REF.finditer(text)) + list(PLUGIN_SKILL.finditer(text)):
                name = next(g for g in m.groups() if g)
                if name not in skills and name.split(":")[-1] not in skills:
                    line = text.count("\n", 0, m.start()) + 1
                    problems.append(f"{rel_src}:{line}: names the skill `{name}`, which doesn't exist")
    # protected paths that name this repo's own files
    pp = ROOT / "plugins/core/protected-paths.txt"
    if pp.is_file():
        for n, line in enumerate(pp.read_text().splitlines(), 1):
            s = line.strip()
            if s and not s.startswith("#") and s.startswith(OWN_DIRS) and not any(c in s for c in "*?[") and not exists(s):
                problems.append(f"plugins/core/protected-paths.txt:{n}: `{s}` doesn't exist")
    # trap baseline ids, edit-check ids
    try:
        import yaml
        checks = yaml.safe_load(TRAP_CHECKS.read_text()) if TRAP_CHECKS.exists() else {}
        ids = {c.get("id") for c in (checks or {}).get("checks") or []}
        base = ROOT / "knowledge/trap-baseline.yaml"
        if base.is_file() and ids:
            for cid in ((yaml.safe_load(base.read_text()) or {}).get("baseline") or {}):
                if cid not in ids:
                    problems.append(f"knowledge/trap-baseline.yaml: baseline for `{cid}`, which is not a check")
    except ImportError:
        print("refcheck: pyyaml missing, skipping the trap baseline check", file=sys.stderr)
    ec = ROOT / "plugins/core/edit-checks.tsv"
    if ec.is_file():
        seen = set()
        for n, line in enumerate(ec.read_text().splitlines(), 1):
            if line and not line.startswith("#"):
                parts = line.split("\t")
                if len(parts) != 6:
                    problems.append(f"plugins/core/edit-checks.tsv:{n}: {len(parts)} fields, expected 6")
                elif parts[0] in seen:
                    problems.append(f"plugins/core/edit-checks.tsv:{n}: duplicate id `{parts[0]}`")
                seen.add(parts[0])
    # plugin versions in the reference
    versions = {json.loads(Path(p).read_text())["name"]: json.loads(Path(p).read_text()).get("version")
                for p in glob.glob(str(ROOT / "plugins/*/.claude-plugin/plugin.json"))}
    for p in glob.glob(str(ROOT / "docs/reference/sections/*.md")):
        text = Path(p).read_text()
        for m in re.finditer(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*([0-9]+\.[0-9]+\.[0-9]+)\s*\|", text, re.M):
            name, v = m.groups()
            if name in versions and versions[name] != v:
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{Path(p).relative_to(ROOT)}:{line}: `{name}` {v} in the table, {versions[name]} in plugin.json")
    for line in problems:
        print(line)
    print(f"refcheck: {len(problems)} problem(s)" if problems else "refcheck: clean")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
