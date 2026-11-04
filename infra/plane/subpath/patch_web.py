"""Gives Plane's web app a base path (admin and space already read theirs from VITE_*_BASE_PATH).

    python3 patch_web.py <plane source dir>

Each edit names the exact text it replaces and fails loudly if Plane changed it, so an upgrade
can't silently build an unpatched image.
"""
import sys
from pathlib import Path

src = Path(sys.argv[1])
BASE = 'joinUrlPath(process.env.VITE_WEB_BASE_PATH ?? "", "/") ?? "/"'

EDITS = [
    # React Router: route under the prefix (every Link/useRouter navigation goes through it).
    ("apps/web/react-router.config.ts",
     'import type { Config } from "@react-router/dev/config";\n',
     'import type { Config } from "@react-router/dev/config";\nimport { joinUrlPath } from "@plane/utils";\n\n'
     f"const basePath = {BASE};\n"),
    ("apps/web/react-router.config.ts",
     '  appDirectory: "app",\n',
     '  appDirectory: "app",\n  basename: basePath,\n'),
    # Vite: emit asset URLs under the prefix.
    ("apps/web/vite.config.ts",
     'import tsconfigPaths from "vite-tsconfig-paths";\n',
     'import tsconfigPaths from "vite-tsconfig-paths";\nimport { joinUrlPath } from "@plane/utils";\n'),
    ("apps/web/vite.config.ts",
     "export default defineConfig(() => ({\n",
     f"const basePath = {BASE};\n\nexport default defineConfig(() => ({{\n  base: basePath,\n"),
    # Manifests are plain strings, not imports.
    ("apps/web/app/layout.tsx",
     '<link rel="manifest" href="/site.webmanifest.json" />',
     '<link rel="manifest" href={`${import.meta.env.BASE_URL}site.webmanifest.json`} />'),
    ("apps/web/app/layout.tsx",
     '<link rel="manifest" href="/manifest.json" />',
     '<link rel="manifest" href={`${import.meta.env.BASE_URL}manifest.json`} />'),
    ("apps/web/app/root.tsx",
     '{ rel: "manifest", href: "/site.webmanifest.json" },',
     '{ rel: "manifest", href: `${import.meta.env.BASE_URL}site.webmanifest.json` },'),
    ("apps/web/app/root.tsx",
     '{ rel: "manifest", href: "/manifest.json" },',
     '{ rel: "manifest", href: `${import.meta.env.BASE_URL}manifest.json` },'),
    # The web Dockerfile doesn't pass VITE_WEB_BASE_PATH / VITE_API_BASE_PATH through.
    ("apps/web/Dockerfile.web",
     'ARG VITE_WEB_BASE_URL=""\n',
     'ARG VITE_WEB_BASE_PATH=""\nENV VITE_WEB_BASE_PATH=$VITE_WEB_BASE_PATH\n\nARG VITE_WEB_BASE_URL=""\n'),
]

for rel, old, new in EDITS:
    path = src / rel
    text = path.read_text()
    if text.count(old) != 1:
        sys.exit(f"patch_web: expected exactly one match in {rel} for {old!r}; Plane changed, update the patch")
    path.write_text(text.replace(old, new))
print("patch_web: web app patched for a base path")
