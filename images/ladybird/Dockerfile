# SPDX-License-Identifier: BSD-2-Clause
# Ladybird's WebDriver in Ladybird's own CI image, so the build runs where it was built. The build is mounted read-only
# at the path it was built at, /src/Build/release, because its binaries find their libraries through an absolute RUNPATH. The fonts are copied from the same Debian packages as the reference image.
FROM debian:trixie-slim AS fonts
COPY fonts/packages.txt /tmp/font-packages.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends $(grep -v '^#' /tmp/font-packages.txt) \
 && rm -rf /var/lib/apt/lists/*

FROM ghcr.io/ladybirdbrowser/ladybird-ci:2026.11.20
RUN rm -rf /usr/share/fonts/*
COPY --from=fonts /usr/share/fonts/ /usr/share/fonts/
COPY fonts/local.conf /etc/fonts/local.conf
COPY ladybird/start.sh /usr/local/bin/webparity-ladybird
RUN fc-cache -f >/dev/null \
 && chmod 755 /usr/local/bin/webparity-ladybird \
 && (getent passwd 2001 >/dev/null || useradd --create-home --uid 2001 webparity)
USER 2001
ENV LADYBIRD_BUILD=/src/Build/release LADYBIRD_WEBDRIVER_PORT=9616 HOME=/tmp QT_QPA_PLATFORM=offscreen
ENTRYPOINT ["webparity-ladybird"]
