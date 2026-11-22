# SPDX-License-Identifier: BSD-2-Clause
# The reference browsers: Chromium (with chromedriver) and Firefox ESR, both over WebDriver BiDi, in real windows under Xvfb.
# Headless Chromium announces itself as HeadlessChrome and gets different pages, so it runs in a window.
FROM debian:trixie-slim
COPY fonts/packages.txt /tmp/font-packages.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium chromium-driver firefox-esr xvfb xauth ca-certificates \
      python3 tini $(grep -v '^#' /tmp/font-packages.txt) \
 && rm -rf /var/lib/apt/lists/* /tmp/font-packages.txt
# xdotool moves Xvfb's pointer out of the browser windows at start (start.sh). A layer of its own, so adding it didn't
# reinstall the browsers above.
RUN apt-get update && apt-get install -y --no-install-recommends xdotool && rm -rf /var/lib/apt/lists/*
COPY fonts/local.conf /etc/fonts/local.conf
COPY reference/start.sh /usr/local/bin/webparity-reference
# Firefox trusts a proxy CA when one is mounted at /etc/webparity/proxy-ca.pem (enterprise policy).
RUN mkdir -p /usr/lib/firefox-esr/distribution /etc/webparity \
 && printf '{"policies": {"Certificates": {"Install": ["/etc/webparity/proxy-ca.pem"]}, "DisableAppUpdate": true, "DisableTelemetry": true}}\n' \
      > /usr/lib/firefox-esr/distribution/policies.json \
 && chmod 755 /usr/local/bin/webparity-reference \
 && useradd --create-home --uid 2001 webparity
USER webparity
WORKDIR /home/webparity
# Chromium's own sandbox (user namespaces + seccomp-bpf) stays on; it needs the container to allow namespaces, see
# compose.yaml. CHROME_DEVEL_SANDBOX empty: no setuid helper, so the container can run with no-new-privileges.
ENV CHROMEDRIVER_PORT=9515 FIREFOX_BIDI_PORT=9222 DISPLAY=:99 CHROME_DEVEL_SANDBOX=
ENTRYPOINT ["tini", "--", "webparity-reference"]
