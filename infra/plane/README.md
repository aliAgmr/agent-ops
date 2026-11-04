# Plane (self-hosted task board)

Plane Community Edition is the built-in task board (`routing.yaml` → `tracker: plane`), pinned in
`VERSION`, running in Docker on the agent host (macOS with Docker Desktop, or Linux with Docker
Engine and the compose plugin). Other trackers plug in through `infra/adapters/tracker/`; skip this
folder if you use Plane Cloud or another tracker.

## Keep the host always on

- **macOS:** Docker Desktop only runs while a user is logged in. System Settings → Users & Groups →
  *Automatically log in as* the agent user (FileVault blocks automatic login: turn it off or accept a
  manual login after reboots); Energy → *Prevent automatic sleeping*, *Start up automatically after a
  power failure*, *Wake for network access* (or `sudo pmset -a sleep 0 disksleep 0 autorestart 1
  womp 1`); Docker Desktop → *Start when you sign in*, Memory 8 GB or more (Plane runs ~12 containers).
- **Linux:** `systemctl enable --now docker`; nothing else (no login needed).
- Give the host a fixed address and point a DNS name at it (e.g. `plane.example.com`).

## Install / update

```bash
bash infra/plane/install.sh plane.example.com          # optional 2nd arg: HTTP port (default 80)
bash infra/plane/install.sh https://plane.example.com  # TLS at your reverse proxy
```

The script downloads the pinned compose file into `~/plane-selfhost/plane-app` (`PLANE_DIR`),
creates `plane.env` with generated passwords on the first run (Postgres, RabbitMQ, MinIO, secret keys;
they stay on the host, never in git), starts the containers and waits until Plane answers. Running it
again with a new `VERSION` upgrades; the old `plane.env` is copied to `archive/` first.

Then open `<your URL>/god-mode`, create the instance admin, turn off public sign-up and invite people.
Logs, backup, restart: `cd ~/plane-selfhost && ./setup.sh` (Plane's own menu, same folder).

## Reaching Plane from other networks (optional)

If the host sits on a network others can't reach, publish Plane on a gateway with an SSH remote
forward (`infra/macos/publish-tunnel.sh`, launchd `PUBLISH_TUNNEL=true` or the systemd
`agent-publish-tunnel` service): `gateway:8090 → this host:80`. Put your reverse proxy (Traefik,
nginx, Caddy) in front of the gateway port with TLS. The gateway's sshd needs `GatewayPorts
clientspecified` to bind 0.0.0.0.

### Serving Plane under a path (e.g. https://tools.example.com/plane)

Plane's stock images can't run under a prefix (their base paths are compiled in, and the web app has
none), so `infra/plane/subpath/` builds its own:

| File | What it does |
|---|---|
| `build.sh` | clones the `VERSION` source to `~/plane-selfhost/src`, builds `plane-{web,admin,space,backend}:<version>-<prefix>` |
| `patch_web.py` | gives the web app a base path (router `basename`, Vite `base`, manifests, Dockerfile arg) |
| `patch_storage.py` + `backend.Dockerfile` | presigned upload/download URLs get `X-Forwarded-Prefix`, so files load from `/plane/uploads/…` |
| `Caddyfile` | Plane's proxy: bare paths as before (the agents keep `http://<host>/api/`), `/plane/…` stripped like your reverse proxy does, `/` redirects to `/plane/`; `/agent-mcp/*` goes to the agent-mcp server on the host (`host.docker.internal:8765`) |
| `docker-compose.subpath.yaml` | layered over the stock compose file: the images above, the Caddyfile, and `host.docker.internal` on Linux |

```bash
bash infra/plane/subpath/build.sh                                              # 15–30 min the first time
PLANE_LOCAL_HOST=192.0.2.10 bash infra/plane/install.sh https://tools.example.com/plane
```

A path in the URL makes `install.sh` use the subpath images (it stops if they're missing for the
pinned version). Restart and upgrade with `install.sh`, not `setup.sh`'s menu (it only knows the stock
compose file); after changing `VERSION`, run `build.sh` again first. With an https WEB_URL, Plane's
cookies are secure: sign in through the https address. The agents keep using the host's own address
(`TRACKER_BASE_URL`), and so does the Plane → n8n webhook. On Linux, agent-mcp must listen on an
address the proxy container reaches (`AGENT_MCP_HOST` set to the Docker bridge address, firewalled).

## Configure (projects, states, labels, members)

By hand, once (not in Plane's public API):
- A workspace (instance admin → create workspace); its slug is `TRACKER_WORKSPACE`.
- An admin personal access token (avatar → Settings → Personal access tokens). SETUP.md stores it as
  `TRACKER_ADMIN_TOKEN` for setup only.
- One bot user per service agent (triage, release, nightly): invite each as workspace Member, sign up
  as the bot in a private window, create its personal access token (`TRACKER_TRIAGE_TOKEN`,
  `TRACKER_RELEASE_TOKEN`, `TRACKER_NIGHTLY_TOKEN`), and put the bot emails in people.yaml `service:`.

Then everything else comes from the repo's config (idempotent, never deletes):

```bash
PLANE_API_KEY="$TRACKER_ADMIN_TOKEN" bash infra/plane/configure.sh https://plane.example.com <workspace>
```

- One project per `projects.yaml` entry (identifier = its key), with members from `routing.yaml`
  (the first project gets the whole team), the bots, admins = the project's approvers, lead = its
  question owner. Override with `PLANE_PROJECT_CODE`, `PLANE_PROJECT_NAME`, `PLANE_PROJECT_MEMBERS`,
  `PLANE_PROJECT_ADMINS`, `PLANE_PROJECT_LEAD`. People are skipped until they accepted their invite:
  re-run after each one joins.
- States, in board order: Backlog (parked ideas), Findings (nightly findings), Inbox (default),
  Triaged, Todo, In Progress, In Review, Ready for E2E, Blocked, Done, Cancelled. An old `Backlog`
  default state with no `Inbox` is renamed to `Inbox` first.
- Labels: team:product, team:design, team:backend, team:frontend, team:data, team:devops; question,
  e2e:red, e2e:green; source:agent, source:human; lesson, directive; source:nightly,
  out-of-pipeline; effort:xs … effort:xl (set by triage); analysis:needed, analysis:done.
- Projects should be private (people can't join on their own). The public API ignores this setting;
  set it in project settings or with
  `docker exec -i plane-app-api-1 python manage.py shell -c 'from plane.db.models import Project; Project.objects.update(network=0)'`.

Still by hand: saved views (project → Views), e.g. one per team label, "Questions" (label
`question`), "Red E2E" (label `e2e:red`), "Ready for E2E" (state).

Webhook (for notifications and instant triage): `https://n8n.example.com/webhook/plane` (work items
+ comments). Plane blocks webhooks to private addresses by default; `install.sh` trusts its own host
(n8n usually runs there). Otherwise set `PLANE_WEBHOOK_ALLOWED_HOSTS` / `PLANE_WEBHOOK_ALLOWED_IPS`
and re-run it.

## Reporting (optional)

`bi/`: a read-only `bi` schema of clean views over Plane's database for Metabase or any BI tool
(see `bi/README.md`).

## Backup

`infra/macos/backup.sh` (launchd `BACKUP=true` or the systemd `agent-backup.timer`): Plane's
Postgres dump, uploads and `plane.env`, with n8n and the agents' config, into
`~/agent-backups/<date>/` (14 days; `OFFSITE_DIR` copies it off the host). Restore steps:
`infra/macos/README.md` → Backups.
