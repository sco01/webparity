#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Starts Ladybird's WebDriver. HTTPS_PROXY/HTTP_PROXY send Ladybird through a recording proxy; WEBPARITY_CA is a
# certificate bundle that includes the proxy's CA.
set -eu
if [ ! -x "$LADYBIRD_BUILD/bin/WebDriver" ]; then
  echo "no Ladybird build at $LADYBIRD_BUILD (mount Ladybird's Build/release there, at the path it was built at)" >&2
  exit 1
fi
export XDG_RUNTIME_DIR=$(mktemp -d /tmp/runtime.XXXXXX)
PROFILE=$(mktemp -d /tmp/ladybird-profile.XXXXXX)
exec "$LADYBIRD_BUILD/bin/WebDriver" --headless --port "$LADYBIRD_WEBDRIVER_PORT" --profile-path="$PROFILE" \
  ${WEBPARITY_CA:+--certificate="$WEBPARITY_CA"}
