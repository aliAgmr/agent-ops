"""GitLab (gitlab.com or self-managed): REST API v4 with a personal, project or group access token."""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from ..http import AdapterError, request
from .base import CodeHostAdapter


def _enc(repo: str) -> str:
    return urllib.parse.quote(urllib.parse.unquote(repo), safe="")


class GitLabHost(CodeHostAdapter):
    name = "gitlab"

    @property
    def api(self) -> str:
        return f"{self.url}/api/v4"

    def _call(self, method: str, path: str, body: Any = None, *, form: bool = False) -> Any:
        h = {"PRIVATE-TOKEN": self.token} if self.token else {}
        return request(method, self.api + path, h, body, form=form, secrets=[self.token])

    def _list(self, path: str) -> List[Dict[str, Any]]:
        rows = self._call("GET", path)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise AdapterError(0, f"unexpected answer from {path.split('?')[0]}")
        return rows

    @staticmethod
    def _mr(m: Dict[str, Any]) -> Dict[str, Any]:
        return {"iid": m.get("iid"), "title": m.get("title"), "source_branch": m.get("source_branch"),
                "target_branch": m.get("target_branch"), "sha": m.get("sha"), "web_url": m.get("web_url"),
                "author": (m.get("author") or {}).get("username"), "updated_at": m.get("updated_at"),
                "merged_at": m.get("merged_at"), "state": m.get("state"), "description": m.get("description"),
                "labels": m.get("labels") or []}

    def whoami(self) -> Dict[str, Any]:
        u = self._call("GET", "/user") or {}
        return {"username": u.get("username")}

    def list_merge_requests(self, repo: str, *, state: str = "opened", target_branch: Optional[str] = None,
                            updated_after: Optional[str] = None) -> List[Dict[str, Any]]:
        q = {"state": state, "per_page": 50, "order_by": "updated_at"}
        if target_branch:
            q["target_branch"] = target_branch
        if updated_after:
            q["updated_after"] = updated_after
        rows = self._list(f"/projects/{_enc(repo)}/merge_requests?" + urllib.parse.urlencode(q))
        return [self._mr(m) for m in rows]

    def get_merge_request(self, repo: str, iid: int) -> Dict[str, Any]:
        return self._mr(self._call("GET", f"/projects/{_enc(repo)}/merge_requests/{iid}") or {})

    def merge_request_files(self, repo: str, iid: int) -> List[str]:
        rows = self._list(f"/projects/{_enc(repo)}/merge_requests/{iid}/diffs?per_page=100")
        return [d.get("new_path") or d.get("old_path") for d in rows]

    def commits(self, repo: str, ref: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
        q = {"ref_name": ref, "per_page": 100}
        if since:
            q["since"] = since
        rows = self._list(f"/projects/{_enc(repo)}/repository/commits?" + urllib.parse.urlencode(q))
        return [{"sha": c.get("id"), "title": c.get("title"), "message": c.get("message"),
                 "author_name": c.get("author_name"), "created_at": c.get("created_at")} for c in rows]

    def statuses(self, repo: str, sha: str, name: Optional[str] = None) -> List[Dict[str, Any]]:
        q = {"all": "true", "per_page": 100}
        if name:
            q["name"] = name
        rows = self._list(f"/projects/{_enc(repo)}/repository/commits/{sha}/statuses?" + urllib.parse.urlencode(q))
        out = [{"id": s.get("id"), "status": s.get("status"), "name": s.get("name"),
                "description": s.get("description") or "", "created_at": s.get("created_at")} for s in rows]
        return sorted(out, key=lambda s: s["id"] or 0)

    def post_status(self, repo: str, sha: str, state: str, name: str, description: str,
                    target_url: Optional[str] = None) -> Dict[str, Any]:
        body = {"state": state, "name": name, "description": description[:250]}
        if target_url:
            body["target_url"] = target_url
        return self._call("POST", f"/projects/{_enc(repo)}/statuses/{sha}", body, form=True) or {}

    def update_merge_request(self, repo: str, iid: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        f = dict(fields)
        if isinstance(f.get("labels"), list):
            f["labels"] = ",".join(f["labels"])
        return self._mr(self._call("PUT", f"/projects/{_enc(repo)}/merge_requests/{iid}", f) or {})

    def close_merge_request(self, repo: str, iid: int) -> Dict[str, Any]:
        return self._mr(self._call("PUT", f"/projects/{_enc(repo)}/merge_requests/{iid}", {"state_event": "close"}) or {})

    def delete_note(self, repo: str, kind: str, iid: int, note_id: str) -> None:
        noun = "issues" if kind.startswith("issue") else "merge_requests"
        self._call("DELETE", f"/projects/{_enc(repo)}/{noun}/{iid}/notes/{note_id}")

    def clone_url(self, repo: str, with_token: bool = False) -> str:
        auth = f"oauth2:{self.token}@" if with_token and self.token else ""
        scheme, rest = self.url.split("://", 1)
        return f"{scheme}://{auth}{rest}/{urllib.parse.unquote(repo)}.git"
