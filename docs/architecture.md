<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Architecture

```
               ┌────────────── one network and PID namespace ──────────────┐
  sites  ◄──── │ proxy (mitmproxy + WARC addon) ◄── Ladybird (WebDriver)    │
               │        ▲                       ◄── Chromium (chromedriver) │
               │        │ record / replay       ◄── Firefox (BiDi)          │
               │   worker ── WebDriver BiDi ──────────┘                     │
               │     │                                                      │
               └─────┼──────────────────────────────────────────────────────┘
                     ▼
               data/  visits/<day>/<id>/  (visit bundles)   recordings/   webparity.db
                     ▲
               web interface, alerts, nightly chain, CLI
```

## Collection

- **Browsers** speak WebDriver BiDi only (`webparity.bidi`, standard library). Ladybird and Chromium through a classic
  session with `webSocketUrl`, Firefox directly. No CDP.
- **A visit** (`webparity.visit`) runs declared steps (`load`, `probe`, `idle`, `interact`, `reload`) in all browsers
  in parallel. Each step's measurements go into the visit bundle under the browser; BiDi events (console entries,
  script errors, navigations) are written to `events.jsonl` tagged with step, browser and frame.
- **The proxy** records every request and response of the visit into a WARC file. It is the source of truth for
  network traffic (BiDi network events are incomplete in Ladybird and Chromium), and it can replay a visit offline.

## Analysis

- **Detectors** (`webparity.detectors`) read a bundle and return findings: a signature, a category (`error` makes a
  site a candidate, `info` is counted), a text and data. They never talk to a browser, so `webparity reanalyze`
  can run them again on saved bundles before a detector change goes live.
- **Known differences** (`known/*.yaml`) and per-site exceptions mark findings that are explained.

## Operations

- **The database** (SQLite, WAL) holds sites (the queue), visits, findings, candidates, issues, runs and alerts,
  with migrations.
- **Workers** take sites from the queue, one visit at a time per stack, and pause during quiet hours and high load.
- **The nightly chain** runs after the build: canary, regression watch, verification, GitHub watch, retirement,
  requeue, INDEX.md.

## Monitoring and issues

- The web interface shows health, findings, candidates, visits, issues and drafts, and exports Prometheus metrics.
- Alerts fire on a low visit rate, a failed night, or a browser endpoint down.
- Issues go draft → review → filed by a person → registered → watched on GitHub → verified against new builds.
