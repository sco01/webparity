# SPDX-License-Identifier: BSD-2-Clause
# The worker, the nightly chain, the web interface and the alerts: the webparity package on Debian's Python.
# Built from the repository root: docker build -f images/worker/Dockerfile .
# fonts-liberation: the canary's font page serves Liberation Sans to the reference.
FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-yaml ca-certificates tini procps git fonts-liberation \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 2001 webparity
COPY src /opt/webparity/src
COPY known /opt/webparity/known
# The Ladybird checkout is mounted read-only at /src and owned by another user: let git read it
ENV TINI_SUBREAPER=1 PYTHONPATH=/opt/webparity/src WEBPARITY_DATA=/data PYTHONUNBUFFERED=1 \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=*
USER 2001
WORKDIR /data
ENTRYPOINT ["tini", "--", "python3", "-m", "webparity"]
CMD ["worker"]
