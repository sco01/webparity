<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Inventory of today's scripts

Phase 0 inventory, 2026-11-21: every file in the current tooling (`ladybird-linux/` and the host's `/srv/ladybird/skript`) marked as **moves** (adapted into webparity), **rewritten** (the job stays, the code is new), **dropped** or **private** (stays with our own installation and is never published). File names are kept as they are today, many of them Swedish.

## Summary

| Group | Files | Decision |
| --- | --- | --- |
| Core scripts and configuration | 44 | 10 dropped, 8 moves (adapted), 6 private (never published), 20 rewritten |
| systemd units | 11 | rewritten as Compose services, except the backup units, which stay private |
| `prov-*` probe scripts | 213 | 10 move as `webparity probe` modes, the rest are dropped (one-off investigations) |
| `*.js` probe snippets in the repository | 13 | dropped (one-off investigations) |
| Files only on the host (`/srv/ladybird/skript`) | 120 | dropped (one-off investigations); kept in the host backup |

## Core scripts and configuration

| File | What it does | Decision | Where it goes |
| --- | --- | --- | --- |
| `PLAN-utforskaren.md` | The original explorer plan (Swedish) | Dropped | superseded by the webparity plan |
| `backup.sh` | Backup of the host to TrueNAS | Private (never published) | host-specific operations; stays in ladybird-linux |
| `bildlista2.py` | Lists image-difference findings for review | Dropped | the web interface's candidate list |
| `bygg.sh` | Builds Ladybird from master in the CI image | Rewritten | the build image / `webparity nightly` |
| `ddmin-skript.py` | ddmin over script parts, functions, lines, CSS rules or HTML parts for timing-dependent bugs | Moves (adapted) | `webparity reduce` |
| `dela-html.py` | Splits <body> into parts for ddmin | Moves (adapted) | `webparity reduce` |
| `<own>-root-ca.pem` (name withheld) | Our own root CA certificate | Private (never published) | never in the public repository |
| `fanga-minnesexplosion.sh` | Catches a WebContent memory explosion with stacks | Dropped | one-off investigation |
| `fanga-webcontent-signal.sh` | Attaches gdb to each new WebContent | Dropped | one-off investigation |
| `farre.py` | Analysis script for fewer-resources findings | Dropped | one-off analysis |
| `felrapp-besok.py` | Error reports from saved visits | Dropped | one-off analysis |
| `firefox.py` | Firefox over WebDriver BiDi | Moves (adapted) | the browser driver layer (BiDi for all three browsers) |
| `fonts-cr.conf` | Fontconfig for the reference Chromium on the host | Rewritten | fonts installed in the image |
| `fonts-local.conf` | Fontconfig for the Ladybird container (host fonts mounted) | Rewritten | fonts installed in the image from Debian packages |
| `fro` | Seed site lists (municipalities, agencies, large sites, Tranco) | Private (never published) | site lists stay private |
| `halvering.py` | First halving: page without scripts, CSS and iframes | Moves (adapted) | analysis step against the recording |
| `index-status` | Updates issue status in INDEX.md from GitHub | Rewritten | the GitHub watcher |
| `installera.sh` | Installs the scripts and services on the host | Dropped | replaced by Compose |
| `kanarie.py` | Canary pages that must trigger each detector | Rewritten | detector fixtures |
| `kontaktark.py` | Contact sheets: Ladybird and Chromium screenshots side by side | Moves (adapted) | the web interface's comparison view |
| `kor.sh` | Runs a script in the Ladybird CI image with Chromium, Firefox, fonts and CA certificate | Rewritten | the browser image and Compose |
| `lbdb` | Wrapper that runs lbdb.py as uid 2001 on the host | Dropped | replaced by the CLI inside the container |
| `lbdb.py` | SQLite database: sites, visits, findings, candidates, known differences; CLI | Rewritten | new schema with migrations and indexes; data imported in phase 3 |
| `lbprov` | The Mac client for the probe service (uploads test cases, fetches screenshots) | Rewritten | `webparity probe` |
| `maskin` | Host machine notes and setup | Private (never published) | host-specific |
| `matning.py` | The measurement library: visit steps, probe script, all detectors, KANDA patterns (3,000+ lines) | Rewritten | visit steps to collection, each detector to its own module under `detectors/` with fixtures, KANDA to `known/` as YAML |
| `minnesvakt.sh` | Memory watch: stacks from processes that grow too large | Rewritten | monitoring |
| `natt.sh` | The nightly chain: build, regression watch, canary, re-checks | Rewritten | `webparity nightly` and the scheduler |
| `prov` | Client for the probe service on the host | Rewritten | `webparity probe` |
| `rapport.py` | The explorer overview page (index.html) | Rewritten | the web interface |
| `reducera-css.py` | CSS rule ddmin | Moves (adapted) | `webparity reduce` |
| `reducera.py` | DOM ddmin with a caching proxy | Moves (adapted) | `webparity reduce`, against the recording |
| `registrera` | Registers a filed issue: index, known pattern, re-queueing | Rewritten | `webparity register` |
| `regressionsvakt.py` | Regression watch: a fixed set of sites against each new build | Rewritten | `webparity nightly` |
| `sajter.json` | The regression watch's site list | Private (never published) | site lists stay private; the repository gets example lists |
| `sakerhetsvakt.sh` | Security watch: flags crash types that need a second look | Rewritten | crash detector and alerts |
| `saknade.py` | Analysis script for missing-resources findings | Dropped | one-off analysis; the detector covers it |
| `sidtest.py` | Early single-page test | Dropped | superseded by `webparity probe` |
| `sparrade-vardar` | Hosts the Ladybird container may not reach | Private (never published) | per-installation configuration |
| `symbolisera.sh` | Symbolises a Ladybird crash report (build ID, addr2line) | Moves (adapted) | crash detector |
| `undersidor-fyll.py` | Queues subpages for visited sites | Rewritten | the scheduler |
| `utforskare.py` | The explorer worker: queue, visit, repeat visit, known patterns, saving artifacts | Rewritten | split into collection (`webparity visit`, workers) and analysis (`webparity reanalyze`) |
| `utforskare.sh` | Starts the explorer workers in containers | Rewritten | Compose services |
| `verifiera` | Nightly verification of our filed issues | Rewritten | `webparity verify` |

## systemd units

| Unit | Decision |
| --- | --- |
| `ladybird-backup.service` | Private (host backup, stays in ladybird-linux) |
| `ladybird-backup.timer` | Private (host backup, stays in ladybird-linux) |
| `ladybird-bygg.service` | Rewritten (Compose service or scheduler job) |
| `ladybird-minnesvakt.service` | Rewritten (Compose service or scheduler job) |
| `ladybird-natt.service` | Rewritten (Compose service or scheduler job) |
| `ladybird-natt.timer` | Rewritten (Compose service or scheduler job) |
| `ladybird-prov.service` | Rewritten (Compose service or scheduler job) |
| `ladybird-rapport.service` | Rewritten (Compose service or scheduler job) |
| `ladybird-rapport.timer` | Rewritten (Compose service or scheduler job) |
| `ladybird-utforskare@.service` | Rewritten (Compose service or scheduler job) |
| `ladybird-webb.service` | Rewritten (Compose service or scheduler job) |

## Probe scripts that move

| File | Becomes |
| --- | --- |
| `prov-besok-fynd.py` | one full visit and its findings (`webparity visit --dry-run`) |
| `prov-besok-loop.py` | visit-step instrumentation (`webparity probe --steps`) |
| `prov-css-rensa.py` | CSS pruning by what matches in Chromium |
| `prov-fas-loop.py` | visit phases in a loop |
| `prov-jsfel.py` | script errors of a page |
| `prov-jsmobil.py` | a script in mobile width in two browsers |
| `prov-jsstack.py` | stack traces of a script error |
| `prov-logg.py` | the whole Ladybird log of a visit with process numbers |
| `prov-mobil-loop.py` | repeat load, steps and mobile view for timing-dependent crashes |
| `prov-pendel-loop.py` | repeated window resizing for timing-dependent crashes |

## Dropped probe scripts (203)

One-off investigations from issue work. They stay in the private `ladybird-linux` repository and in the host backup.

`prov-404.py`, `prov-animkrasch.py`, `prov-anslutningar.py`, `prov-anv.py`, `prov-avcodec.py`, `prov-bidi.py`, `prov-blob.py`, `prov-blockskript.py`, `prov-cbstrypa.py`, `prov-chunkdiff.py`, `prov-cmdline.py`, `prov-colwrap.py`, `prov-cookiebot.py`, `prov-cpu.py`, `prov-cpu2.py`, `prov-cpu3.py`, `prov-cpu4.py`, `prov-cpu5.py`, `prov-cpu6.py`, `prov-cr.py`, `prov-crjs.py`, `prov-crtypsnitt.py`, `prov-css.py`, `prov-cssbisekt.py`, `prov-cssbisekt2.py`, `prov-cssddmin.py`, `prov-cssdiff.py`, `prov-cssdiff2.py`, `prov-cssminimal.py`, `prov-cssutdrag.py`, `prov-ddmin-hjul.py`, `prov-ddmin-lbprov.py`, `prov-ddmin.py`, `prov-delay.py`, `prov-delay2.py`, `prov-do.py`, `prov-dom.py`, `prov-ensamcell.py`, `prov-ensamcell2.py`, `prov-falskbody.py`, `prov-favikon.py`, `prov-felkalla.py`, `prov-felrapp.py`, `prov-fetch.py`, `prov-ffjs.py`, `prov-flexbild.py`, `prov-flexbild2.py`, `prov-flexbild3.py`, `prov-font.py`, `prov-fontface.py`, `prov-fontlank.py`, `prov-fontlank2.py`, `prov-fontlank3.py`, `prov-fontmeny.py`, `prov-frc.py`, `prov-fynd.py`, `prov-ga.py`, `prov-hallby.py`, `prov-halvering.py`, `prov-hang.py`, `prov-hjul.py`, `prov-hjultf.py`, `prov-hjultf2.py`, `prov-hover.py`, `prov-html.py`, `prov-htmlskript.py`, `prov-huvuden.py`, `prov-hydr.py`, `prov-insertrule.py`, `prov-inter.py`, `prov-io.py`, `prov-js.py`, `prov-jsasync.py`, `prov-jsbada.py`, `prov-jsddmin.py`, `prov-jskrasch.py`, `prov-kan2.py`, `prov-kanarie-ny.py`, `prov-kand.py`, `prov-kedja.py`, `prov-ko.py`, `prov-ko2.py`, `prov-kommentar.py`, `prov-kommentar2.py`, `prov-kommentar3.py`, `prov-konsol.py`, `prov-krasch.py`, `prov-kraschhalv.py`, `prov-kraschprefix.py`, `prov-kraschrapport.py`, `prov-krok.py`, `prov-layout-ny.py`, `prov-layout.py`, `prov-lazy.py`, `prov-lazy2.py`, `prov-listbox.py`, `prov-local.py`, `prov-markera.py`, `prov-minne.py`, `prov-minne2.py`, `prov-minne3.py`, `prov-minne4.py`, `prov-minne5.py`, `prov-minne6.py`, `prov-minne7.py`, `prov-minne8.py`, `prov-minnesexplosion.py`, `prov-minneshalv.py`, `prov-minnessvar.py`, `prov-mobilvy.py`, `prov-modullogg.py`, `prov-mqbyte.py`, `prov-mutationer.py`, `prov-mutationer2.py`, `prov-natfel.py`, `prov-nollhhea.py`, `prov-nollinline.py`, `prov-nrfel.py`, `prov-nyckel.py`, `prov-nyckel3.py`, `prov-nyckel4.py`, `prov-oanvand.py`, `prov-oppna.py`, `prov-overflod.py`, `prov-overflod2.py`, `prov-overflod3.py`, `prov-par.py`, `prov-prefetch.py`, `prov-proxy.py`, `prov-qtterm.py`, `prov-radavstand-typsnitt.py`, `prov-ram.py`, `prov-ramrull.py`, `prov-rattad.py`, `prov-regel.py`, `prov-regler.js`, `prov-regler.sh`, `prov-ren.py`, `prov-sajtrapport.py`, `prov-scb.py`, `prov-scroll.py`, `prov-scrollanim.py`, `prov-senstilmall.py`, `prov-senstilmall2.py`, `prov-sjalvsvg.py`, `prov-skarmbild.py`, `prov-skottkrasch.py`, `prov-skript.py`, `prov-skriptattr.py`, `prov-skriptbisekt.py`, `prov-skripthalv.py`, `prov-skriptprefix.py`, `prov-skriv.py`, `prov-skrivdom.py`, `prov-skrivjs.py`, `prov-skrivminne.py`, `prov-snabb.py`, `prov-sokskript.js`, `prov-sparadom.py`, `prov-sparr.py`, `prov-spegel.py`, `prov-stabil.py`, `prov-storlekhalv.py`, `prov-stroke.py`, `prov-sulkylokal.py`, `prov-sulkyregler.py`, `prov-svg.py`, `prov-svgbg.py`, `prov-svgflex.py`, `prov-symbol.py`, `prov-ta-bort.py`, `prov-tabellbyte.py`, `prov-testfall.py`, `prov-tf.py`, `prov-tf2.py`, `prov-tf3.py`, `prov-tf4.py`, `prov-tf5.py`, `prov-tf7.py`, `prov-tf8.py`, `prov-tfbild.py`, `prov-tfkrasch.py`, `prov-tfvar.py`, `prov-tidserie.py`, `prov-tillstand.py`, `prov-tjanst.py`, `prov-tv.py`, `prov-typsnitt.py`, `prov-typsnitt3.py`, `prov-typsnitt4.py`, `prov-upprepa.py`, `prov-urls.py`, `prov-vanta.py`, `prov-vantatf.py`, `prov-vercel.py`, `prov-vercel2.py`, `prov-vercel3.py`, `prov-video.py`, `prov-videoissue.py`, `prov-videopaus.py`, `prov-videorange.py`, `prov-videourl.py`, `prov-xhr.py`

## Dropped `*.js` snippets (13)

`clamp.js`, `clamp2.js`, `egenskapsregler.js`, `hyph1.js`, `io.js`, `kedja.js`, `knapp.js`, `overflod.js`, `persp1.js`, `prov-regler.js`, `prov-sokskript.js`, `regler.js`, `typsnittsprov.js`

## Files only on the host (120)

Probe snippets and notes written directly on the host. They are in the host backup on TrueNAS (2026-11-21).

`appit2.js`, `appit3.js`, `appit4.js`, `appit.js`, `bl1.js`, `bo1.js`, `bo2.js`, `bonff.js`, `bon.js`, `cl1.js`, `clip2.js`, `clip.js`, `details-sajter.txt`, `dialog2.js`, `ell1.js`, `ell2.js`, `ell3.js`, `emgcv.js`, `emgstil.js`, `emgtrad.js`, `ffdbg.js`, `ff-forestia.js`, `fk1.js`, `flytande-tabellrad-kommentar.html`, `fs.js`, `fvt.js`, `hyph-urval.txt`, `ingred.js`, `ingr.txt`, `jsregler.js`, `kand-14203.txt`, `kand-typsnitt8.txt`, `kand-typsnitt.txt`, `kand-urval.txt`, `kompositor-pseudo-transition.html`, `kort.js`, `layout.js`, `liga2.js`, `liga.js`, `lnum.js`, `lu1.js`, `m3-utan-flickity.js`, `m3-utan-initlogg.js`, `m3-utan-load.js`, `m3-utan-raf.js`, `minitema2.js`, `minitema3.js`, `minitema.js`, `mt-direkt-utan-dcl.js`, `mt-full.js`, `mt-log.js`, `mt-raf.js`, `mt-raf-utan-flickity.js`, `mt-raf-utan-io.js`, `mt-utan-flickity.js`, `mt-utan-io-flickity.js`, `mt-utan-io.js`, `nav.js`, `navprov.js`, `nollinline-urval.txt`, `ntm.js`, `persp2.js`, `persp.js`, `persp-urval.txt`, `prov-ct.py`, `prov-hjul2.py`, `prov-tf6.py`, `prov-webgl.py`, `rok.py`, `sof1.js`, `sof2.js`, `sov.js`, `spel.js`, `sponsor.js`, `sq1.js`, `steg1.html`, `steg2.html`, `steg2-r1.html`, `steg2-r2.html`, `steg2-r3.html`, `steg2-r4.html`, `steg2-r5.html`, `steg3.html`, `steg4-0.html`, `steg4-1600.html`, `steg4-2500.html`, `steg4-4000.html`, `steg4-800.html`, `steg5-jq1.html`, `steg5-jq2.html`, `steg6.html`, `stroke.js`, `stroke-sajter.txt`, `text.js`, `vt.js`, `wgalla.js`, `wg.js`, `wg-sajter.txt`, `wix14503.txt`, `wix5.txt`, `wixkrok2.js`, `wixkrok3.js`, `wixkrok4.js`, `wixkrok5.js`, `wixkrok6.js`, `wixkrok7.js`, `wixkrok.js`, `wixmat2.js`, `wixmat3.js`, `wixmat4.js`, `wixmat5.js`, `wixmat6.js`, `wixmat7.js`, `wixmat8.js`, `wixmat.js`, `wixov.js`, `wix.sql`, `wixvy2.js`, `wixvy.js`, `wk1.js`
