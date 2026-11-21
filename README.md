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
