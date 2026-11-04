# Offline tests for infra/nightly: task keys, the repo list, the facts through fake adapters,
# fingerprints, Telegram splitting and destinations, and the page conversion (no network, no
# Docker). Run: uv run --with pyyaml --with requests --with markdown python tests/nightly.test.py
import importlib.util
import json as _json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
fail = 0


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "infra", "nightly", f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ok(cond, label, extra=None):
    global fail
    print(("ok   " if cond else "FAIL ") + label + ("" if cond or extra is None else f"\n     {extra}"))
    fail |= not cond


collect, checks, send, publish = load("collect"), load("checks"), load("send"), load("publish")
yaml = __import__("yaml")

# ---- collect ----------------------------------------------------------------------------------
K = ["BIL", "PRC", "ANL"]
ok(collect.keys_in("fix bil-12 and PRC-3", "see BIL-12, BIL-40, anl-5", keys=K) == ["BIL-12", "PRC-3", "BIL-40", "ANL-5"],
   "task keys: case-insensitive, deduped, in order")
ok(collect.keys_in(None, "", keys=K) == [], "no text → no keys")
ok(collect.keys_in("ABC-1 BIL-2", keys=K) == ["BIL-2"], "only the configured project keys")
routing = {
    "teams": {"backend": {"repos": [{"host": "main", "path": "acme/billing-backend", "default_branch": "dev"}]},
              "data": {"repos": [{"host": "oss", "path": "CHANGE-ME/data-repo"}]}},
    "projects": {"PRC": {"teams": {"backend": {"repos": [{"host": "main", "path": "acme/pricing", "default_branch": "dev"}]},
                                   "frontend": {"people": ["noor"]}}}},
}
r = collect.repos_by_project(routing, "BIL")
ok([x["path"] for x in r["BIL"]] == ["acme/billing-backend"], "default project repos from teams; CHANGE-ME placeholders skipped", r)
ok([x["path"] for x in r["PRC"]] == ["acme/pricing"] and r["PRC"][0]["team"] == "backend", "PRC repos from projects.PRC with their team", r)
real_routing = yaml.safe_load(open(os.path.join(ROOT, "routing.yaml")))
default = collect.default_project(real_routing)
real = collect.repos_by_project(real_routing, default)
ok(default == "BIL" and len(real["BIL"]) >= 2 and len(real["PRC"]) >= 2, "the example routing.yaml: default project and repos", (default, real))
ok([(x["path"], x["team"]) for x in real.get("ANL", [])] == [("acme/warehouse", "data")], "ANL: the warehouse repo, as data team", real.get("ANL"))
ok(collect.default_project({"default_project": "prc"}) == "PRC", "routing.yaml default_project wins")
ok("ANL" in collect.repos_by_project({"projects": {"ANL": {"teams": {"product": {"people": ["sam"]}}}}}, "BIL"),
   "a project with no repos still gets an entry (its board)")


class FakeTracker:
    def projects(self):
        return [{"id": "p1", "identifier": "BIL", "name": "Acme Billing", "is_member": True}]

    def members(self, pid):
        return [{"id": "u1", "email": "noor@example.com", "display_name": "Noor"}]

    def states(self, pid):
        return [{"id": "s1", "name": "Todo", "group": "unstarted"}, {"id": "s2", "name": "Done", "group": "completed"}]

    def list_items(self, pid):
        return [{"id": "w1", "key": "BIL-7", "name": "Fix totals", "state_name": "Todo", "state_group": "unstarted",
                 "labels": ["team:frontend"], "assignees": ["u1"], "created_at": "2026-11-01T00:00:00Z",
                 "updated_at": "2026-11-03T10:00:00Z"},
                {"id": "w2", "key": "BIL-8", "name": "Old", "state_name": "Done", "state_group": "completed",
                 "labels": [], "assignees": [], "updated_at": "2026-10-01T00:00:00Z"}]

    def activities(self, pid, wid):
        return [{"actor": {"id": "u1"}, "field": "state", "old_value": "Backlog", "new_value": "Todo",
                 "created_at": "2026-11-03T10:00:00Z"}]


class FakeCodeHost:
    def list_merge_requests(self, repo, state="opened", updated_after=None, target_branch=None):
        return [{"iid": 4, "title": "BIL-7: fix totals", "state": "merged", "source_branch": "fix/bil-7", "target_branch": "dev",
                 "author": "noor", "created_at": "2026-11-02T00:00:00Z", "merged_at": "2026-11-03T00:00:00Z", "web_url": "u"}]

    def commits(self, repo, ref, since):
        return [{"sha": "9f3ab21c00", "title": "Merge BIL-7", "message": "", "author_name": "Noor", "created_at": "2026-11-03T00:00:00Z"}]


class FakeConnector:
    hosts = {"main": {"type": "gitlab", "url": "https://gitlab.example.com"}, "oss": {"type": "github", "url": "https://github.com"}}

    def tracker(self, token):
        return FakeTracker() if token else None

    def codehost(self, key, token):
        return FakeCodeHost() if token else None


with tempfile.TemporaryDirectory() as d:
    os.environ.update({"TRACKER_NIGHTLY_TOKEN": "t", "CODEHOST_MAIN_TOKEN": "g", "AGENT_OPS_ENV": os.path.join(d, "none")})
    os.environ.pop("CODEHOST_OSS_TOKEN", None)
    out = os.path.join(d, "facts.json")
    collect.main(["--since", "2026-11-02T00:00:00Z", "--out", out], connector=FakeConnector())
    facts = _json.load(open(out))
bil = facts["projects"]["BIL"]
ok([t["key"] for t in bil["tracker"]["changed"]] == ["BIL-7"] and bil["tracker"]["changed"][0]["assignees"] == ["noor@example.com"]
   and bil["tracker"]["changed"][0]["history"][0]["by"] == "noor@example.com", "facts: changed tasks with history, through the tracker adapter", bil["tracker"])
ok([t["key"] for t in bil["tracker"]["open"]] == ["BIL-7"], "facts: open tasks (not completed/cancelled)")
mr = bil["repos"][0]["merge_requests"][0]
ok(mr["keys"] == ["BIL-7"] and bil["repos"][0]["host"] == "main", "facts: MRs/PRs with their task keys, through the code-host adapter", bil["repos"][0])
ok(any("CODEHOST_OSS_TOKEN" in e for e in facts["errors"]) and any("PRC: project not visible" in e for e in facts["errors"]),
   "facts: a missing token or board is reported, the rest still collected", facts["errors"])

# ---- checks -----------------------------------------------------------------------------------
ok(checks.fp("a", "b") == checks.fp("a", "b") and checks.fp("a", "b") != checks.fp("a", "c"), "fingerprints are stable and distinct")
ok(len(checks.fp("x")) == 24, "fingerprint length 24")
full = yaml.safe_load(open(os.path.join(ROOT, "infra", "nightly", "checks.yaml")))
cfg = full["repos"]
names = {os.path.basename(x["path"]) for p in real.values() for x in p}
ok(set(cfg) == names, "checks.yaml covers exactly the repos in routing.yaml", (sorted(cfg), sorted(names)))
ok(all(("checks" not in c) or ("image" in c and "setup" in c) for c in cfg.values()), "every repo with checks has an image and setup")
ok(checks.api_url({"type": "gitlab", "url": "https://gitlab.example.com/"}) == "https://gitlab.example.com/api/v4"
   and checks.api_url({"type": "github", "url": "https://github.com"}) == "https://api.github.com"
   and checks.api_url({"type": "github", "url": "https://ghe.example.com"}) == "https://ghe.example.com/api/v3", "code host API URLs")
ok(checks.e2e_nightly("2026-11-01", {})["ran"] is False, "e2e: not configured → says so")
os.environ["CODEHOST_MAIN_TOKEN"] = "secret-token-1234"
env = checks.git_auth_env("https://gitlab.example.com/acme/billing-backend.git")
ok(env.get("GIT_CONFIG_KEY_0") == "http.https://gitlab.example.com/.extraHeader" and "secret-token-1234" not in env.get("GIT_CONFIG_VALUE_0", ""),
   "git auth: an extra header for the matching code host, token base64-encoded (never on a command line)")

# ---- send -------------------------------------------------------------------------------------
long = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
parts = send.chunks(long)
ok(len(parts) > 1 and all(len(p) <= send.LIMIT for p in parts), "long DM split under Telegram's limit", [len(p) for p in parts])
ok("".join(parts).replace("\n", "") == long.replace("\n", ""), "splitting loses nothing")
ok(send.link_line({"url": "https://x/p"}) == "https://x/p", "page link used when published")
ok("not published" in send.link_line({"error": "sign-in failed"}), "no page → the reason instead of a link")
topics, dms, dm_to = send.send_config()
ok(topics == {"BIL": 101, "PRC": 102} and dms == {"ANL": "kenji"} and dm_to == "question_owner", "destinations from checks.yaml send", (topics, dms, dm_to))
with tempfile.TemporaryDirectory() as d:
    for f, t in (("group-BIL.xx.txt", "other\nPAGE_LINK"), ("group-BIL.en.txt", "en\nPAGE_LINK"), ("dm.en.txt", "full list"),
                 ("page.json", '{"url": "https://x/p"}')):
        open(os.path.join(d, f), "w").write(t)
    res = subprocess.run([sys.executable, os.path.join(ROOT, "infra", "nightly", "send.py"), d, "--dry-run"], capture_output=True, text=True)
    sent = _json.loads(res.stdout or "[]")
ok([m["text"].splitlines()[0] for m in sent] == ["en", "full list"] and sent[0]["thread"] == 101 and "https://x/p" in sent[0]["text"],
   "the project summary to its topic with the link; other files ignored", sent or res.stderr)
ok(sent and sent[-1]["to"] == "alex", "the full list to the question owner (routing.yaml)", sent)

# ---- publish ----------------------------------------------------------------------------------
md = "# Daily 2026-11-04\n\n## Summary\n\n- a\n- b\n\n| repo | tests |\n|---|---|\n| billing | ✅ |\n"
ok(publish.title_of(md) == "Daily 2026-11-04", "page name from the first heading")
h = publish.to_html(md)
ok("<h2>Summary</h2>" in h and "<table>" in h and "Daily 2026" not in h, "markdown → HTML with tables; title not repeated", h)
ok(publish.report_project("BIL")[1] == "00000000-0000-4000-8000-000000000001", "report page project from projects.yaml")
ok("plane" in publish.PUBLISHERS, "Plane pages built in")

raise SystemExit(1 if fail else 0)
