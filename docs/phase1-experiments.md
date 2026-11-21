<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Phase 1 experiments

The experiments that settle the open questions from the plan before the browser image and `webparity probe` are built. Run on the current host (Debian 13, Ladybird master from the nightly build, Chromium 156, Firefox ESR 153.7).

## 1. WebDriver BiDi events per frame (2026-11-21)

**Question:** is BiDi enough for console and network events in all frames, in all three browsers, or does Chromium need CDP?

**Setup:** a top document on `127.0.0.1` that logs, throws and fetches, with a cross-origin iframe on `localhost` that does the same. One BiDi client for all three browsers (Ladybird and Chromium through `webSocketUrl` on a WebDriver session, Firefox through BiDi directly), subscribed to `log.entryAdded`, `network.beforeRequestSent`, `network.responseCompleted`, `network.fetchError`, `browsingContext.contextCreated`, `browsingContext.navigationStarted`, `browsingContext.load` and `script.realmCreated`. Navigation with `browsingContext.navigate`, then 3 seconds of events.

**Result:**

| | Ladybird | Chromium | Firefox |
| --- | --- | --- | --- |
| All eight events accepted by `session.subscribe` | yes | yes | yes |
| Console entry from the top document and from the iframe, each with its own context | yes | yes | yes |
| JavaScript error from the top document and from the iframe, each with its own context and a stack trace | yes | yes | yes |
| `beforeRequestSent` for the iframe document and both fetches, with the right context | yes | yes | yes |
| `responseCompleted` for every request | no: missing for the iframe document | no: missing for both fetches (their bodies are never read) | yes |

**Conclusion:** BiDi is enough for console and script errors in all frames in all three browsers, with the frame identified by its browsing context. Chromium doesn't need CDP for this. Network completion events are incomplete in Ladybird and Chromium, so the recording proxy is the source of truth for network traffic, and BiDi network events are supplementary.

## 2. HTTP/2 and HTTP/3 through the recording proxy (2026-11-21)

**Question:** does recording through mitmproxy change what the browsers do?

**Setup:** mitmproxy 12.3.1 (official Docker image, MIT) as `mitmdump` on `127.0.0.1:8790`, writing every flow. Eight random sites the explorer had marked clean, each loaded with and without the proxy in all three browsers; element count, resource count, page height and `nextHopProtocol` per resource after 4 seconds.

- Ladybird: `HTTPS_PROXY`/`HTTP_PROXY` in the environment, and mitmproxy's CA appended to the certificate bundle given with `--certificate`.
- Chromium: `--proxy-server` and `--ignore-certificate-errors-spki-list` with the SPKI hash of mitmproxy's CA.
- Firefox: the WebDriver `proxy` capability in BiDi `session.new`, with `acceptInsecureCerts`.

**Result:**

| | Ladybird | Chromium | Firefox |
| --- | --- | --- | --- |
| Same elements, resources and page height with and without the proxy, on all 8 sites | yes | yes (one resource fewer on one site) | (not proxied) |
| HTTP/3 used directly | yes, on 4 of 8 sites | yes, on 4 of 8 sites | yes, on 5 of 8 sites |
| HTTP/3 through the proxy | no, HTTP/2 instead | no, HTTP/2 instead | (not proxied) |
| Traffic recorded per visit | 1.29 MB | 1.34 MB | 0 MB: the proxy capability had no effect |

**Conclusion:** recording through mitmproxy works for Ladybird and Chromium and doesn't change the pages, only the protocol (HTTP/3 becomes HTTP/2). Firefox ignores the `proxy` capability given in BiDi `session.new`; the image must configure its proxy and CA through profile preferences (`network.proxy.*` and an imported certificate) instead. Ladybird already supports HTTP/3, so the loss of HTTP/3 through the proxy is a known measurement limit: an HTTP/3-only bug would not show. A direct (unrecorded) visit stays possible for that case.

## 3. Size of a recorded visit (2026-11-21)

**Question:** how much storage does recording every visit need?

**Setup:** 23 random sites the explorer had marked clean, loaded in Chromium; the sum of `encodedBodySize` and `decodedBodySize` over the navigation and all resources.

**Result:** compressed (as on the wire) median 0.7 MB, mean 1.6 MB, max 7.2 MB per page load; decompressed median 2.0 MB, mean 2.6 MB, max 7.9 MB. These are underestimates: cross-origin resources without `Timing-Allow-Origin` report a size of 0.

**Estimate:** with three browsers and identical responses stored once, about 3 to 5 MB per visit. At about 9,000 visits a day, keeping every recording would need 27 to 45 GB a day. Keeping recordings only for visits with findings (about 9 % today) and canary pages, plus the visit bundles (about 4.7 MB each today), comes to roughly 6 to 8 GB a day. That fits four weeks of history on the current host. Measured through mitmproxy (experiment 2): 1.29 MB per visit for Ladybird and 1.34 MB for Chromium, so about 4 MB per visit for three browsers, inside the estimate.
