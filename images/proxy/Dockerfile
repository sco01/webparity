# SPDX-License-Identifier: BSD-2-Clause
# The recording proxy: mitmproxy (MIT) with webparity's addon, which writes and replays WARC files (warcio, Apache-2.0).
# Proxy on 127.0.0.1:8790, control on 127.0.0.1:8791; see images/proxy/webparity_proxy.py.
FROM mitmproxy/mitmproxy:12.3.1
RUN pip install --no-cache-dir warcio==1.8.2 \
 && (getent passwd 2001 >/dev/null || useradd --create-home --uid 2001 webparity) \
 && mkdir -p /home/webparity/.mitmproxy /export /data/recordings \
 && chown 2001:2001 /home/webparity/.mitmproxy /export /data/recordings
COPY proxy/webparity_proxy.py /opt/webparity/webparity_proxy.py
USER 2001
ENV WEBPARITY_CA_EXPORT=/export WEBPARITY_RECORDINGS=/data/recordings
# connection_strategy=lazy: no upstream connection just because a browser opened a tunnel, so a replay stays offline.
# The CA key lives in the private confdir (a volume of its own); only the certificate is exported to /export.
ENTRYPOINT ["mitmdump", "--listen-host", "127.0.0.1", "--listen-port", "8790", \
            "--set", "confdir=/home/webparity/.mitmproxy", "--set", "connection_strategy=lazy", \
            "--set", "termlog_verbosity=warn", "--set", "stream_large_bodies=50m", \
            "-s", "/opt/webparity/webparity_proxy.py"]
