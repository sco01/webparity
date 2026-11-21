<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Security review

Phase 7 review of what webparity exposes, 2026-11-21. webparity loads arbitrary, untrusted websites in three
browsers, so the threat model is: **a visited page is hostile**. It must not reach the host, the other services, or
anything that leaves the installation.

## What listens where

All services share one network namespace (the `reference` container's). Nothing listens on the host's network except
the web interface, which is published on the host's `127.0.0.1:8700` only.

| Service | Address (inside the namespace) | Reachable by a visited page? | Protection |
| --- | --- | --- | --- |
| Ladybird WebDriver | 127.0.0.1:9616 | the page's browser can send requests to loopback | rejects requests with a foreign `Origin` (tested: 403) |
| chromedriver | 127.0.0.1:9515 | same | `--allowed-ips=127.0.0.1` and rejects a foreign `Origin` (tested: 403) |
| Firefox BiDi | 127.0.0.1:9222 | same | Firefox refuses WebSocket upgrades with a foreign `Origin` |
| Proxy | 127.0.0.1:8790 | it is the browsers' proxy | refuses requests to loopback, private, link-local and unspecified addresses |
| Proxy control | 127.0.0.1:8791 | yes | requires the `X-Webparity-Control` header; a page can't send it cross-origin without a CORS preflight, which the control server never answers |
| Web interface | 0.0.0.0:8700 in the namespace, host 127.0.0.1:8700 | yes (read-only) | GET/HEAD only; no state changes; no `Access-Control-Allow-Origin`, so a page can't read responses |

Known gap: the proxy blocks private addresses by the host name or literal IP in the request. A public name that
resolves to a private address (DNS rebinding) is not caught. The containers hold no secrets and have no capabilities,
which limits what such a request could reach.

## Containers

- Every container runs as an unprivileged user (uid 2001), with `cap_drop: ALL` and `no-new-privileges`.
- The `reference` container runs without Docker's seccomp and AppArmor profiles, because Chromium's own sandbox needs
  user namespaces. Chromium's and Firefox's sandboxes stay on; they are what isolates the page's renderer. This is a
  deliberate trade-off: the alternative (`--no-sandbox`) would leave renderers unsandboxed.
- Ladybird runs in Ladybird's CI image with the build mounted read-only.
- Browsers have memory limits (Ladybird 12 GB, reference 8 GB) and a low CPU weight.

## The proxy's certificate authority

- Each installation generates its own CA on first start, in the proxy's private volume (`proxy-conf`). The key never
  leaves that volume.
- Only the certificate is exported (`proxy-ca` volume, read-only for the browsers), with its public key hash for
  Chromium. The browsers trust it only inside their containers; nothing on the host trusts it.
- Upstream, mitmproxy verifies servers' certificates as usual.

## Data

- Visit bundles and recordings can contain what sites send to an anonymous visitor: cookies, tracking identifiers,
  and occasionally personal data. webparity never logs in, fills forms or sends personal data.
- Recordings are kept only for visits with findings (`proxy.keep: findings`) and are never published; `data/` is
  ignored by git.
- The web interface never serves saved DOMs or recordings, so site content is never rendered on our origin; it
  serves screenshots and text logs only, with a strict CSP (`default-src 'self'`).

## Outgoing

- GitHub: anonymous, read-only REST calls with a descriptive user agent and ETag caching; nothing is ever posted.
- Alerts: an optional webhook, and optional email through a local relay (plain SMTP, no TLS or login). Use a relay on
  the same host or network.
- No telemetry. Firefox's telemetry and data submission are switched off by policy and preferences.

## Checklist before publishing or exposing

- [x] The web interface listens on loopback by default.
- [x] No state-changing endpoints without authentication (there are none).
- [x] Output escaping and path-traversal tests (`tests/test_web.py`).
- [x] Proxy control cannot be driven by pages; private addresses are blocked.
- [x] No personal details, host names or addresses in the repository (checked with a grep for addresses, user names
      and paths; test files use documentation addresses).
- [ ] DNS rebinding through the proxy (see the known gap above).
- [ ] If the web interface ever gets forms (marking candidates, registering issues), add authentication and CSRF
      tokens first.
