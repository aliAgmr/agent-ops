"""Small JSON-over-HTTP helper for the adapters (stdlib only). Errors never carry a token."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional


class AdapterError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message[:300]}")
        self.status = status


def redact(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[redacted]")
    return text


def request(method: str, url: str, headers: Dict[str, str], body: Any = None, *, form: bool = False,
            timeout: float = 20, secrets: Iterable[str] = ()) -> Any:
    """Sends one request and returns the decoded JSON (None for an empty body). `form`: send the
    body as application/x-www-form-urlencoded instead of JSON."""
    data = None
    h = {"Accept": "application/json", "User-Agent": "agent-ops", **headers}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (configured hosts only)
            raw = r.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        raise AdapterError(e.code, redact(msg, secrets)) from None
    except urllib.error.URLError as e:
        raise AdapterError(0, redact(f"{url.split('?')[0]}: {e.reason}", secrets)) from None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise AdapterError(0, f"not JSON from {url.split('?')[0]}") from None


def results(page: Any) -> list:
    """The list inside a paginated answer ({results: [...]}) or the answer itself."""
    if isinstance(page, dict) and "results" in page:
        return list(page.get("results") or [])
    return list(page or []) if isinstance(page, list) else []
