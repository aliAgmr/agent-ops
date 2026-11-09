"""The code-host interface (see infra/adapters/README.md). "Merge request" is a GitLab MR or a GitHub
pull request; `iid` is the MR iid or the PR number; `repo` is the routing.yaml `path`."""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

STATUS_STATES = ("pending", "success", "failed", "canceled")


class CodeHostAdapter:
    name = "base"

    def __init__(self, url: str, token: str = ""):
        self.url = (url or "").rstrip("/")
        self.token = token or ""

    @property
    def host(self) -> str:
        return self.url.split("://", 1)[-1].split("/", 1)[0]

    def whoami(self) -> Dict[str, Any]:
        raise NotImplementedError

    def list_merge_requests(self, repo: str, *, state: str = "opened", target_branch: Optional[str] = None,
                            updated_after: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_merge_request(self, repo: str, iid: int) -> Dict[str, Any]:
        raise NotImplementedError

    def merge_request_files(self, repo: str, iid: int) -> List[str]:
        raise NotImplementedError

    def merge_request_diffs(self, repo: str, iid: int) -> List[Dict[str, Any]]:
        """[{new_path, old_path, diff}] per changed file; `diff` is the unified diff's hunks
        ("" for a binary or too-large file)."""
        raise NotImplementedError

    def commits(self, repo: str, ref: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def statuses(self, repo: str, sha: str, name: Optional[str] = None) -> List[Dict[str, Any]]:
        """[{id, status (pending|success|failed|canceled|running), name, description, created_at}], oldest first."""
        raise NotImplementedError

    def post_status(self, repo: str, sha: str, state: str, name: str, description: str,
                    target_url: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def update_merge_request(self, repo: str, iid: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def close_merge_request(self, repo: str, iid: int) -> Dict[str, Any]:
        raise NotImplementedError

    def delete_note(self, repo: str, kind: str, iid: int, note_id: str) -> None:
        raise NotImplementedError

    def clone_url(self, repo: str, with_token: bool = False) -> str:
        raise NotImplementedError

    def git_auth_header(self) -> str:
        """`Authorization: …` for git over HTTPS (passed to git through GIT_CONFIG_* variables,
        never on a command line). Empty without a token."""
        if not self.token:
            return ""
        user = "x-access-token" if self.name == "github" else "oauth2"
        return "Authorization: Basic " + base64.b64encode(f"{user}:{self.token}".encode()).decode()
