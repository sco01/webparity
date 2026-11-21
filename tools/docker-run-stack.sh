#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# The same stack as compose.yaml with plain `docker run`, for hosts without the Compose plugin.
#   tools/docker-run-stack.sh up N      start stack N (N=1 also starts the web interface and the alerts)
#   tools/docker-run-stack.sh down N    stop and remove stack N
# Environment: LADYBIRD_BUILD (required), LADYBIRD_SOURCE (optional checkout containing it), WEBPARITY_DATA (default ./data), WEBPARITY_BUILD_INFO (optional file),
# WEBPARITY_WEB_PORT (default 8700), TAG (image tag, default dev).
set -euo pipefail
ACTION=${1:?up or down}; N=${2:-1}
P=wp$N; TAG=${TAG:-dev}
DATA=$(realpath "${WEBPARITY_DATA:-./data}")
H=(--security-opt no-new-privileges:true --cap-drop ALL --restart unless-stopped -v /etc/localtime:/etc/localtime:ro)

if [ "$ACTION" = down ]; then
  for s in alerts web worker ladybird proxy reference; do docker rm -f "$P-$s" >/dev/null 2>&1 || true; done
  exit 0
fi
: "${LADYBIRD_BUILD:?set LADYBIRD_BUILD to a Ladybird Build/release directory}"
mkdir -p "$DATA/recordings"
PORTS=(); [ "$N" = 1 ] && PORTS=(-p "127.0.0.1:${WEBPARITY_WEB_PORT:-8700}:8700")
# The worker reads the Ladybird checkout (read-only) for "is this fix in the build"; without it, only the build
SRC=(-v "$LADYBIRD_BUILD:/src/Build/release:ro"); [ -n "${LADYBIRD_SOURCE:-}" ] && SRC=(-v "$LADYBIRD_SOURCE:/src:ro")
BUILD_INFO=(); [ -n "${WEBPARITY_BUILD_INFO:-}" ] && BUILD_INFO=(-v "$WEBPARITY_BUILD_INFO:/run/webparity/build-info:ro" -e WEBPARITY_BUILD_INFO=/run/webparity/build-info)

docker run -d --name "$P-reference" --shm-size 2g --cpu-shares 256 --memory 8g \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt no-new-privileges:true \
  --cap-drop ALL --restart unless-stopped -e WEBPARITY_PROXY=127.0.0.1:8790 -v /etc/localtime:/etc/localtime:ro \
  -v "$P-proxy-ca:/etc/webparity:ro" "${PORTS[@]}" "webparity-reference:$TAG" >/dev/null
NET=(--network "container:$P-reference")
docker run -d --name "$P-proxy" "${H[@]}" "${NET[@]}" \
  -v "$P-proxy-conf:/home/webparity/.mitmproxy" -v "$P-proxy-ca:/export" -v "$DATA/recordings:/data/recordings" \
  "webparity-proxy:$TAG" >/dev/null
docker run -d --name "$P-ladybird" "${H[@]}" "${NET[@]}" --pid "container:$P-reference" --shm-size 2g \
  --cpu-shares 256 --memory 12g -e WEBPARITY_PROXY=127.0.0.1:8790 \
  -v "$LADYBIRD_BUILD:/src/Build/release:ro" -v "$P-proxy-ca:/etc/webparity:ro" -v "$P-ladybird-run:/run/webparity/ladybird" \
  "webparity-ladybird:$TAG" >/dev/null
docker run -d --name "$P-worker" --stop-timeout 120 "${H[@]}" "${NET[@]}" --pid "container:$P-reference" \
  -e WEBPARITY_CONFIG=/data/config.yaml -e WEBPARITY_WORKER="$(hostname)-$N" "${BUILD_INFO[@]}" \
  -v "$DATA:/data" -v "$P-proxy-ca:/etc/webparity:ro" -v "$P-ladybird-run:/run/webparity/ladybird:ro" \
  "${SRC[@]}" "webparity-worker:$TAG" worker >/dev/null
if [ "$N" = 1 ]; then
  docker run -d --name "$P-web" "${H[@]}" "${NET[@]}" -e WEBPARITY_CONFIG=/data/config.yaml -v "$DATA:/data" \
    "webparity-worker:$TAG" web --listen 0.0.0.0:8700 >/dev/null
  docker run -d --name "$P-alerts" "${H[@]}" "${NET[@]}" -e WEBPARITY_CONFIG=/data/config.yaml -v "$DATA:/data" \
    "webparity-worker:$TAG" alerts >/dev/null
fi
echo "stack $N started"
