#!/usr/bin/env python3
"""Instant wake for the service agents on the agent host (launchd job <prefix>.agent-wake on macOS,
systemd service agent-wake on Linux).

n8n calls POST /wake/<agent> (header X-Wake-Token) on every task-board webhook. After a short
debounce that coalesces bursts, this starts the agent's job: on macOS `launchctl kickstart` of
<prefix>.agent-<agent> (prefix: AGENT_OPS_LABEL_PREFIX, default io.agent-ops), on Linux `systemctl
start --no-block agent@<agent>.service`. If that job is running, the wake is remembered and the job
is started again once it has finished. The agent's own lock and precheck make an extra run cheap,
so waking on every change is fine.

  GET  /health          -> 200 "ok" (no token)
  POST /wake/<agent>    -> 202 (valid token, allowed agent) | 401 | 404

Settings (environment, or the env file AGENT_OPS_ENV, default ~/.config/agent-ops/env):
  WAKE_TOKEN (required), WAKE_BIND (0.0.0.0), WAKE_PORT (8766), WAKE_AGENTS (triage),
  WAKE_DEBOUNCE seconds (10), WAKE_POLL seconds (5), WAKE_SUPERVISOR (launchd | systemd; default by
  platform), AGENT_OPS_LABEL_PREFIX (launchd, io.agent-ops). Python 3 stdlib only.
"""
import hmac
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_env(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                out[k] = v
    except OSError as e:
        print(f"{ts()} cannot read env file {path}: {e.strerror}", flush=True)
    return out


def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"{ts()} {msg}", flush=True)


ENV_FILE = os.environ.get("AGENT_OPS_ENV") or os.path.expanduser("~/.config/agent-ops/env")
CFG = {**load_env(ENV_FILE), **{k: v for k, v in os.environ.items() if k.startswith("WAKE_")}}
TOKEN = CFG.get("WAKE_TOKEN", "")
BIND = CFG.get("WAKE_BIND", "0.0.0.0")
PORT = int(CFG.get("WAKE_PORT", "8766"))
AGENTS = {a.strip() for a in CFG.get("WAKE_AGENTS", "triage").split(",") if a.strip()}
DEBOUNCE = float(CFG.get("WAKE_DEBOUNCE", "10"))
POLL = float(CFG.get("WAKE_POLL", "5"))
DOMAIN = f"gui/{os.getuid()}"
PREFIX = os.environ.get("AGENT_OPS_LABEL_PREFIX") or CFG.get("AGENT_OPS_LABEL_PREFIX") or "io.agent-ops"
SUPERVISOR = CFG.get("WAKE_SUPERVISOR") or ("launchd" if sys.platform == "darwin" else "systemd")


def label(agent):
    return f"{PREFIX}.agent-{agent}"


def unit(agent):
    return f"agent@{agent}.service"


def is_running(agent):
    try:
        if SUPERVISOR == "systemd":
            # a oneshot service is "activating" while its tick runs
            r = subprocess.run(["systemctl", "is-active", unit(agent)], capture_output=True, text=True, timeout=10)
            return r.stdout.strip() in ("active", "activating", "reloading")
        r = subprocess.run(["launchctl", "print", f"{DOMAIN}/{label(agent)}"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(l.strip() == "state = running" for l in r.stdout.splitlines())


def kickstart(agent):
    try:
        cmd = (["systemctl", "start", "--no-block", unit(agent)] if SUPERVISOR == "systemd"
               else ["launchctl", "kickstart", f"{DOMAIN}/{label(agent)}"])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        rc = r.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        rc = f"error {type(e).__name__}"
    log(f"kick {agent} rc={rc}")


class Waker:
    """Per agent: due = time the debounced wake fires; pending = wake that arrived while running."""

    def __init__(self):
        self.lock = threading.Lock()
        self.due = {}
        self.pending = set()
        self.last_poll = {}

    def wake(self, agent):
        with self.lock:
            first = agent not in self.due
            if first:
                self.due[agent] = time.monotonic() + DEBOUNCE
        log(f"wake {agent}" + ("" if first else " (coalesced)"))

    def step(self):
        now = time.monotonic()
        with self.lock:
            fire = [a for a, t in self.due.items() if t <= now]
            for a in fire:
                del self.due[a]
            waiting = [a for a in self.pending if now - self.last_poll.get(a, 0) >= POLL]
        for a in fire:
            if is_running(a):
                with self.lock:
                    self.pending.add(a)
                    self.last_poll[a] = now
                log(f"{a} is running, kick after it finishes")
            else:
                kickstart(a)
        for a in waiting:
            with self.lock:
                self.last_poll[a] = time.monotonic()
            if not is_running(a):
                with self.lock:
                    self.pending.discard(a)
                kickstart(a)

    def loop(self):
        while True:
            try:
                self.step()
            except Exception as e:  # never let the worker die
                log(f"worker error {type(e).__name__}: {e}")
            time.sleep(0.5)


WAKER = Waker()


class Handler(BaseHTTPRequestHandler):
    server_version = "agent-wake"

    def log_message(self, *args):  # no per-request access log; wakes/kicks are logged instead
        pass

    def reply(self, code, body):
        data = (body + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def drain(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        while n > 0:  # read and discard the body (never logged)
            chunk = self.rfile.read(min(n, 65536))
            if not chunk:
                break
            n -= len(chunk)

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self.reply(200, "ok")
        else:
            self.reply(404, "not found")

    def do_POST(self):
        self.drain()
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) != 2 or parts[0] != "wake":
            return self.reply(404, "not found")
        given = (self.headers.get("X-Wake-Token") or "").encode()
        if not TOKEN or not hmac.compare_digest(given, TOKEN.encode()):
            log("rejected wake: bad token")
            return self.reply(401, "unauthorized")
        agent = parts[1]
        if agent not in AGENTS:
            return self.reply(404, "unknown agent")
        WAKER.wake(agent)
        self.reply(202, "queued")


def main():
    if not TOKEN:
        log(f"WAKE_TOKEN is not set (env or {ENV_FILE}); every wake will be rejected")
    threading.Thread(target=WAKER.loop, daemon=True).start()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    log(f"listening on {BIND}:{PORT}, agents={','.join(sorted(AGENTS))}, debounce={DEBOUNCE:g}s")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
