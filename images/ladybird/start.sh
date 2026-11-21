#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Starts Ladybird's WebDriver. With WEBPARITY_PROXY=host:port, Ladybird goes through the recording proxy and trusts
# its CA (waited for at /etc/webparity/proxy-ca.pem). The WebDriver log and the profile (with Ladybird's crash
# reports) go to /run/webparity/ladybird, which the worker reads.
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
echo "=== WebDriver start $(date '+%F %T')" >> "$RUN/webdriver.log"
"$LADYBIRD_BUILD/bin/WebDriver" --headless --port "$LADYBIRD_WEBDRIVER_PORT" --profile-path="$RUN/profile" \
  $CERT_ARG >> "$RUN/webdriver.log" 2>&1 &
child=$!
trap 'kill $child 2>/dev/null' TERM INT
wait $child
