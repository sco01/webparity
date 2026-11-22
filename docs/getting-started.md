<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Getting started

You need:

- a Linux host (x86_64) with Docker and the Compose plugin,
- about 16 GB of memory for one stack, plus what the Ladybird build needs (it uses all cores for about an hour the first
  time and about 30 GB of disk with its caches),
- a Ladybird build made in Ladybird's own CI image, as described below. webparity runs that build in the same image,
  because the binaries find their libraries through absolute paths under `/src/Build/release`.

Everything runs in containers as uid 2001; nothing is installed on the host.

## 1. Build Ladybird

```sh
git clone https://github.com/LadybirdBrowser/ladybird ~/ladybird
cd ~/ladybird
export LADYBIRD_CI_TAG=$(cat Meta/Docker/ci/VERSION)
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/src" -w /src \
  ghcr.io/ladybirdbrowser/ladybird-ci:$LADYBIRD_CI_TAG ./Meta/ladybird.py build
```

The build ends up in `~/ladybird/Build/release` (inside the container `/src/Build/release`). `ladybird.py` refuses to
run as root, hence `--user`. To rebuild after `git pull`, run the same `docker run` again; keep `LADYBIRD_CI_TAG` in step
with `Meta/Docker/ci/VERSION`, since webparity builds its Ladybird image from the same tag.

Optionally describe the build for the reports, in the build directory:

```sh
git log -1 --format='{"commit": "%h", "date": "%cd"}' --date=format:'%Y-%m-%d %H:%M:%S' > Build/release/webparity-build.json
```

## 2. Configure

```sh
git clone https://github.com/sco01/webparity && cd webparity
mkdir -p data/recordings && cp config.example.yaml data/config.yaml   # then edit
sudo chown -R 2001:2001 data
```

The containers run as uid 2001, so `data/` must be theirs (create `data/recordings` first, or Docker creates it as
root). In `data/config.yaml`, at least set `github.user_agent` to something that says who runs this instance; the rest
has working defaults (see the comments in the file and `src/webparity/config.py`).

## 3. Start

```sh
export LADYBIRD_BUILD=~/ladybird/Build/release LADYBIRD_SOURCE=~/ladybird
export LADYBIRD_CI_TAG=$(cat ~/ladybird/Meta/Docker/ci/VERSION)
docker compose up -d --build
```

This starts the browsers, the recording proxy, one worker, the web interface (<http://127.0.0.1:8700>, on the host
only) and the alerts. `LADYBIRD_SOURCE` is optional: with the checkout, webparity can tell whether a fix is in the build
under test. Without the Compose plugin, `tools/docker-run-stack.sh up 1` starts the same containers with `docker run`.

Check that it works:

```sh
docker compose run --rm worker sites add https://example.org/
docker compose logs -f worker      # a line per visit: domain, seconds, findings or "clean"
```

## 4. Add sites

```sh
docker compose run --rm worker sites add https://example.org/ path/to/site-list.txt
docker compose run --rm worker sites            # the queue
```

A site list is a text file with one URL or domain per line (put it in `data/` so the container can read it, and pass
`/data/your-list.txt`). The worker visits queued sites one at a time. Each visit loads the page in Ladybird and
Chromium at the same time, measures, interacts, reloads, records the traffic, runs the detectors and stores a visit
bundle under `data/visits/`. Sites with error findings become candidates, shown in the web interface and here:

```sh
docker compose run --rm worker candidates
```

## 5. Investigate

Ladybird's WebDriver allows one session at a time, and the worker keeps its browsers busy. For your own probes, start a
second project without a worker (on another web port) and run the commands in it:

```sh
WEBPARITY_WEB_PORT=8701 docker compose -p webparity-probe up -d reference ladybird proxy
docker compose -p webparity-probe run --rm worker probe /data/case.html -j "document.title"
docker compose -p webparity-probe down
```

- `webparity visit URL` visits one site and prints its findings.
- `webparity probe page.html -j "expression"` runs a script in all browsers. `--before SCRIPT` and `--after S` run
  something first, `--wheel [SELECTOR]` sends one wheel step, `--screenshot` saves a screenshot per browser, and
  `--cross-origin` loads local test cases from `localhost` instead of `127.0.0.1`. Ladybird's crash, panic and
  VERIFY lines during each target are printed with it.
- `webparity replay VISIT_ID` replays a recorded visit offline and checks that the page is the same.
- `webparity reduce VISIT_OR_FILE -o case.html --differs -j "expression"` reduces a page to a small test case.
- `webparity draft SIGNATURE` writes an issue draft; a person reviews it on the review page and files it;
  `webparity register DRAFT N` links it to the issue.

## 6. The nightly chain

Run `webparity nightly` once a night after your Ladybird build (a cron job or systemd timer on the host calling
`docker compose run --rm worker nightly`). It runs the canary pages, the regression sites, verifies filed issues,
refreshes them from GitHub, retires candidates that went away (status `gone`; a candidate that is seen again, gone or
fixed, becomes `new` with a "back" note) and requeues candidates for the new build. The workers pause during
`worker.quiet_hours`, so a build in that window gets the CPU.

## More parallel visits

One stack is one visit at a time (about 60 to 100 visits an hour depending on the sites). For more, start more
projects on the same data directory:

```sh
WEBPARITY_WEB_PORT=8702 docker compose -p webparity-2 up -d reference ladybird proxy worker
```

Each project needs its own `WEBPARITY_WEB_PORT`, since every reference container publishes the (unused) web port.

Keep the load reasonable: the ground rules limit visits per site, and the worker pauses when the load average is
above `worker.max_load`.

## Troubleshooting

- **Ladybird doesn't start** (`docker compose logs ladybird`): the build and the image must come from the same CI image
  tag. Rebuild the image with `LADYBIRD_CI_TAG` set as in step 3 (`docker compose build ladybird`).
- **Permission denied under /data**: `data/` isn't owned by uid 2001 (step 2).
- **Every visit is slow or fails**: check the proxy (`docker compose logs proxy`) and the host's load; the worker waits
  while the load average is above `worker.max_load`.
