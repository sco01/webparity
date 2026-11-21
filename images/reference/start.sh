#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# Starts Xvfb, chromedriver and Firefox (BiDi). WEBPARITY_PROXY=host:port sends Firefox through a recording proxy;
# Chromium gets its proxy per session from the worker.
set -eu
Xvfb :99 -screen 0 3000x2000x24 -nolisten tcp &
for _ in $(seq 1 50); do [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.1; done

chromedriver --port="$CHROMEDRIVER_PORT" --allowed-ips=127.0.0.1 &

# Firefox reads its enterprise policy (which installs the proxy CA) at start, so wait for the CA first.
if [ -n "${WEBPARITY_PROXY:-}" ]; then
  for _ in $(seq 1 300); do [ -s /etc/webparity/proxy-ca.pem ] && break; sleep 0.2; done
fi
PROFILE=$(mktemp -d /tmp/firefox-profile.XXXXXX)
{
  echo 'user_pref("browser.shell.checkDefaultBrowser", false);'
  echo 'user_pref("datareporting.policy.dataSubmissionEnabled", false);'
  echo 'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);'
  echo 'user_pref("browser.startup.homepage_override.mstone", "ignore");'
  if [ -n "${WEBPARITY_PROXY:-}" ]; then
    host=${WEBPARITY_PROXY%:*}; port=${WEBPARITY_PROXY##*:}
    echo 'user_pref("network.proxy.type", 1);'
    echo "user_pref(\"network.proxy.http\", \"$host\");"
    echo "user_pref(\"network.proxy.http_port\", $port);"
    echo "user_pref(\"network.proxy.ssl\", \"$host\");"
    echo "user_pref(\"network.proxy.ssl_port\", $port);"
    echo 'user_pref("network.proxy.no_proxies_on", "");'
    echo 'user_pref("network.http.http3.enable", false);'
  fi
} > "$PROFILE/user.js"
MOZ_CRASHREPORTER_DISABLE=1 firefox-esr --no-remote --profile "$PROFILE" --remote-debugging-port="$FIREFOX_BIDI_PORT" &

# Exit (and let the container restart) as soon as any of the three stops.
wait -n
