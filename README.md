<!-- SPDX-License-Identifier: BSD-2-Clause -->
# webparity

webparity loads real websites in Ladybird, Chromium and Firefox side by side, records every difference, and helps turn the differences that hold up into reduced test cases and bug reports.

> **Status: phase 0.** The repository skeleton exists; nothing works yet. See the plan for what comes next.

webparity is an independent project. It is not affiliated with or endorsed by the Ladybird Browser Initiative; "Ladybird" is their trademark, used here to describe what webparity tests.

It is a rebuild, as a reusable Docker Compose stack, of the setup described in [How I test Ladybird](https://gist.github.com/sco01/6ec1ca5e5edbef8622fdbbe7165dd410).

## Ground rules

These are built into the defaults and apply to anyone running webparity:

- Everything in the project is in English: code, comments, signatures, logs, documentation, CLI output and the web interface.
- No faked user agent and no bypassing of bot protection.
- Requests are rate limited per site. Reductions run against a local recording, not against the site.
- Nothing goes into a report that wasn't measured. A guess is labelled as a guess.
- Every issue is reviewed and filed by a person. webparity never posts on GitHub, and it never writes patches or opens pull requests against the engines it tests.

## Running the browsers (phase 1)

`compose.yaml` starts the three browsers: Chromium and Firefox in the `reference` image (Debian 13, real windows under Xvfb), and Ladybird's WebDriver in the `ladybird` image, built on Ladybird's own CI image. Ladybird itself is not in the image: point `LADYBIRD_BUILD` at a `Build/release` directory built in Ladybird's CI image.

```sh
LADYBIRD_BUILD=/path/to/ladybird/Build/release docker compose up --build
```

All three speak WebDriver BiDi on `127.0.0.1` inside the shared network namespace: Ladybird on 9616 and chromedriver on 9515 (BiDi through `webSocketUrl`), Firefox on 9222 directly. `tools/smoke_images.py` checks that each browser answers, that console entries arrive, and that all three use the same fonts.

The `reference` container runs without Docker's seccomp and AppArmor profiles, because Chromium's sandbox needs user namespaces. It has no capabilities and runs with `no-new-privileges`, and Chromium and Firefox keep their own sandboxes.

## Probing pages (phase 1)

`webparity probe` loads URLs or local test cases in the browsers and prints what a script returns in each. Run it where the browsers listen (inside the `reference` container's network namespace). Local files are served on `127.0.0.1`, and `localhost` is the same server on another origin, for cross-origin tests.

```sh
webparity probe testcase/test.html                          # the text of the test case's #out
webparity probe -j "[scrollY, document.title]" https://example.com/
webparity probe --browsers ladybird,chromium --mobile --json page.html
```

Without a script it waits up to 5 seconds for `#out` to get past a placeholder such as "running..." and prints its text, or the page title for sites.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python tools/check_spdx.py
.venv/bin/pytest
```

## License

BSD-2-Clause, see [LICENSE](LICENSE). Third-party licenses are documented in [docs/license-review.md](docs/license-review.md) until `LICENSES.md` and `THIRD_PARTY_NOTICES.md` are written before the first release.
