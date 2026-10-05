#!/bin/sh
set -eu
: "${CI_COMMIT_SHA:?}"
test "$(id -u)" = 1001
test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
image="localhost/nginx-container:$CI_COMMIT_SHA"
name="ac-nginx-check-$CI_COMMIT_SHA"
cleanup() { podman rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
podman build --network=slirp4netns \
  --label "org.opencontainers.image.revision=$CI_COMMIT_SHA" \
  -f Dockerfile -t "$image" .
podman run --rm --network none "$image" nginx -t
podman run --rm --network none "$image" nginx -V
podman run -d --network none --name "$name" "$image"
attempt=0
until podman exec "$name" wget -q -O /dev/null http://127.0.0.1/; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 10 ]; then
        podman logs "$name"
        exit 1
    fi
    sleep 1
done
podman stop --time 15 "$name"
test "$(podman inspect --format '{{.State.ExitCode}}' "$name")" = 0
echo "NGINX_IMAGE_OK: $CI_COMMIT_SHA"
sh ops/nginx/local-transfer/export-image.sh
