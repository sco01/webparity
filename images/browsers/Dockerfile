# SPDX-License-Identifier: BSD-2-Clause
# The browser image: Ladybird, Chromium and Firefox over WebDriver BiDi under Xvfb.
# Phase 0 placeholder so that CI builds an image from the start; filled in during phase 1.
FROM debian:trixie-slim
LABEL org.opencontainers.image.title="webparity-browsers" \
      org.opencontainers.image.licenses="BSD-2-Clause"
RUN useradd --create-home --uid 2001 webparity
USER webparity
WORKDIR /home/webparity
