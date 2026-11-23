#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Starts Ladybird's WebDriver. With WEBPARITY_PROXY=host:port, Ladybird goes through the recording proxy and trusts
# its CA (waited for at /etc/webparity/proxy-ca.pem). The WebDriver log and the profile (with Ladybird's crash
# reports) go to /run/webparity/ladybird, which the worker reads.
#
# Every visit starts from an empty profile, like Chromium's new session and Firefox's new user context: the WebDriver
# shares one profile between its sessions, so on SIGUSR1 (sent by the worker or a probe, between sessions; they share
# this container's PID namespace) the WebDriver is stopped, the profile emptied (cookies, storage, HSTS, the HTTP cache;
# the crash reports stay) and the WebDriver started again. profile-generation changes with every start.
set -eu
if [ ! -x "$LADYBIRD_BUILD/bin/WebDriver" ]; then
  echo "no Ladybird build at $LADYBIRD_BUILD (mount Ladybird's Build/release there, at the path it was built at)" >&2
  exit 1
fi
export XDG_RUNTIME_DIR=$(mktemp -d /tmp/runtime.XXXXXX)
RUN=${WEBPARITY_RUN:-/run/webparity/ladybird}
mkdir -p "$RUN/profile"
CERT_ARG=""
if [ -n "${WEBPARITY_PROXY:-}" ]; then
  for _ in $(seq 1 300); do [ -s /etc/webparity/proxy-ca.pem ] && break; sleep 0.2; done
  if [ ! -s /etc/webparity/proxy-ca.pem ]; then echo "the proxy CA did not appear" >&2; exit 1; fi
  BUNDLE=$(mktemp /tmp/ca-bundle.XXXXXX.pem)
  cat /etc/ssl/certs/ca-certificates.crt /etc/webparity/proxy-ca.pem > "$BUNDLE"
  CERT_ARG="--certificate=$BUNDLE"
  export HTTPS_PROXY="http://$WEBPARITY_PROXY" HTTP_PROXY="http://$WEBPARITY_PROXY"
  export https_proxy="$HTTPS_PROXY" http_proxy="$HTTP_PROXY" NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
elif [ -n "${WEBPARITY_CA:-}" ]; then
  CERT_ARG="--certificate=$WEBPARITY_CA"
fi
clean_profile() {
  mkdir -p "$RUN/profile/data"
  find "$RUN/profile" -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {} +
  find "$RUN/profile/data" -mindepth 1 -maxdepth 1 ! -name CrashReports -exec rm -rf {} +
}
stop_child() {
  # The WebDriver runs in its own process group with the browsers it started
  if [ -n "$child" ]; then kill -TERM -- "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null || true; fi
}
child=""; reset=0
trap 'reset=1; stop_child' USR1
trap 'stop_child; exit 0' TERM INT
echo $$ > "$RUN/supervisor.pid"
while :; do
  clean_profile
  reset=0
  echo "=== WebDriver start $(date '+%F %T')" >> "$RUN/webdriver.log"
  setsid "$LADYBIRD_BUILD/bin/WebDriver" --headless --port "$LADYBIRD_WEBDRIVER_PORT" --profile-path="$RUN/profile" \
    $CERT_ARG >> "$RUN/webdriver.log" 2>&1 &
  child=$!
  date +%s%N > "$RUN/profile-generation.tmp" && mv "$RUN/profile-generation.tmp" "$RUN/profile-generation"
  while kill -0 "$child" 2>/dev/null; do wait "$child" || true; done
  child=""
  # A WebDriver that stopped by itself ends the container (which restarts), as before
  [ "$reset" = 1 ] || exit 1
done
