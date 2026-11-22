<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Getting started

You need a Linux host with Docker (with the Compose plugin), about 16 GB of memory for one stack, and a Ladybird build
made in Ladybird's CI image (so its binaries find their libraries at `/src/Build/release`).

## 1. Build Ladybird

Build Ladybird's release preset inside `ghcr.io/ladybirdbrowser/ladybird-ci` with the source checked out at `/src`,
so the build ends up in `/src/Build/release` inside the container. Keep that directory on the host, for example
`~/ladybird/Build/release`. Optionally write `webparity-build.json` next to it:

```json
{"commit": "58932c0cbf0", "date": "2026-11-21 01:36:04"}
```

## 2. Configure

```sh
git clone https://github.com/sco01/webparity && cd webparity
mkdir -p data && cp config.example.yaml data/config.yaml   # then edit
```

## 3. Start

```sh
LADYBIRD_BUILD=~/ladybird/Build/release docker compose up -d --build
```

This starts the browsers, the recording proxy, one worker, the web interface (<http://127.0.0.1:8700>) and the
alerts. Without the Compose plugin, `tools/docker-run-stack.sh up 1` starts the same containers with `docker run`.

## 4. Add sites

```sh
docker compose run --rm worker sites add https://example.org/ path/to/site-list.txt
docker compose run --rm worker sites            # the queue
```

The worker visits queued sites one at a time. Each visit loads the page in Ladybird and Chromium at the same time,
measures, interacts, reloads, records the traffic, runs the detectors and stores a visit bundle under
`data/visits/`. Sites with error findings become candidates:

```sh
docker compose run --rm worker candidates
```

## 5. Investigate

- `webparity visit URL` visits one site and prints its findings.
- `webparity probe page.html -j "expression"` runs a script in all browsers. `--before SCRIPT` and `--after S` run
  something first, `--wheel [SELECTOR]` sends one wheel step, `--screenshot` saves a screenshot per browser, and
  `--cross-origin` loads local test cases from `localhost` instead of `127.0.0.1`. Ladybird's crash, panic and
  VERIFY lines during each target are printed with it. The workers keep their browsers busy (Ladybird's WebDriver
  allows one session at a time), so for probes start a stack without a worker:
  `NO_WORKER=1 tools/docker-run-stack.sh up probe`, and run the probe in its network.
- `webparity replay VISIT_ID` replays a recorded visit offline and checks that the page is the same.
- `webparity reduce VISIT_OR_FILE -o case.html --differs -j "expression"` reduces a page to a small test case.
- `webparity draft SIGNATURE` writes an issue draft; a person reviews it and files it; `webparity register DRAFT N`
  links it to the issue.

## 6. The nightly chain

Run `webparity nightly` once a night after your Ladybird build (a cron job or systemd timer on the host calling
`docker compose run --rm worker nightly`). It runs the canary pages, the regression sites, verifies filed issues,
refreshes them from GitHub, retires candidates that went away (status `gone`; a candidate that is seen again, gone or fixed, becomes `new` with a "back" note) and requeues candidates for the new build.

## More parallel visits

One stack is one visit at a time (about 60 to 100 visits an hour depending on the sites). For more, start more
projects on the same data directory:

```sh
LADYBIRD_BUILD=... docker compose -p webparity-2 up -d reference ladybird proxy worker
```

Keep the load reasonable: the ground rules limit visits per site, and the worker pauses when the load average is
above `worker.max_load`.
