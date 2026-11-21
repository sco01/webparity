<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Licenses

webparity's own code, documentation, test cases and fixtures are under the BSD 2-Clause license ([LICENSE](LICENSE)).
Every file carries an SPDX header, checked in CI (`tools/check_spdx.py`).

The repository ships no third-party code. The images are built by whoever runs webparity, from public sources, and
are not published by this project. What they install, and under which licenses, is listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## What is never in the repository

| Item | Why |
| --- | --- |
| Recordings (WARC), screenshots, saved DOMs, visit bundles, the database | Sites' content is copyrighted and can contain personal data. `data/` and `*.warc*` are ignored by git. |
| Test cases reduced from real sites | Can contain sites' code and text. Only hand-written test cases and synthetic fixtures are committed. |
| Site lists | Lists of others' services; only small examples from public sources would be acceptable. |
| Certificates, keys and host configuration | Personal and host-specific. The proxy's CA key never leaves the proxy's private volume. |

## Copyleft

No dependency is copyleft. pywb (GPL-3.0) was considered for replay and rejected; replay is our own mitmproxy addon
with warcio (Apache-2.0). MPL-2.0 and GPL components inside Firefox ESR and Debian are installed as unmodified
Debian packages in a user-built image, which does not affect webparity's license.

## Trademarks

webparity is not affiliated with or endorsed by the Ladybird Browser Initiative, Google or Mozilla. It doesn't use
their logos. Images that contain a Ladybird build are built locally by whoever runs webparity and are not official
Ladybird releases ([Ladybird's trademark policy](https://ladybird.org/trademark/)).

The full review behind this page is in [docs/license-review.md](docs/license-review.md).
