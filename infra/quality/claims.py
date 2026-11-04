#!/usr/bin/env python3
"""How much of an agent's write-up is backed by a source: the unsourced-claim rate.

A *claim* is one factual unit in the parts of a write-up that state facts: a list item, a table row
or a paragraph (in an analyst `## Analysis`: Reproduced, Where it happens, When it started, Root
cause, Impact, Design, What exists today, Data check; in an agent-mcp answer: the whole body). A
claim is *sourced* when it cites something checkable: `repo/path.ts:12` or a file path, an MR/PR
(`!123`), a commit sha, a task key (`BIL-12`), `artifact "<title>"`, a CI build, a BI
card/dashboard, a URL, or SQL (in the unit, or a query block in the same part for numbers/tables).

Which parts state facts is configurable (your analysis template may use other headings):
`AGENT_CLAIMS_FACT_PARTS` / `AGENT_CLAIMS_SKIP_PARTS` (comma-separated heading starts), and which
`<db>.` prefixes count as warehouse tables: `AGENT_CLAIMS_DATABASES` (default analytics, staging,
raw, warehouse, mv).

Deterministic, no model calls: it measures, it doesn't judge whether the source says what the claim
says (that is the analyst's fresh claim-checker's job).

  claims.py --item BIL-12       the item's ## Analysis section, read from the task board (tracker
                                adapter, TRACKER_TRIAGE_TOKEN from the agent host's env file)
  claims.py - < answer.md       any text or HTML on stdin
Prints JSON: {"claims", "unsourced", "rate", "examples": [first unsourced claims]}.
Standard library only.
"""
from __future__ import annotations

import argparse
import html as htmlmod
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_FACT_PARTS = ["reproduced", "where it happens", "when it started", "root cause", "impact", "design",
                      "what exists", "data check"]
DEFAULT_SKIP_PARTS = ["summary", "fix options", "how to verify", "questions", "sources", "approaches", "plan",
                      "acceptance criteria", "risks"]


def _parts_re(names: List[str], word_end: bool = True) -> "re.Pattern[str]":
    alts = "|".join(re.escape(n.strip()) for n in names if n.strip()) or "(?!)"
    return re.compile(rf"^({alts})" + (r"\b" if word_end else ""), re.I)


def _env_list(name: str, default: List[str]) -> List[str]:
    v = os.environ.get(name, "")
    return [x.strip() for x in v.split(",") if x.strip()] or default


FACT_PARTS = _parts_re(_env_list("AGENT_CLAIMS_FACT_PARTS", DEFAULT_FACT_PARTS))
SKIP_PARTS = _parts_re(_env_list("AGENT_CLAIMS_SKIP_PARTS", DEFAULT_SKIP_PARTS), word_end=False)
SOURCE = re.compile(
    r"[\w.\-/]+\.(?:ts|tsx|js|jsx|mjs|py|sql|sh|go|java|kt|cs|vue|yaml|yml|json|md|xml|html)\b(?::\d+)?"  # file
    r"|(?:^|\s|\()!\d+\b"                                   # MR !123
    r"|\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"               # commit sha (has a letter)
    r"|\b[A-Z]{2,5}-\d+\b"                                  # task key
    r"|artifact\s+[\"“]"                                    # artifact "<title>"
    r"|\bbuild\s*#?\d+|#\d+\s+build"                        # CI build
    r"|\b(?:card|dashboard|question)\s+#?\d+"               # BI card / dashboard
    r"|https?://"
    r"|\bselect\b[\s\S]{0,400}\bfrom\b",                     # SQL in the unit
    re.I)
DATAISH = re.compile(r"\d|\b(?:" + "|".join(re.escape(d) for d in _env_list(
    "AGENT_CLAIMS_DATABASES", ["analytics", "staging", "raw", "warehouse", "mv"])) + r")\.\w+", re.I)


class _Units(HTMLParser):
    """Splits HTML into (part heading, unit text, unit has <pre>) and remembers which parts hold a
    query block."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.part = ""
        self.units: List[Tuple[str, str, bool]] = []
        self.part_has_pre: Dict[str, bool] = {}
        self.stack: List[str] = []
        self.buf: List[str] = []
        self.unit_pre = False
        self.heading: Optional[List[str]] = None
        self.depth = 0      # inside an li / tr / p we collect

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self._flush()
            self.heading = []
        elif tag in ("li", "tr", "p") and self.depth == 0:
            self._flush()
            self.depth = 1
            self.stack.append(tag)
        elif tag in ("li", "tr", "p"):
            self.depth += 1
            self.stack.append(tag)
        if tag == "pre":
            self.part_has_pre[self.part] = True
            if self.depth:
                self.unit_pre = True
        if tag in ("td", "th", "br") and self.depth:
            self.buf.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self.heading is not None:
            self.part = " ".join("".join(self.heading).split())
            self.heading = None
        elif tag in ("li", "tr", "p") and self.stack:
            self.stack.pop()
            self.depth -= 1
            if self.depth == 0:
                self._flush()

    def handle_data(self, data):
        if self.heading is not None:
            self.heading.append(data)
        elif self.depth:
            self.buf.append(data)

    def _flush(self):
        text = " ".join("".join(self.buf).split()).strip(" |")
        if text:
            self.units.append((self.part, text, self.unit_pre))
        self.buf, self.unit_pre, self.depth, self.stack = [], False, 0, []


def _md_to_units(text: str) -> Tuple[List[Tuple[str, str, bool]], Dict[str, bool]]:
    units, has_pre, part, in_code, buf = [], {}, "", False, []

    def flush():
        t = " ".join(" ".join(buf).split())
        if t:
            units.append((part, t, False))
        buf.clear()

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            has_pre[part] = True
            if in_code:
                flush()
            continue
        if in_code:
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", s)
        if m:
            flush(); part = m.group(1).strip(); continue
        if not s:
            flush(); continue
        if re.match(r"^([-*+]|\d+[.)])\s+", s) or s.startswith("|"):
            flush(); buf.append(re.sub(r"^([-*+]|\d+[.)])\s+", "", s)); flush(); continue
        buf.append(s)
    flush()
    return units, has_pre


def is_html(text: str) -> bool:
    return bool(re.search(r"<(p|li|h[1-6]|ul|ol|table|pre)\b", text, re.I))


def analysis_section(description_html: str) -> str:
    """The `## Analysis` part of a work item's description (up to the next h2), or ''."""
    m = re.search(r"<h2[^>]*>\s*Analysis\b.*?</h2>", description_html or "", re.I | re.S)
    if not m:
        return ""
    rest = description_html[m.start():]
    n = re.search(r"<h2[^>]*>", rest[len(m.group(0)):], re.I)
    return rest[: len(m.group(0)) + n.start()] if n else rest


def measure(text: str, *, all_parts: bool = False) -> Dict[str, Any]:
    """Counts claims and unsourced claims. `all_parts`: every part counts (agent-mcp answers);
    otherwise only the fact parts of an analysis (and everything when it has no headings)."""
    if is_html(text):
        p = _Units(); p.feed(text); p._flush()
        units, has_pre = p.units, p.part_has_pre
    else:
        units, has_pre = _md_to_units(text)
    has_fact = any(FACT_PARTS.match(u[0] or "") for u in units)
    claims, unsourced = 0, []
    for part, unit, pre in units:
        part = part or ""
        if re.match(r"^sources?\b", part, re.I):
            continue
        if not all_parts and (not FACT_PARTS.match(part) if has_fact else bool(SKIP_PARTS.match(part))):
            continue
        if len(unit) < 25 or re.match(r"^(option|approach|step)\s*\|", unit, re.I):
            continue                                        # table headers, fragments
        claims += 1
        ok = pre or bool(SOURCE.search(unit)) or (has_pre.get(part) and bool(DATAISH.search(unit)))
        if not ok:
            unsourced.append(unit[:160])
    return {"claims": claims, "unsourced": len(unsourced),
            "rate": round(len(unsourced) / claims, 3) if claims else None, "examples": unsourced[:5]}


# ------------------------------------------------------------------------------------------ CLI

def _env(path: str = "~/.config/agent-ops/env") -> Dict[str, str]:
    out: Dict[str, str] = {}
    p = Path(path).expanduser()
    if p.is_file():
        for line in p.read_text().splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


def fetch_item(key: str, tracker: Any = None) -> str:
    """The description HTML of a task `BIL-12`, through the tracker adapter (infra/adapters) with the
    agents' read token (TRACKER_TRIAGE_TOKEN), unless a tracker adapter object is passed."""
    if tracker is None:
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "infra" / "journal"))
        import journal as J  # noqa: E402  (its Connector builds adapters from routing.yaml + env)
        env = {**_env(os.environ.get("AGENT_OPS_ENV", "~/.config/agent-ops/env")), **{k: v for k, v in os.environ.items() if v}}
        tok = env.get("TRACKER_TRIAGE_TOKEN", "")
        if not (env.get("TRACKER_BASE_URL") and tok):
            raise SystemExit("TRACKER_BASE_URL / TRACKER_TRIAGE_TOKEN not set")
        tracker = J.Connector(env, root).tracker(tok)
    item = tracker.get_item_by_key(key.upper()) or {}
    return item.get("description_html") or ""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", nargs="?", default="-", help="'-' for stdin")
    ap.add_argument("--item", help="a task key (BIL-12): measure its ## Analysis section")
    ap.add_argument("--all", action="store_true", help="count every part (agent-mcp answers)")
    a = ap.parse_args(argv)
    if a.item:
        text = analysis_section(fetch_item(a.item))
        if not text:
            print(json.dumps({"claims": 0, "unsourced": 0, "rate": None, "examples": [], "note": "no ## Analysis section"}))
            return 0
    else:
        text = sys.stdin.read() if a.source == "-" else Path(a.source).read_text()
    print(json.dumps(measure(htmlmod.unescape(text) if not is_html(text) else text, all_parts=a.all), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
