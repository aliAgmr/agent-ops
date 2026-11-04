# Stock Plane backend + patch_storage.py: presigned upload/download URLs get the request's
# X-Forwarded-Prefix (set by Traefik's strip, or by our Caddyfile), so the browser asks for
# /plane/uploads/… and the proxy strips it back to the path MinIO signed.
ARG VERSION
FROM makeplane/plane-backend:${VERSION}
COPY patch_storage.py /tmp/patch_storage.py
RUN python /tmp/patch_storage.py /code/plane/settings/storage.py && rm /tmp/patch_storage.py
