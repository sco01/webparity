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

## 2. HTTP/2 and HTTP/3 through the recording proxy

**Question:** does recording through mitmproxy change what the browsers do?

**Partial result (2026-11-21):** on 23 random sites, Chromium fetched 506 of 1,126 resources over HTTP/2 and 84 (7 %) over HTTP/3. Through an HTTP proxy, browsers tunnel with `CONNECT` over TCP, so HTTP/3 is not used. Ladybird's own HTTP/3 support still needs to be checked. Ladybird already accepts an extra root certificate (`LB_CA`), which the proxy needs.

**Not run yet:** mitmproxy is not installed on the host.

## 3. Size of a recorded visit (2026-11-21)

**Question:** how much storage does recording every visit need?

**Setup:** 23 random sites the explorer had marked clean, loaded in Chromium; the sum of `encodedBodySize` and `decodedBodySize` over the navigation and all resources.

**Result:** compressed (as on the wire) median 0.7 MB, mean 1.6 MB, max 7.2 MB per page load; decompressed median 2.0 MB, mean 2.6 MB, max 7.9 MB. These are underestimates: cross-origin resources without `Timing-Allow-Origin` report a size of 0.

**Estimate:** with three browsers and identical responses stored once, about 3 to 5 MB per visit. At about 9,000 visits a day, keeping every recording would need 27 to 45 GB a day. Keeping recordings only for visits with findings (about 9 % today) and canary pages, plus the visit bundles (about 4.7 MB each today), comes to roughly 6 to 8 GB a day. That fits four weeks of history on the current host. The real size per visit is measured again once the proxy runs.
