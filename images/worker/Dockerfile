# SPDX-License-Identifier: BSD-2-Clause
# The worker, the nightly chain, the web interface and the alerts: the webparity package on Debian's Python.
# Built from the repository root: docker build -f images/worker/Dockerfile .
FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-yaml ca-certificates tini procps \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 2001 webparity
COPY src /opt/webparity/src
COPY known /opt/webparity/known
ENV PYTHONPATH=/opt/webparity/src WEBPARITY_KNOWN=/opt/webparity/known WEBPARITY_DATA=/data PYTHONUNBUFFERED=1
USER 2001
WORKDIR /data
ENTRYPOINT ["tini", "--", "python3", "-m", "webparity"]
CMD ["worker"]
