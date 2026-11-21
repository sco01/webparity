<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Contributing

- Follow the ground rules in the [README](README.md). In particular, everything is written in English.
- Every source file starts with `SPDX-License-Identifier: BSD-2-Clause`. CI checks it with `tools/check_spdx.py`.
- A new dependency needs a license that is compatible with BSD-2-Clause. Copyleft licenses (GPL, AGPL) are not accepted. Add it to [docs/license-review.md](docs/license-review.md) with its license and source.
- Test cases and fixtures must be your own, hand-written pages. Do not commit code, text or images copied from websites, and never commit recordings, screenshots, DOM dumps or site lists from your own runs.
- Every false finding you fix becomes a negative fixture for its detector first.
