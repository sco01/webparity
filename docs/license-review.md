<!-- SPDX-License-Identifier: BSD-2-Clause -->
# License review

Phase 0 review, 2026-11-21. Every row was checked against the source named in it. The review is repeated in phase 7 against what actually ships, and then turned into `LICENSES.md` and `THIRD_PARTY_NOTICES.md`.

## Our own code

| Item | License | Source | Verdict |
| --- | --- | --- | --- |
| webparity | BSD-2-Clause | [LICENSE](../LICENSE), chosen 2026-11-21 | Same license as Ladybird. Every file carries an SPDX header, checked in CI. |

## Browsers and system packages in the image

| Item | Version checked | License | Source | Verdict |
| --- | --- | --- | --- | --- |
| Ladybird | master | BSD-2-Clause | `LICENSE` in the Ladybird repository | Fine. The image builds it from source. See the trademark section below. |
| Chromium (Debian package) | 156.0.8078.79 | BSD-3-Clause, plus Unicode and others for bundled parts | `/usr/share/doc/chromium/copyright` | Fine to install in an image built by the user. If we publish ready-made images, the Debian copyright files must ship with them. |
| Firefox ESR (Debian package) | 153.7.0esr | MPL-2.0, plus GPL-2/GPL-3/LGPL-2.1/MPL-1.1/MIT for bundled parts | `/usr/share/doc/firefox-esr/copyright` | Same as Chromium. MPL-2.0 is file-level copyleft and does not affect our code. The Firefox name and logo follow Mozilla's trademark policy; an unmodified Debian package is fine. |
| Xvfb | 21.1.16 | MIT/X11 | Debian package `xvfb` | Fine. |

## Fonts

Today's setup mounts the host's `/usr/share/fonts` into the container. All 287 fonts there come from Debian packages, and none of them is from `ttf-mscorefonts-installer`.

| Package | License | Source | Verdict |
| --- | --- | --- | --- |
| fonts-liberation | SIL OFL 1.1 (fonts), GPL-2+ (packaging) | `/usr/share/doc/fonts-liberation/copyright` | Redistributable. |
| fonts-liberation-sans-narrow | GPL-2 with font exception | `/usr/share/doc/fonts-liberation-sans-narrow/copyright` | Redistributable; the font exception keeps documents that embed it free of the GPL. |
| fonts-noto-core, fonts-noto-mono | SIL OFL 1.1 (fonts), GPL-3+ (packaging) | `/usr/share/doc/fonts-noto-core/copyright` | Redistributable. |
| xfonts-base | MIT-style | `/usr/share/doc/xfonts-base/copyright` | Redistributable. |

Decision: the image installs these packages instead of mounting the host's fonts, so every installation gets the same fonts.

## Candidate Python dependencies

None of these is used yet. Licenses as stated on PyPI on 2026-11-21.

| Package | Version | License | Verdict |
| --- | --- | --- | --- |
| warcio | 1.8.2 | Apache-2.0 | Allowed (WARC reading and writing). |
| mitmproxy | 12.3.1 | MIT | Allowed (recording proxy). |
| pywb | 2.11.1 | GPL-3.0 | **Not allowed** (copyleft). Use warcio and mitmproxy instead. |
| selenium | 4.53.0 | Apache-2.0 | Allowed, if we use it for BiDi. |
| websockets | 17.4 | BSD-3-Clause | Allowed (a direct BiDi client). |
| fastapi | 0.165.0 | MIT | Allowed (web interface). |
| starlette | 1.10.0 | BSD-3-Clause | Allowed. |
| uvicorn | 0.54.0 | BSD-3-Clause | Allowed. |
| jinja2 | 3.1.6 | BSD-3-Clause | Allowed. |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | Allowed. |
| pyyaml | 6.0.4 | MIT | Allowed (known differences as YAML). |
| httpx | 0.28.1 | BSD-3-Clause | Allowed (GitHub watcher). |
| pillow | 13.0.0 | MIT-CMU | Allowed (image comparison). |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Allowed. |

Apache-2.0 dependencies are compatible with BSD-2-Clause. If we ever redistribute them, their NOTICE files must ship too.

## Content that never ships

| Item | Why | Rule |
| --- | --- | --- |
| Test cases reduced from real sites | Can contain code and text from sites and themes | Only our own, hand-written test cases and fixtures. Checked in review. |
| Recordings (WARC), screenshots, DOM dumps | Sites' content is copyrighted and can contain personal data | Ignored by `.gitignore` (`data/`, `*.warc*`, `*.db`); never published. |
| Site lists | Our lists are personal | Only example lists from public sources. |
| Our root CA certificate and host configuration | Personal and host-specific | Never in the repository. |

## Text from How I test Ladybird

The gist ([How I test Ladybird](https://gist.github.com/sco01/6ec1ca5e5edbef8622fdbbe7165dd410)) states no license. The README links to it and doesn't copy its text. If text is to be copied into the documentation, its author gives it a license first.

## The Ladybird name

[Ladybird's trademark policy](https://ladybird.org/trademark/) (updated 2026-11-14) says:

- The name and logo may be used to discuss, review or document Ladybird, as long as it doesn't imply sponsorship or endorsement.
- Builds and packages made by others must clearly say who made them and that they are not official releases.
- Distinct derivative products must use their own names and logos.

What this means for us:

- The name webparity doesn't use the Ladybird name. The README describes webparity as testing Ladybird, which the policy allows.
- The README states that webparity is not affiliated with or endorsed by the Ladybird Browser Initiative.
- If we publish images that contain a Ladybird build, they say who built them and that they are not official Ladybird releases.
- We don't use the Ladybird logo.

## Open items

- [ ] A dependency license check in CI once there are dependencies (for example `pip-licenses` with an allow list).
- [ ] Repeat this review in phase 7 against what actually ships.
