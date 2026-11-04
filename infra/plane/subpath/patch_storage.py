"""Makes Plane's presigned MinIO URLs honor X-Forwarded-Prefix (run at image build time).

    python patch_storage.py /code/plane/settings/storage.py

MinIO signs the bare path (/uploads/<key>); the browser has to ask for <prefix>/uploads/<key> so
Traefik routes it to Plane, and the prefix is stripped again before MinIO checks the signature.
Each edit fails loudly if Plane changed the code it replaces.
"""
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()

EDITS = [
    ("from urllib.parse import quote\n",
     "import re\nfrom urllib.parse import quote, urlsplit, urlunsplit\n"),
    ('    def __init__(self, request=None):\n',
     '    def __init__(self, request=None):\n'
     '        # Path prefix the browser sees (Traefik strip / Caddyfile), e.g. /plane\n'
     '        prefix = request.headers.get("X-Forwarded-Prefix", "") if request else ""\n'
     '        self.url_prefix = prefix.rstrip("/") if re.fullmatch(r"(/[A-Za-z0-9_-]+)+/?", prefix) else ""\n'),
    ("            return None\n\n        return response\n\n    def _get_content_disposition",
     "            return None\n\n        if self.url_prefix:\n"
     '            response["url"] = self._with_prefix(response["url"])\n'
     "        return response\n\n"
     "    def _with_prefix(self, url):\n"
     "        parts = urlsplit(url)\n"
     "        if parts.path.startswith(self.url_prefix + \"/\"):\n"
     "            return url\n"
     "        return urlunsplit(parts._replace(path=self.url_prefix + parts.path))\n\n"
     "    def _get_content_disposition"),
    ("        # The response contains the presigned URL\n        return response\n",
     "        # The response contains the presigned URL\n"
     "        return self._with_prefix(response) if self.url_prefix else response\n"),
]

for old, new in EDITS:
    if text.count(old) != 1:
        sys.exit(f"patch_storage: expected exactly one match for {old!r}; Plane changed, update the patch")
    text = text.replace(old, new)
path.write_text(text)
print("patch_storage: presigned URLs honor X-Forwarded-Prefix")
