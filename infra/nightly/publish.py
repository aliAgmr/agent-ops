# /// script
# requires-python = ">=3.10"
# dependencies = ["markdown==3.9", "requests==2.34.2", "pyyaml==6.0.3"]
# ///
"""Publishes RUN_DIR/report.md as a page on the task board, named after the report's first heading
(e.g. "Daily 2026-11-04"), in one project (NIGHTLY_REPORT_PROJECT, a projects.yaml key; default the
first project).

Built in for Plane (routing.yaml `tracker: plane`): Plane's public API has no pages, so this logs in
to Plane's web app as the nightly bot (email + password, like a browser: CSRF token, then
/auth/sign-in/) and uses the app's own `/api/workspaces/<ws>/projects/<id>/pages/` endpoint. That
endpoint is not a public contract: if a Plane upgrade breaks it, the report still goes out by
Telegram (with no link) and this script says why in RUN_DIR/page.json. Another tracker: page.json
says pages aren't supported (add a publisher here).

Re-running for the same night updates the same page (matched by name). Writes RUN_DIR/page.json:
{"url": ..., "id": ...} or {"error": ...}.
Env: TRACKER_BASE_URL, TRACKER_PUBLIC_URL (default the base URL), TRACKER_WORKSPACE,
TRACKER_NIGHTLY_EMAIL, TRACKER_NIGHTLY_PASSWORD_FILE (default ~/.config/agent-ops/tracker-nightly.password).

  uv run --script publish.py RUN_DIR
"""
import json
import os
import re
import sys

import markdown
import requests
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def title_of(md):
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return (m.group(1).strip() if m else "Daily report")[:255]


def to_html(md):
    body = re.sub(r"^#\s+.+\n", "", md, count=1, flags=re.M)  # the title is the page name
    return markdown.markdown(body, extensions=["tables", "sane_lists"])


def report_project(key=None):
    """(key, tracker_project_id) of the project the report page lives in."""
    data = (yaml.safe_load(open(os.path.join(ROOT, "projects.yaml"))) or {}).get("projects") or {}
    key = (key or os.environ.get("NIGHTLY_REPORT_PROJECT") or next(iter(data), "")).upper()
    p = data.get(key) or {}
    if not p.get("tracker_project_id"):
        raise RuntimeError(f"project {key!r} has no tracker_project_id in projects.yaml")
    return key, str(p["tracker_project_id"])


class PlaneWeb:
    def __init__(self, base, email, password):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        r = self.s.get(f"{self.base}/auth/get-csrf-token/", timeout=30)
        r.raise_for_status()
        self.csrf = r.json()["csrf_token"]
        r = self.s.post(f"{self.base}/auth/sign-in/", data={"csrfmiddlewaretoken": self.csrf, "email": email,
                        "password": password}, headers={"Referer": self.base + "/"}, allow_redirects=False, timeout=30)
        if r.status_code not in (200, 302) or "error_code" in (r.headers.get("Location") or ""):
            raise RuntimeError(f"sign-in failed (HTTP {r.status_code}, {(r.headers.get('Location') or '')[:120]})")
        self.csrf = self.s.cookies.get("csrftoken") or self.csrf
        self.s.headers.update({"X-CSRFToken": self.csrf, "Referer": self.base + "/"})

    def call(self, method, path, body=None):
        r = self.s.request(method, f"{self.base}/api{path}", json=body, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"{method} {path} → HTTP {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}


def publish_plane(env, md):
    base = env["TRACKER_BASE_URL"]
    public = env.get("TRACKER_PUBLIC_URL") or base
    pw_file = os.path.expanduser(env.get("TRACKER_NIGHTLY_PASSWORD_FILE", "~/.config/agent-ops/tracker-nightly.password"))
    if not env.get("TRACKER_NIGHTLY_EMAIL"):
        raise RuntimeError("TRACKER_NIGHTLY_EMAIL is not set")
    web = PlaneWeb(base, env["TRACKER_NIGHTLY_EMAIL"], open(pw_file).read().strip())
    _, pid = report_project()
    ws, name, body = env["TRACKER_WORKSPACE"], title_of(md), to_html(md)
    path = f"/workspaces/{ws}/projects/{pid}/pages/"
    pages = web.call("GET", path)
    pages = pages.get("results", pages) if isinstance(pages, dict) else pages
    page = next((p for p in pages if p.get("name") == name), None)
    if page:
        web.call("PATCH", f"{path}{page['id']}/", {"description_html": body})
    else:
        page = web.call("POST", path, {"name": name, "description_html": body, "access": 0})
    return {"id": page["id"], "name": name, "url": f"{public.rstrip('/')}/{ws}/projects/{pid}/pages/{page['id']}/"}


PUBLISHERS = {"plane": publish_plane}


def main():
    run_dir = sys.argv[1]
    env = os.environ
    out = os.path.join(run_dir, "page.json")
    try:
        md = open(os.path.join(run_dir, "report.md"), encoding="utf-8").read()
        tracker = str((yaml.safe_load(open(os.path.join(ROOT, "routing.yaml"))) or {}).get("tracker") or "plane")
        if tracker not in PUBLISHERS:
            raise RuntimeError(f"report pages aren't supported for tracker {tracker!r} yet (infra/nightly/publish.py PUBLISHERS)")
        res = PUBLISHERS[tracker](env, md)
    except Exception as e:  # never stop the send: the report goes out without a link
        res = {"error": str(e)[:300]}
    json.dump(res, open(out, "w"))
    print(json.dumps(res))


if __name__ == "__main__":
    main()
