<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Detectors

A detector reads a visit bundle and returns findings; it never talks to a browser, so `webparity reanalyze` can run it again on saved bundles. Each detector is a module in `src/webparity/detectors/` and has fixtures in `tests/fixtures/detectors/<name>/`. The measurements they read are in [measurements.md](measurements.md).

Ladybird is compared with one reference browser (Chromium when it took part). Before comparing, every comparison detector checks the **gate** (`detectors._common.gate`), in the old explorer's order:

1. No usable reference (missing, behind a bot wall, an error page, a failed probe): only what Ladybird can tell on its own (crash, missing features, `blocked:ladybird-only`).
2. The browsers ended on different sites: only `different-final-site`.
3. Ladybird got an error page or a bot wall: only `not-loading:<reason>`.

Only `error` findings make a site a candidate. A finding whose `known` is set (see [Known differences](#known-differences)) is kept but is not a candidate.

## The detectors

| Detector | Signatures | Category |
| --- | --- | --- |
| `crash` | `crash:<location>`, `security:<location>` | error |
| `missing_features` | `missing-feature:<log text>` | info |
| `page` | `blocked:ladybird-only`, `different-final-site`, `cookie-banner-locks-page`, `object-fallback`, `overflow-hidden` | info |
| | `not-loading:<http2\|ssl\|connection\|dns\|timeout\|other>` | error |
| | `not-loading:crash`, `not-loading:xml-feed` | info |
| | `less-content`, `shorter-page`, `error-reports`, `error-text:<text>`, `overflow`, `empty-root`, `zero-size-text`, `off-page-text` | error |
| `network` | `more-resources:<initiator>:<type>`, `fewer-resources`, `failed-loads`, `missing-resources`, `network-loop:<type>` | error |
| | `resource-buffer-full`, `reference-smaller-page`, `missing-resources-few`, `renavigation` | info |
| `layout` | `layout:<missing\|extra\|width\|height\|position>:<parent display>><display>` | error |
| | `layout-differs-a-lot` | info |
| `mobile` | `mobile-layout:<kind>:<parent>><display>`, `mobile-overflow` | error |
| | `mobile-overflow-hidden`, `mobile-overflow-above` | info |
| `image` | `image-differs` (info with a layout finding), `blank-page`, `empty-iframe`, `empty-canvas`, `broken-images`, `font-error[:<formats>]` | error |
| | `cookie-banner-differs`, `font-not-loaded-in-reference`, `font-replaced`, `font-local` | info |
| `css` | `css-rules`, `css-dropped:<property>:<value kind>` (info unless important and visible) | error |
| | `css-rules-known`, `missing-feature:css:<property>` | info |
| `script_error` | `script-error:<message>` | error |
| | `script-error-iframe`, `missing-feature:js:<message>` | info |
| `scroll` | `scroll-lazy-content`, `scroll-locked` | error |
| `menu` | `menu` | error |
| `video` | `video:not-started` | error |
| `performance` | `memory-leak`, `cpu-idle`, `slow-response`, `slow-load` | error |
| | `memory-high`, `unstable` | info |
| (any) | `detector-error:<name>` when a detector raised | info |

Each module's docstring has the exact thresholds. In short:

- **crash**: the crash location from the log (a Rust panic's file and message, `VERIFY`/`ASSERT` expression and file, or the signal), else from Ladybird's crash reports (`SIGSEGV in WebContent`); `unknown location` when the session ended without a crash line. `security:` when the crash may be more than a controlled abort: a memory-error signal (SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGTRAP), a crash in another process than WebContent, an invalid-value assertion, or JIT/GC code.
- **script_error**: Ladybird's exceptions from the log before the reload and from the BiDi events of the steps before the reload, in the top document only; the reference had none.
- **layout**: the element where a difference starts, from the two layout maps, masked by the reference's reload map. At most two groups; more than eight groups is one info finding.
- **image**: 32 px blocks of the first screenshot, with blocks that move (load vs idle, load vs reload, in either browser) masked.
- **css**: rule counts per stylesheet; dropped declarations from the interact step's CSS comparison.
- **network**: resource lists, Ladybird's failed loads, Resource Timing repeats, BiDi navigation events.
- **performance**, **scroll**, **menu**, **video**, **mobile**: from the steps of the same names.

## Pitfalls become tests

Every heuristic was tuned against false findings in the old explorer. Each pitfall below has a negative fixture (the directory name is under `tests/fixtures/detectors/`); a new false finding becomes a fixture before the detector is fixed.

| Pitfall | Rule | Fixture |
| --- | --- | --- |
| Exceptions from cross-origin iframes (2026-11-08, 2026-11-21) | only exceptions BiDi places in the top document; info otherwise | `script_error/iframe-exceptions-info` |
| An iframe's exception from a CDN the top document never used (2026-11-21) | host heuristics when the events can't place it | `script_error/frame-host-heuristic` |
| Exceptions when leaving the page for the reload (2026-11-21) | only the log before `log_reload_offset` | `script_error/after-reload-offset-not-counted` |
| The reference throws too | no `script-error` when the reference had exceptions | `script_error/reference-also-throws` |
| Callbacks from a document that is not fully active (2026-11-17) | not counted | `script_error/not-fully-active-not-counted` |
| `TODO(...)`, not implemented, unknown PermissionName | `missing-feature:js:`, info | `script_error/not-implemented-is-missing-feature` |
| A VERIFY trap (SIGILL/SIGTRAP) is a controlled abort (2026-11-10) | not `security:` | `crash/verify-trap-report-not-security` |
| Out-of-memory is never a security bug | not `security:` | `crash/out-of-memory-not-security` |
| Inline boxes are fragment unions and differ between engines (2026-11-09) | not compared by size | `layout/inline-not-compared` |
| The reference's own variation (ads, A/B tests) (2026-11-09) | elements that differ between the reference's loads are masked | `layout/unstable-in-reference-masked` |
| content-visibility: hidden in the reference: Ladybird measures 0 x 0 (2026-11-09) | not counted | `layout/hidden-in-reference-not-missing` |
| Fixed and sticky layers (cookie banners, headers) | not counted | `layout/fixed-layer-not-compared` |
| Text wraps differently with other font metrics | height tolerance of 3.2 x font size for text | `layout/text-wrap-height-tolerated` |
| Lazy loading below the first screens | only the first 2,700 px | `layout/below-depth-not-compared` |
| Many groups: different variants of the page | one info finding | `layout/many-groups-info` |
| A difference already seen in desktop width | not repeated in mobile width | `mobile/already-in-desktop` |
| Boxes entirely above the page in mobile width (2026-11-10) | Chromium doesn't count them: info | `mobile/overflow-from-boxes-above-info` |
| Overflow the user can't scroll to (2026-11-10) | info | `page/overflow-hidden-is-info`, `mobile/mobile-overflow-hidden-info` |
| Moving blocks (carousels, ads) | masked | `image/moving-blocks-masked` |
| An image difference caused by a layout difference (2026-11-09) | info | `image/layout-makes-it-info` |
| A cookie banner in only one browser | `cookie-banner-differs`, info | `image/cookie-banner-differs-info` |
| A font the reference didn't load either (2026-11-08) | info | `image/font-not-loaded-in-reference-info` |
| Another @font-face for the same family loaded (2026-11-08) | info | `image/font-replaced-info` |
| next/font's local-only "X Fallback" (2026-11-08) | info | `image/font-local-fallback-info` |
| Rules Ladybird drops for known reasons (2026-11-09) | `css-rules-known`, info | `css/known-reasons-info` |
| A dropped declaration without a visible difference (2026-11-09) | info | `css/dropped-without-visible-difference-info` |
| Harmless drops: text-stroke 0, focus ring, base-select, unitless perspective | info | `css/harmless-drops-info` |
| An unsupported property is a missing feature | `missing-feature:css:`, info | `css/unsupported-property-missing-feature` |
| Link prefetches (Speculation Rules) in the reference (2026-11-08) | not counted | `network/link-prefetch-not-fewer` |
| A full Resource Timing buffer | no fewer/missing comparison | `network/buffer-full-no-fewer` |
| The reference got a much smaller page (headless wall) (2026-11-08) | no `more-resources` | `network/reference-smaller-page-suppresses-more-resources` |
| A CMS directory that changes per request (2026-11-08) | resource keys with digits replaced | `network/changing-directory-not-missing` |
| Scripts the page removed from the DOM (2026-11-21) | only those in the final DOM | `network/removed-from-dom-not-missing` |
| Elementor modules loaded by a tracking pixel's getter scan (2026-11-16) | not counted | `network/elementor-lazy-modules-not-missing` |
| One or two missing scripts: consent-dependent tracking (2026-11-08) | info | `network/missing-resources-few-is-info` |
| A host that doesn't resolve (2026-11-10) | not a failed load | `network/unresolvable-host-not-failed` |
| A cookie banner that locks only Ladybird's page (2026-11-08) | info, no `shorter-page` | `page/cookie-lock-no-shorter-page` |
| Ladybird-only bot walls (2026-11-08) | info | `page/blocked-only-in-ladybird` |
| The browsers ended on different sites (2026-11-08) | nothing compared | `page/different-final-site` |
| A crash makes the page not load (2026-11-10); RSS/Atom feeds | info | `page/not-loading-after-crash-is-info`, `page/not-loading-xml-feed-is-info` |
| Memory that levels off (2026-11-08) | `memory-high`, info | `performance/memory-levels-off-info` |
| The reference is busy too | no `cpu-idle` | `performance/reference-busy-too` |
| Different click methods in the menu test (2026-11-08) | not compared | `menu/different-click-method` |
| Ladybird loaded lazy content before scrolling (2026-11-08) | not `scroll-lazy-content` | `scroll/already-loaded-not-lazy` |
| The reference didn't scroll either | not `scroll-locked` | `scroll/reference-did-not-scroll` |

Measurement-side pitfalls are handled in `webparity.measure` and `webparity.js` and noted there with their dates: `<option>` text excluded from the text length, map tiles not mapped, an instant (not smooth) scroll reset before the wheel test, the pointer moved away after the wheel and menu tests, the main renderer process chosen with a busy loop, the reference's own reload for noise masking.

## Known differences

`webparity.known.Known.mark(bundle, findings)` sets `finding.known` from three sources:

- **Signature entries** in `known/*.yaml` (`known/signatures.yaml` and the signature-only entries in the area files): a prefix or regex that is explained everywhere.
- **Page tests** in `known/*.yaml` (89 entries with `id` and `js` or `log`, ported from the old explorer's known patterns): the JS runs at the end of the reload step when a preliminary analysis has an error finding the pattern can explain; `log` tests are a regex on the Ladybird log. An entry with `fixed: <commit>` applies only to builds without the commit (`git merge-base --is-ancestor` in `config.ladybird.source`, default `/src`).
- **Site exceptions** in the database table `site_exceptions` (domain or `*`, signature prefix, reason).

## Old signatures

`webparity.signatures.translate_signature` maps every signature of the old explorer to its webparity name (`krasch:SIGSEGV i WebContent` → `crash:SIGSEGV in WebContent`, `layout:saknas:block>block` → `layout:missing:block>block`), and `translate_status` maps candidate, site and visit statuses, so imported findings, candidates, known differences and issues keep matching.
