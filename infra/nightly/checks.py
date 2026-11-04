# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.3", "requests==2.34.2"]
# ///
"""Nightly security and test checks (README.md here). No Claude, no writes outside the cache dir.

Per repo in checks.yaml: refresh a private checkout of the branch, run its setup + checks in a
throwaway Docker container (only the repo mounted), then osv-scanner on its lockfiles, gitleaks on
the commits since --since and semgrep on the files changed since then. Optionally (checks.yaml
`e2e`) reads the latest scheduled E2E pipeline (GitLab) or workflow run (GitHub) on a branch, and a
bugs file such as tests/e2e/BUGS.md.

Every finding carries a stable `fingerprint` (the agent uses it as the task's external id, so the
same problem never becomes two tasks). Repos are fetched from the code hosts in routing.yaml with
the agents' read-only tokens (CODEHOST_<KEY>_TOKEN).

  uv run --script checks.py --since 2026-10-30T23:00:00Z --out checks.json [--only repo] [--skip-tests]
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.expanduser(os.environ.get(
    "NIGHTLY_CACHE", "~/Library/Caches/agent-ops/nightly" if sys.platform == "darwin" else "~/.cache/agent-ops/nightly"))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.environ.get("AGENT_REPOS_DIR", "/srv/repos")
TAIL = 4000
CODE_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".svelte", ".py", ".sql", ".sh", ".yml", ".yaml", ".json", ".Dockerfile")


def fp(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def run(cmd, cwd=None, timeout=1800, env=None):
    """→ (rc, combined output tail). rc 124 on timeout."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout + p.stderr)[-TAIL:]
    except subprocess.TimeoutExpired as e:
        return 124, f"timed out after {timeout}s\n" + ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))[-TAIL:]


def upstream_url(name):
    """Code-host URL of a repo: `origin` of /srv/repos/<name>, followed while it is a local checkout
    (a clone made from someone's working copy; infra/bin/repo-sync.sh does the same)."""
    url = os.path.join(SRC, name)
    for _ in range(4):
        if not os.path.isdir(url):
            return url
        url = subprocess.run(["git", "-C", url, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    raise RuntimeError(f"no remote origin found for {name}")


def code_hosts():
    """routing.yaml `code_hosts`: key → {type, url}."""
    try:
        return (yaml.safe_load(open(os.path.join(ROOT, "routing.yaml"))) or {}).get("code_hosts") or {}
    except OSError:
        return {}


def api_url(h):
    """REST API base of a code host: GitLab `<url>/api/v4`; github.com `https://api.github.com`;
    GitHub Enterprise `<url>/api/v3`."""
    url = str(h.get("url") or "").rstrip("/")
    if h.get("type") == "github":
        return "https://api.github.com" if url in ("", "https://github.com") else url + "/api/v3"
    return url + "/api/v4"


def git_auth_env(url):
    """Git environment that authenticates to the configured code hosts with the agents' read-only
    tokens (CODEHOST_<KEY>_TOKEN), through GIT_CONFIG_* variables (never on a command line or in a
    config file). GitLab: oauth2:<token>; GitHub: x-access-token:<token>."""
    import base64
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname or ""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for key, h in code_hosts().items():
        if (urlsplit(str(h.get("url") or "")).hostname or "") != host:
            continue
        tok = os.environ.get(f"CODEHOST_{key.upper().replace('-', '_')}_TOKEN")
        if tok:
            user = "x-access-token" if h.get("type") == "github" else "oauth2"
            basic = base64.b64encode(f"{user}:{tok}".encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": f"http.https://{host}/.extraHeader",
                        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
                        "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": ""})
        break
    return env


def checkout(name, branch, only=None):
    """Private checkout of <branch> fetched fresh from its code host. → (dir, head sha). With `only`, a
    blobless fetch and a sparse checkout of that folder (big repos where one folder is ours)."""
    d = os.path.join(CACHE, "repos", name)
    url = upstream_url(name)
    env = git_auth_env(url)
    if not os.path.isdir(os.path.join(d, ".git")):
        os.makedirs(os.path.dirname(d), exist_ok=True)
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "remote", "add", "upstream", url], check=True)
        if only:
            subprocess.run(["git", "-C", d, "config", "remote.upstream.promisor", "true"], check=True)
            subprocess.run(["git", "-C", d, "config", "remote.upstream.partialclonefilter", "blob:none"], check=True)
    subprocess.run(["git", "-C", d, "remote", "set-url", "upstream", url], check=True)
    if only:  # every run, so a changed `only` in checks.yaml reaches an existing checkout
        subprocess.run(["git", "-C", d, "sparse-checkout", "set", "--no-cone", f"/{only.strip('/')}/", "/package.json",
                        "/package-lock.json"], check=True, timeout=900, env=env)
    subprocess.run(["git", "-C", d, "fetch", "-q", "--prune", *(["--filter=blob:none"] if only else []), "upstream",
                    f"+refs/heads/{branch}:refs/remotes/upstream/{branch}"], check=True, timeout=1800, env=env)
    subprocess.run(["git", "-C", d, "checkout", "-q", "-f", "--detach", f"upstream/{branch}"], check=True, timeout=900, env=env)
    subprocess.run(["git", "-C", d, "clean", "-q", "-fdx", "-e", "node_modules", "-e", "node_modules/**"], check=True)
    head = subprocess.run(["git", "-C", d, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    return d, head


def docker_checks(d, cfg):
    """setup + each check in one container; results via files in .nightly/ (the container's exit
    code alone can't tell checks apart)."""
    out = {}
    if not cfg.get("checks"):
        return out
    script = ["set +e", "mkdir -p .nightly", f"( {cfg['setup']} ) > .nightly/setup.log 2>&1; echo $? > .nightly/setup.rc"]
    script.append('if [ "$(cat .nightly/setup.rc)" = 0 ]; then')
    for name, cmd in cfg["checks"].items():
        script.append(f"  ( {cmd} ) > .nightly/{name}.log 2>&1; echo $? > .nightly/{name}.rc")
    script.append("fi")
    shutil.rmtree(os.path.join(d, ".nightly"), ignore_errors=True)
    name = f"nightly-{os.path.basename(d)}-{os.getpid()}"
    rc, log = run(["docker", "run", "--rm", "--name", name, "-e", "CI=true", "-e", "HOME=/tmp",
                   "-v", f"{d}:/w", "-w", "/w", cfg["image"], "sh", "-c", "\n".join(script)], timeout=2400)
    if rc == 124:
        subprocess.run(["docker", "kill", name], capture_output=True)

    def read(n):
        try:
            r = open(os.path.join(d, ".nightly", f"{n}.rc")).read().strip()
            return int(r) if r.isdigit() else None, open(os.path.join(d, ".nightly", f"{n}.log"), errors="replace").read()[-TAIL:]
        except OSError:
            return None, ""

    src, slog = read("setup")
    out["setup"] = {"ok": src == 0, "rc": src if src is not None else rc, "tail": slog or log}
    for n in cfg["checks"]:
        c, t = read(n)
        out[n] = {"ok": c == 0, "rc": c, "tail": t if c else t[-600:]} if c is not None else {"ok": None, "rc": None, "tail": "not run (setup failed or timed out)"}
    return out


def osv(d, name):
    rc, _ = run(["osv-scanner", "scan", "source", "--format", "json", "--output", os.path.join(CACHE, f"{name}.osv.json"),
                 "--recursive=false", d], timeout=600)
    try:
        data = json.load(open(os.path.join(CACHE, f"{name}.osv.json")))
    except (OSError, ValueError):
        return [], f"osv-scanner failed (exit {rc})"
    by_pkg = {}
    for res in data.get("results") or []:
        for pkg in res.get("packages") or []:
            p = pkg.get("package") or {}
            key = (p.get("ecosystem"), p.get("name"))
            v = by_pkg.setdefault(key, {"package": p.get("name"), "versions": [], "ecosystem": p.get("ecosystem"),
                                         "ids": [], "max_severity": 0.0, "summaries": [],
                                         "fingerprint": fp(name, "osv", p.get("ecosystem"), p.get("name"))})
            if p.get("version") and p.get("version") not in v["versions"]:
                v["versions"].append(p.get("version"))
            for g in pkg.get("groups") or []:
                v["ids"] += [i for i in g.get("ids") or [] if i not in v["ids"]]
                try:
                    v["max_severity"] = max(v["max_severity"], float(g.get("max_severity") or 0))
                except ValueError:
                    pass
            for x in pkg.get("vulnerabilities") or []:
                if x.get("summary") and len(v["summaries"]) < 5:
                    v["summaries"].append(x["summary"][:160])
    vulns = sorted(by_pkg.values(), key=lambda v: -v["max_severity"])
    return vulns, None


def gitleaks(d, name, since):
    path = os.path.join(CACHE, f"{name}.gitleaks.json")
    rc, log = run(["gitleaks", "git", d, f"--log-opts=--since={since} HEAD", "--report-format", "json",
                   "--report-path", path, "--redact", "--no-banner", "--exit-code", "0"], timeout=600)
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return [], f"gitleaks failed (exit {rc}): {log[-200:]}"
    return [{"rule": x.get("RuleID"), "file": x.get("File"), "line": x.get("StartLine"), "commit": (x.get("Commit") or "")[:10],
             "author": x.get("Email"), "fingerprint": fp(name, "secret", x.get("RuleID"), x.get("File"), x.get("Commit"))}
            for x in data or []], None


def changed_files(d, since, only):
    r = subprocess.run(["git", "-C", d, "log", f"--since={since}", "--name-only", "--pretty=format:", "HEAD", "--", only or "."],
                       capture_output=True, text=True)
    files = sorted({f for f in r.stdout.splitlines() if f and os.path.isfile(os.path.join(d, f))})
    return [f for f in files if f.endswith(CODE_EXT) or os.path.basename(f) == "Dockerfile"]


def semgrep(d, name, files):
    if not files:
        return [], None
    path = os.path.join(CACHE, f"{name}.semgrep.json")
    rc, log = run(["semgrep", "scan", "--config", "p/security-audit", "--config", "p/secrets", "--json", "--quiet",
                   "--metrics=off", "--disable-version-check", "--output", path, *files], cwd=d, timeout=1200)
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return [], f"semgrep failed (exit {rc}): {log[-200:]}"
    return [{"rule": r["check_id"].split(".")[-1], "file": r["path"], "line": r["start"]["line"],
             "severity": r["extra"].get("severity"), "message": r["extra"].get("message", "")[:240],
             "fingerprint": fp(name, "sast", r["check_id"], r["path"], r["extra"].get("lines", "").strip()[:120])}
            for r in data.get("results") or []], None


def e2e_nightly(since, cfg):
    """The latest scheduled pipeline (GitLab) or workflow run (GitHub) on `cfg.branch` since
    `since`, with the jobs whose name contains `cfg.job_match`. cfg (checks.yaml `e2e`): host (a
    routing.yaml code_hosts key), repo (path), branch, job_match. Empty cfg → not configured."""
    if not cfg:
        return {"ran": False, "note": "no e2e pipeline configured (checks.yaml e2e)"}
    h = code_hosts().get(cfg.get("host"))
    tok = os.environ.get(f"CODEHOST_{str(cfg.get('host')).upper().replace('-', '_')}_TOKEN")
    if not (h and tok):
        return {"error": f"no code host or token for {cfg.get('host')} (CODEHOST_<KEY>_TOKEN)"}
    base, repo, branch, match = api_url(h), cfg["repo"], cfg.get("branch") or "main", cfg.get("job_match") or "e2e"
    s = requests.Session()
    try:
        if h.get("type") == "github":
            s.headers.update({"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
            runs = s.get(f"{base}/repos/{repo}/actions/runs", params={"event": "schedule", "branch": branch,
                                                                      "created": f">={since[:10]}", "per_page": 5}, timeout=30).json()
            runs = runs.get("workflow_runs") or []
            if not runs:
                return {"ran": False, "note": f"no scheduled workflow run on {branch} since the last run"}
            r = runs[0]
            jobs = s.get(f"{base}/repos/{repo}/actions/runs/{r['id']}/jobs", params={"per_page": 100}, timeout=30).json().get("jobs") or []
            return {"ran": True, "pipeline": r["html_url"], "status": r.get("conclusion") or r.get("status"),
                    "created_at": r["created_at"], "jobs": [{"name": j["name"], "status": j.get("conclusion") or j.get("status"),
                                                             "url": j["html_url"]} for j in jobs if match in j["name"]]}
        from urllib.parse import quote
        pid = quote(repo, safe="")
        s.headers.update({"PRIVATE-TOKEN": tok})
        ps = s.get(f"{base}/projects/{pid}/pipelines", params={"source": "schedule", "ref": branch, "updated_after": since,
                                                               "order_by": "id", "sort": "desc", "per_page": 5}, timeout=30).json()
        if not ps:
            return {"ran": False, "note": f"no scheduled pipeline on {branch} since the last run"}
        p = ps[0]
        jobs = s.get(f"{base}/projects/{pid}/pipelines/{p['id']}/jobs", params={"per_page": 100}, timeout=30).json()
        return {"ran": True, "pipeline": p["web_url"], "status": p["status"], "created_at": p["created_at"],
                "jobs": [{"name": j["name"], "status": j["status"], "url": j["web_url"]} for j in jobs if match in j["name"]]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    full = yaml.safe_load(open(os.environ.get("NIGHTLY_CHECKS", os.path.join(HERE, "checks.yaml")))) or {}
    cfg, e2e = full.get("repos") or {}, full.get("e2e") or {}
    os.makedirs(CACHE, exist_ok=True)
    out = {"since": a.since, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "repos": {}, "e2e": e2e_nightly(a.since, e2e)}
    for name, c in cfg.items():
        if a.only and name != a.only:
            continue
        t0 = time.time()
        r = {"branch": c["branch"], "errors": []}
        try:
            d, r["head"] = checkout(name, c["branch"], c.get("only"))
        except Exception as e:
            r["errors"].append(f"checkout failed: {e}")
            out["repos"][name] = r
            continue
        if not a.skip_tests:
            r["tests"] = docker_checks(d, c)
        r["dependencies"], err = osv(os.path.join(d, c.get("only") or "") if c.get("only") and os.path.exists(os.path.join(d, c["only"], "package-lock.json")) else d, name)
        err and r["errors"].append(err)
        r["secrets"], err = gitleaks(d, name, a.since)
        err and r["errors"].append(err)
        files = changed_files(d, a.since, c.get("only"))
        r["changed_files"] = len(files)
        r["sast"], err = semgrep(d, name, files)
        err and r["errors"].append(err)
        bugs = c.get("bugs_file")    # e.g. tests/e2e/BUGS.md: known E2E bugs, read by the agent
        if bugs and os.path.isfile(os.path.join(d, bugs)):
            r["e2e_bugs_md"] = open(os.path.join(d, bugs), errors="replace").read()[-6000:]
        r["seconds"] = round(time.time() - t0)
        out["repos"][name] = r
    out["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({n: {"tests": {k: v.get("ok") for k, v in (r.get("tests") or {}).items()},
                          "deps": len(r.get("dependencies") or []), "secrets": len(r.get("secrets") or []),
                          "sast": len(r.get("sast") or []), "errors": r.get("errors"), "s": r.get("seconds")}
                      for n, r in out["repos"].items()}))


if __name__ == "__main__":
    main()
