#!/usr/bin/env python3
"""Render a Figma frame to PNG, save it for the agent to look at, and attach it to a task-board item.

    python3 figma-shot.py <ITEM> <figma-url> [--no-attach]

ITEM is the item's key on the board (BIL-12). The URL is a figma.com/design|file/<key>/...?node-id=1-2 link
(a branch URL uses the branch key). Prints one JSON line:
    {"file": "/tmp/agent-analyst/BIL-12/1-2.png", "attachment": "uploaded|exists|skipped", ...}
`Read` the file to see the frame. The attachment is idempotent per frame (external_id = key:node),
so running it twice never attaches the same frame twice.

Used by the analyst agent (service/analyst). Read-only on Figma (FIGMA_RO_TOKEN); writes only the
attachment on the board (TRACKER_TRIAGE_TOKEN). Attachments are built for Plane (routing.yaml
`tracker: plane`); with another tracker the frame is saved and `attachment` is "skipped". Standard
library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SOURCE = "figma"
OUT_ROOT = os.environ.get("AGENT_FIGMA_DIR", "/tmp/agent-analyst")
MAX_BYTES = 5 * 1024 * 1024  # the board's attachment preview and the agent's image Read both cap near here


def die(msg: str, code: int = 1):
    print(json.dumps({"error": msg}))
    sys.exit(code)


def parse_figma(url: str) -> tuple[str, str]:
    """→ (file key, node id "1:2"). Raises ValueError for anything that isn't a frame link."""
    u = urllib.parse.urlparse(url)
    host = u.hostname or ""
    if host != "figma.com" and not host.endswith(".figma.com"):
        raise ValueError("not a figma.com link")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2 or parts[0] not in ("design", "file", "proto"):
        raise ValueError("only figma.com/design|file|proto links are supported")
    key = parts[3] if len(parts) >= 4 and parts[2] == "branch" else parts[1]
    node = (urllib.parse.parse_qs(u.query).get("node-id") or [""])[0].replace("-", ":")
    if not re.fullmatch(r"[A-Za-z0-9]{10,64}", key):
        raise ValueError("unexpected file key")
    if not re.fullmatch(r"I?\d+:\d+(;\d+:\d+)*", node):
        raise ValueError("the link has no node-id: open the frame in Figma and copy its link")
    return key, node


def item_ref(item: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}-\d{1,6}", item):
        raise ValueError("ITEM must look like BIL-12")
    return item


def http(req: urllib.request.Request, timeout=60) -> tuple[int, bytes]:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:  # Plane ~60/min per token; Figma rate limits too
                time.sleep(int(e.headers.get("Retry-After") or 20))
                continue
            return e.code, e.read()
    return 429, b""


def figma_png(key: str, node: str) -> bytes:
    tok = os.environ.get("FIGMA_RO_TOKEN") or die("FIGMA_RO_TOKEN is not set")
    for scale in (2, 1, 0.5):
        q = urllib.parse.urlencode({"ids": node, "format": "png", "scale": scale})
        code, body = http(urllib.request.Request(f"https://api.figma.com/v1/images/{key}?{q}",
                                                 headers={"X-Figma-Token": tok}))
        if code != 200:
            die(f"Figma images API → HTTP {code}: {body[:200].decode(errors='replace')}")
        img = (json.loads(body).get("images") or {}).get(node)
        if not img:
            die("Figma rendered nothing for that node (deleted, or not a frame?)")
        code, data = http(urllib.request.Request(img))
        if code != 200:
            die(f"downloading the render → HTTP {code}")
        if len(data) <= MAX_BYTES:
            return data
    die("the frame is too large to render under 5 MB even at scale 0.5; link a smaller frame")


def multipart(fields: dict, name: str, data: bytes, mime: str) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
               f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def plane_attach(item: str, key: str, node: str, name: str, data: bytes) -> str:
    base, ws = os.environ.get("TRACKER_BASE_URL"), os.environ.get("TRACKER_WORKSPACE")
    tok = os.environ.get("TRACKER_TRIAGE_TOKEN")
    if not (base and ws and tok):
        die("TRACKER_BASE_URL, TRACKER_WORKSPACE and TRACKER_TRIAGE_TOKEN must be set")
    api = f"{base}/api/v1/workspaces/{ws}"
    h = {"X-API-Key": tok, "Content-Type": "application/json"}

    def call(method, path, body=None):
        req = urllib.request.Request(api + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        return http(req, timeout=30)

    code, body = call("GET", f"/work-items/{item}/")
    if code != 200:
        die(f"Plane item {item} → HTTP {code}")
    wi = json.loads(body)
    pid, iid = wi.get("project") or wi.get("project_id"), wi["id"]
    code, body = call("POST", f"/projects/{pid}/work-items/{iid}/attachments/",
                      {"name": name, "type": "image/png", "size": len(data),
                       "external_source": SOURCE, "external_id": f"{key}:{node}"})
    if code == 409:
        return "exists"
    if code not in (200, 201):
        die(f"Plane attachment create → HTTP {code}: {body[:200].decode(errors='replace')}")
    b = json.loads(body)
    up = b["upload_data"]
    payload, ctype = multipart(up["fields"], name, data, "image/png")
    code, resp = http(urllib.request.Request(up["url"], data=payload, method="POST",
                                             headers={"Content-Type": ctype}))
    if code >= 300:
        die(f"attachment upload → HTTP {code}: {resp[:200].decode(errors='replace')}")
    code, _ = call("PATCH", f"/projects/{pid}/work-items/{iid}/attachments/{b['asset_id']}/", {"is_uploaded": True})
    if code >= 300:
        die(f"marking the attachment uploaded → HTTP {code}")
    return "uploaded"


def tracker_kind() -> str:
    """routing.yaml `tracker:` (default plane)."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        import adapters as A
        return str(A.load_routing().get("tracker") or "plane")
    except Exception:  # noqa: BLE001 - no config: assume the built-in tracker
        return "plane"


def main(argv: list[str]):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        die("usage: figma-shot.py <ITEM> <figma-url> [--no-attach]", 2)
    try:
        item, (key, node) = item_ref(args[0]), parse_figma(args[1])
    except ValueError as e:
        die(str(e), 2)
    data = figma_png(key, node)
    slug = node.replace(":", "-").replace(";", "_")
    d = os.path.join(OUT_ROOT, item)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{slug}.png")
    with open(path, "wb") as f:
        f.write(data)
    name = f"figma-{slug}.png"
    att = "skipped" if "--no-attach" in argv or tracker_kind() != "plane" else plane_attach(item, key, node, name, data)
    print(json.dumps({"file": path, "attachment": att, "name": name, "bytes": len(data),
                      "figma": f"https://www.figma.com/design/{key}/?node-id={slug}"}))


if __name__ == "__main__":
    main(sys.argv[1:])
