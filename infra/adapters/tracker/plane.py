"""Plane (self-hosted or cloud) as the task board: the REST API v1 with a personal access token.
Newer Plane serves work items under /work-items/, older releases under /issues/: every call tries
/work-items/ first and falls back once to /issues/ on a 404."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..http import AdapterError, request, results
from .base import TrackerAdapter


class PlaneTracker(TrackerAdapter):
    name = "plane"

    def __init__(self, base_url: str, workspace: str, token: str):
        super().__init__(base_url, workspace, token)
        self._states: Dict[str, List[Dict[str, Any]]] = {}
        self._labels: Dict[str, List[Dict[str, Any]]] = {}
        self._projects: Optional[List[Dict[str, Any]]] = None

    # ---- plumbing ---------------------------------------------------------------------------
    @property
    def api(self) -> str:
        return f"{self.base_url}/api/v1/workspaces/{self.workspace}"

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        h = {"X-API-Key": self.token}
        url = path if path.startswith("http") else self.api + path
        try:
            return request(method, url, h, body, secrets=[self.token])
        except AdapterError as e:
            if e.status == 404 and "/work-items/" in url:
                return request(method, url.replace("/work-items/", "/issues/"), h, body, secrets=[self.token])
            raise

    def _pages(self, path: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Every page of a cursor-paginated list (per_page=100)."""
        out: List[Dict[str, Any]] = []
        cursor = ""
        sep = "&" if "?" in path else "?"
        for _ in range(limit):
            page = self._call("GET", f"{path}{sep}per_page=100" + (f"&cursor={cursor}" if cursor else ""))
            out += results(page)
            if not (isinstance(page, dict) and page.get("next_page_results")):
                break
            cursor = page.get("next_cursor") or ""
            if not cursor:
                break
        return out

    # ---- identity and structure -------------------------------------------------------------
    def whoami(self) -> Dict[str, Any]:
        me = request("GET", f"{self.base_url}/api/v1/users/me/", {"X-API-Key": self.token}, secrets=[self.token]) or {}
        return {"id": me.get("id"), "email": (me.get("email") or "").strip().lower(),
                "display_name": me.get("display_name") or me.get("first_name") or ""}

    def projects(self) -> List[Dict[str, Any]]:
        if self._projects is None:
            self._projects = [{"id": p.get("id"), "identifier": p.get("identifier"), "name": p.get("name"),
                               "is_member": bool(p.get("is_member", True))} for p in self._pages("/projects/")]
        return self._projects

    def states(self, project_id: str) -> List[Dict[str, Any]]:
        if project_id not in self._states:
            self._states[project_id] = [{"id": s.get("id"), "name": s.get("name"), "group": s.get("group")}
                                        for s in results(self._call("GET", f"/projects/{project_id}/states/"))]
        return self._states[project_id]

    def labels(self, project_id: str) -> List[Dict[str, Any]]:
        if project_id not in self._labels:
            self._labels[project_id] = [{"id": l.get("id"), "name": l.get("name")}
                                        for l in results(self._call("GET", f"/projects/{project_id}/labels/"))]
        return self._labels[project_id]

    def members(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        path = f"/projects/{project_id}/members/" if project_id else "/members/"
        out = []
        for m in results(self._call("GET", path)):
            member = m.get("member") if isinstance(m.get("member"), dict) else m
            out.append({"id": member.get("id") or m.get("member") or m.get("id"),
                        "email": (member.get("email") or m.get("member__email") or "").strip().lower(),
                        "display_name": member.get("display_name") or ""})
        return out

    # ---- items ------------------------------------------------------------------------------
    def _identifier(self, project_id: str) -> str:
        return next((p["identifier"] for p in self.projects() if p["id"] == project_id), "") or ""

    def _item(self, project_id: str, w: Dict[str, Any]) -> Dict[str, Any]:
        sid = w.get("state")
        if isinstance(sid, dict):
            sid = sid.get("id")
        state = next((s for s in self.states(project_id) if s["id"] == sid), {})
        lmap = {l["id"]: l["name"] for l in self.labels(project_id)}
        label_ids = [x.get("id") if isinstance(x, dict) else x for x in (w.get("labels") or [])]
        ident = self._identifier(project_id)
        return {**w, "id": w.get("id"), "key": f"{ident}-{w.get('sequence_id')}" if ident and w.get("sequence_id") else None,
                "project_id": project_id, "state": sid, "state_name": state.get("name"), "state_group": state.get("group"),
                "label_ids": label_ids, "labels": [lmap.get(i, i) for i in label_ids],
                "assignees": [x.get("id") if isinstance(x, dict) else x for x in (w.get("assignees") or [])]}

    def list_items(self, project_id: str, *, state_names: Optional[List[str]] = None,
                   label_names: Optional[List[str]] = None, updated_since: Optional[str] = None) -> List[Dict[str, Any]]:
        items = [self._item(project_id, w) for w in self._pages(f"/projects/{project_id}/work-items/")]
        if state_names is not None:
            items = [i for i in items if i["state_name"] in state_names]
        if label_names is not None:
            items = [i for i in items if set(label_names) & set(i["labels"])]
        if updated_since:
            items = [i for i in items if (i.get("updated_at") or i.get("created_at") or "") > updated_since]
        return items

    def get_item(self, project_id: str, item_id: str) -> Dict[str, Any]:
        return self._item(project_id, self._call("GET", f"/projects/{project_id}/work-items/{item_id}/") or {})

    def get_item_by_key(self, key: str) -> Dict[str, Any]:
        w = self._call("GET", f"/work-items/{key.upper()}/") or {}
        pid = w.get("project") if isinstance(w.get("project"), str) else (w.get("project") or {}).get("id")
        return self._item(pid, w) if pid else w

    def update_item(self, project_id: str, item_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        return self._item(project_id, self._call("PATCH", f"/projects/{project_id}/work-items/{item_id}/", fields) or {})

    # ---- comments, links, history -----------------------------------------------------------
    def list_comments(self, project_id: str, item_id: str) -> List[Dict[str, Any]]:
        return results(self._call("GET", f"/projects/{project_id}/work-items/{item_id}/comments/"))

    def add_comment(self, project_id: str, item_id: str, html: str) -> Dict[str, Any]:
        return self._call("POST", f"/projects/{project_id}/work-items/{item_id}/comments/", {"comment_html": html}) or {}

    def update_comment(self, project_id: str, item_id: str, comment_id: str, html: str) -> Dict[str, Any]:
        return self._call("PATCH", f"/projects/{project_id}/work-items/{item_id}/comments/{comment_id}/", {"comment_html": html}) or {}

    def delete_comment(self, project_id: str, item_id: str, comment_id: str) -> None:
        self._call("DELETE", f"/projects/{project_id}/work-items/{item_id}/comments/{comment_id}/")

    def delete_link(self, project_id: str, item_id: str, link_id: str) -> None:
        self._call("DELETE", f"/projects/{project_id}/work-items/{item_id}/links/{link_id}/")

    def activities(self, project_id: str, item_id: str) -> List[Dict[str, Any]]:
        out = []
        for a in self._pages(f"/projects/{project_id}/work-items/{item_id}/activities/"):
            actor = a.get("actor")
            if isinstance(actor, dict):
                actor = actor.get("id")
            out.append({**a, "actor": actor or (a.get("actor_detail") or {}).get("id")})
        return out
