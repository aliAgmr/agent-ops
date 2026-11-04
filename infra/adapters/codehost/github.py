"""GitHub (github.com or Enterprise Server): REST API v3 with a fine-grained or classic token.
Pull requests stand in for merge requests, issue comments for notes, and commit statuses keep
GitLab's vocabulary on the outside (failed ↔ failure, canceled ↔ error)."""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from ..http import AdapterError, request
from .base import CodeHostAdapter

TO_GH = {"pending": "pending", "running": "pending", "success": "success", "failed": "failure", "canceled": "error"}
FROM_GH = {"pending": "pending", "success": "success", "failure": "failed", "error": "canceled"}


class GitHubHost(CodeHostAdapter):
    name = "github"

    @property
    def api(self) -> str:
        if self.host in ("github.com", "www.github.com"):
            return "https://api.github.com"
        if self.host.startswith("api."):
            return self.url
        return f"{self.url}/api/v3"

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return request(method, self.api + path, h, body, secrets=[self.token])

    def _list(self, path: str) -> List[Dict[str, Any]]:
        rows = self._call("GET", path)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise AdapterError(0, f"unexpected answer from {path.split('?')[0]}")
        return rows

    @staticmethod
    def _pr(p: Dict[str, Any]) -> Dict[str, Any]:
        return {"iid": p.get("number"), "title": p.get("title"), "source_branch": (p.get("head") or {}).get("ref"),
                "target_branch": (p.get("base") or {}).get("ref"), "sha": (p.get("head") or {}).get("sha"),
                "web_url": p.get("html_url"), "author": (p.get("user") or {}).get("login"),
                "updated_at": p.get("updated_at"), "merged_at": p.get("merged_at"),
                "state": "merged" if p.get("merged_at") else ("opened" if p.get("state") == "open" else p.get("state")),
                "description": p.get("body"), "labels": [l.get("name") for l in (p.get("labels") or [])]}

    def whoami(self) -> Dict[str, Any]:
        u = self._call("GET", "/user") or {}
        return {"username": u.get("login")}

    def list_merge_requests(self, repo: str, *, state: str = "opened", target_branch: Optional[str] = None,
                            updated_after: Optional[str] = None) -> List[Dict[str, Any]]:
        gh_state = {"opened": "open", "all": "all", "merged": "closed", "closed": "closed"}.get(state, "open")
        q = {"state": gh_state, "sort": "updated", "direction": "desc", "per_page": 50}
        if target_branch:
            q["base"] = target_branch
        rows = [self._pr(p) for p in (self._list(f"/repos/{repo}/pulls?" + urllib.parse.urlencode(q)))]
        if state == "merged":
            rows = [r for r in rows if r["merged_at"]]
        if updated_after:
            rows = [r for r in rows if (r["updated_at"] or "") > updated_after]
        return rows

    def get_merge_request(self, repo: str, iid: int) -> Dict[str, Any]:
        return self._pr(self._call("GET", f"/repos/{repo}/pulls/{iid}") or {})

    def merge_request_files(self, repo: str, iid: int) -> List[str]:
        return [f.get("filename") for f in (self._list(f"/repos/{repo}/pulls/{iid}/files?per_page=100"))]

    def commits(self, repo: str, ref: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
        q = {"sha": ref, "per_page": 100}
        if since:
            q["since"] = since
        out = []
        for c in self._list(f"/repos/{repo}/commits?" + urllib.parse.urlencode(q)):
            msg = (c.get("commit") or {}).get("message") or ""
            out.append({"sha": c.get("sha"), "title": msg.split("\n", 1)[0], "message": msg,
                        "author_name": ((c.get("commit") or {}).get("author") or {}).get("name"),
                        "created_at": ((c.get("commit") or {}).get("author") or {}).get("date")})
        return out

    def statuses(self, repo: str, sha: str, name: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._list(f"/repos/{repo}/commits/{sha}/statuses?per_page=100")
        out = [{"id": s.get("id"), "status": FROM_GH.get(s.get("state"), s.get("state")), "name": s.get("context"),
                "description": s.get("description") or "", "created_at": s.get("created_at")}
               for s in rows if not name or s.get("context") == name]
        return sorted(out, key=lambda s: s["id"] or 0)

    def post_status(self, repo: str, sha: str, state: str, name: str, description: str,
                    target_url: Optional[str] = None) -> Dict[str, Any]:
        body = {"state": TO_GH.get(state, state), "context": name, "description": description[:140]}
        if target_url:
            body["target_url"] = target_url
        return self._call("POST", f"/repos/{repo}/statuses/{sha}", body) or {}

    def update_merge_request(self, repo: str, iid: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        f = dict(fields)
        labels = f.pop("labels", None)
        body = {k2: f[k1] for k1, k2 in (("title", "title"), ("description", "body"), ("target_branch", "base")) if k1 in f}
        out = self._pr(self._call("PATCH", f"/repos/{repo}/pulls/{iid}", body) or {}) if body else {}
        if labels is not None:
            lab = labels.split(",") if isinstance(labels, str) else labels
            self._call("PUT", f"/repos/{repo}/issues/{iid}/labels", {"labels": [x for x in lab if x]})
        return out

    def close_merge_request(self, repo: str, iid: int) -> Dict[str, Any]:
        return self._pr(self._call("PATCH", f"/repos/{repo}/pulls/{iid}", {"state": "closed"}) or {})

    def delete_note(self, repo: str, kind: str, iid: int, note_id: str) -> None:
        self._call("DELETE", f"/repos/{repo}/issues/comments/{note_id}")

    def clone_url(self, repo: str, with_token: bool = False) -> str:
        auth = f"x-access-token:{self.token}@" if with_token and self.token else ""
        scheme, rest = self.url.split("://", 1)
        return f"{scheme}://{auth}{rest}/{repo}.git"
