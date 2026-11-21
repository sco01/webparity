<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Third-party notices

Software that webparity uses at run time. None of it is included in this repository; it is installed when the
images are built or when the Python package is installed. Versions are the ones pinned or tested on 2026-11-21;
licenses were checked against each project's own license file or Debian copyright file.

## Python packages

| Package | Version | License | Used for | Where |
| --- | --- | --- | --- | --- |
| PyYAML | ≥ 6 (Debian `python3-yaml` in the image) | MIT | known differences and the configuration | worker image, `pip install webparity` |
| mitmproxy | 12.3.1 (official image `mitmproxy/mitmproxy:12.3.1`) | MIT | the recording proxy | proxy image |
| warcio | 1.8.2 | Apache-2.0 | writing and reading WARC files in the proxy | proxy image |
| cryptography | as bundled in the mitmproxy image | Apache-2.0 OR BSD-3-Clause | exporting the proxy CA's public key hash | proxy image |
| pytest, ruff | development only | MIT | tests and lint | not shipped |

warcio is Apache-2.0: if anyone redistributes the proxy image, its NOTICE file must go with it. Our code only calls it.

## Browsers and system packages

| Item | License | Where | Notes |
| --- | --- | --- | --- |
| Ladybird | BSD-2-Clause | mounted from the user's own build | not an official Ladybird release |
| Ladybird CI image `ghcr.io/ladybirdbrowser/ladybird-ci` | BSD-2-Clause (Ladybird), Ubuntu packages under their own licenses | base of the ladybird image | |
| Chromium (Debian) | BSD-3-Clause and others for bundled parts | reference image | copyright in `/usr/share/doc/chromium/copyright` |
| Firefox ESR (Debian) | MPL-2.0 and others for bundled parts | reference image | copyright in `/usr/share/doc/firefox-esr/copyright`; Mozilla trademark policy applies to the name |
| Xvfb, xauth | MIT/X11 | reference image | |
| tini | MIT | reference and worker images | |
| Debian base `debian:trixie-slim` | Debian Free Software Guidelines; per-package copyright files | all images except proxy | |

## Fonts (all images, from Debian packages, see `images/fonts/packages.txt`)

| Package | License |
| --- | --- |
| fonts-liberation | SIL OFL 1.1 |
| fonts-liberation-sans-narrow | GPL-2 with font exception |
| fonts-noto-core, fonts-noto-mono | SIL OFL 1.1 |
| xfonts-base | MIT-style |

If you publish built images, ship the Debian copyright files (`/usr/share/doc/*/copyright`) and warcio's NOTICE with
them, and say who built them.
