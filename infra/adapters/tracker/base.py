"""The task-board interface (see infra/adapters/README.md). An adapter is constructed with the board's
base URL, its workspace and one token; every method acts as that token's user."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

STATE_GROUPS = ("backlog", "unstarted", "started", "completed", "cancelled")


class TrackerAdapter:
    name = "base"

    def __init__(self, base_url: str, workspace: str, token: str):
        self.base_url = (base_url or "").rstrip("/")
        self.workspace = workspace
        self.token = token

    # identity and structure
    def whoami(self) -> Dict[str, Any]:
        raise NotImplementedError

    def projects(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def project_id(self, identifier: str) -> Optional[str]:
        return next((p["id"] for p in self.projects() if p.get("identifier") == identifier), None)

    def states(self, project_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def labels(self, project_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def members(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    # items
    def list_items(self, project_id: str, *, state_names: Optional[List[str]] = None,
                   label_names: Optional[List[str]] = None, updated_since: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_item(self, project_id: str, item_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_item_by_key(self, key: str) -> Dict[str, Any]:
        raise NotImplementedError

    def update_item(self, project_id: str, item_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    # comments, links, history
    def list_comments(self, project_id: str, item_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def add_comment(self, project_id: str, item_id: str, html: str) -> Dict[str, Any]:
        raise NotImplementedError

    def update_comment(self, project_id: str, item_id: str, comment_id: str, html: str) -> Dict[str, Any]:
        raise NotImplementedError

    def delete_comment(self, project_id: str, item_id: str, comment_id: str) -> None:
        raise NotImplementedError

    def delete_link(self, project_id: str, item_id: str, link_id: str) -> None:
        raise NotImplementedError

    def activities(self, project_id: str, item_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError
