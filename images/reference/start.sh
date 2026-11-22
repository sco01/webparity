#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# Starts Xvfb, chromedriver and Firefox (BiDi). WEBPARITY_PROXY=host:port sends Firefox through a recording proxy;
# Chromium gets its proxy per session from the worker.
set -eu
# The same screen as Ladybird reports (1920 x 1080): pages that size things from screen.height came out differently
# with 3000 x 2000 (2026-11-22: Rellax parallax heroes on magasinetvasteras.se and pramenvasteras.se)
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
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

# Xvfb starts with the pointer in the middle of the screen, inside the browser windows: Chromium then fires a trusted
# mousemove on every page as its window appears, and sites that load scripts on the first interaction (mousemove,
# scroll ...) loaded them only in Chromium (2026-11-22: funktionsratt.se, missing-resources). The bottom-right corner is
# outside the 1440 x 900 and 390 x 844 windows. Moved once the browsers run (a move right after Xvfb's socket appears
# didn't stick), and checked.
for _ in $(seq 1 40); do
  xdotool mousemove 1919 1079 2>/dev/null && [ "$(xdotool getmouselocation 2>/dev/null | cut -d' ' -f1)" = "x:1919" ] && break
  sleep 0.5
done
[ "$(xdotool getmouselocation 2>/dev/null | cut -d' ' -f1)" = "x:1919" ] || echo "webparity-reference: could not move the pointer" >&2

# Exit (and let the container restart) as soon as any of the three stops.
wait -n
