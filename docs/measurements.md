<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Measurements

What each visit step (`webparity.measure`) stores per browser in `visit.json` under `browsers.<browser>`, and the files it writes into the bundle. The in-page scripts are in `webparity.js`; the same script runs in every browser, so the measurements are symmetric. A failing measurement is recorded as `<key>_error` (or `error`) and the step goes on.

Steps run in order, each in all browsers in parallel: `load`, `probe`, `idle`, `interact`, `reload`. `interact` and `reload` need `visit.deep`. `should_run` skips a step in a browser when its load failed without any elements, skips `idle`, `interact` and `reload` in a reference behind a bot wall, and skips `interact` and `reload` everywhere when Ladybird got an error page.

The *reference* is Chromium when it takes part, else the first other browser. The CSS comparison and the known-bug page tests need Ladybird's and the reference's results together; the steps meet through `ctx.shared` (with timeouts, so a failing browser never blocks the other).

## load

| Key | Meaning |
| --- | --- |
| `seconds` | time for `browsingContext.navigate` to return (wait `complete`) |
| `waited` | seconds waited afterwards until the page was stable |
| `stable` | elements, resources and text length unchanged for two seconds and `readyState` complete (within `visit.stable_timeout`) |
| `error` | the navigation error, if any (a cancelled navigation counts as loaded) |

In Ladybird the first load also records where Ladybird's log was (`ctx.shared["log_start"]`) for the reload step.

## probe

The page probe (`js.PROBE`), cheap: no `getComputedStyle` over the whole page.

| Key | Meaning |
| --- | --- |
| `title`, `url`, `content_type` | `document.title` (80 chars), `location.href` (300), `document.contentType` |
| `elements`, `text`, `height`, `links`, `images` | element count, `innerText` length without visible `<option>` text (Chromium includes it, Ladybird doesn't), `scrollHeight`, link and image counts |
| `canvas`, `canvas_visible` | canvases, and those at least 100 x 80 in view |
| `resources`, `resources_total` | Resource Timing entries per initiator type, and in total |
| `resource_list`, `resource_types` | the first 500 entries (URL cut at 300) and their initiator types |
| `repeated` | `[url, count]` for addresses the top document fetched at least 20 times (Resource Timing is per document) |
| `error_reports` | requests to error reporting services (Sentry, Bugsnag, Datadog, Rollbar, New Relic `/jserrors/`) |
| `broken_images` | visible complete images with natural width 0 |
| `font_errors`, `font_error_names`, `font_errors_all`, `font_errors_unreplaced` | fonts with status `error`: count, first five, up to 40 as `family weight style`, and those without a loaded @font-face of the same family/weight/style |
| `fonts_loaded` | up to 60 loaded fonts as `family weight style` |
| `font_error_families`, `font_sources` | failing families, and the `src` of their @font-face rules (readable sheets only) |
| `video_stalled`, `video_total` | visible, not hidden videos with a source and `readyState` 0; all videos |
| `overflow`, `viewport_overflow_x` | `scrollWidth - clientWidth` of the root; the viewport's overflow-x (html's, or body's when html's is visible) |
| `error_texts` | error phrases a page shows its users (English and Swedish), up to five |
| `empty_root` | a framework root (`#root`, `#__next`, `#app`, `#___gatsby`, `[ng-version]`) with almost no markup |
| `iframes` | up to 10 iframes in view: `box` [x, y, w, h], `src` (host + path), `same_origin`, `elements`, `text` |
| `canvases`, `videos` | boxes of canvases and videos in view |
| `css`, `css_known`, `css_cross_origin` | top-level rule count per stylesheet (`host/path` or `inline:<length>:<hash>`); per sheet, rules dropped by Ladybird for known reasons (`view-transitions`, `#14121`, `-webkit-any`, `-webkit-scrollbar`); unreadable sheets |
| `large_fixed` | fixed/sticky elements covering at least 15 % of the viewport: `box`, `share` (%), `cookie` (looks like a cookie banner), `text` |
| `zero_size`, `zero_size_examples` | text elements (at least 20 characters) with a 0 width or height |
| `off_page`, `off_page_examples` | text elements entirely left of or above the page |
| `net_error_page` | an `ERR_...` code on an almost empty page (a browser error page) |
| `scroll_lock` | html/body overflow-y hidden or clip and the page no taller than the viewport |
| `objects_without_data` | `<object>` without `data` but with fallback content |
| `dom_resources` | script and stylesheet URLs as elements in the final DOM (up to 400) |
| `top_script_hosts`, `frame_hosts` | hosts the top document fetched scripts from; hosts of cross-origin iframes |
| `link_domains`, `internal_links`, `template` | other domains linked; candidate subpages (visible same-host links, no files, login, checkout or search); the WordPress or Shopify theme |
| `user_agent`, `dpr`, `viewport` | `navigator.userAgent`, `devicePixelRatio`, `[innerWidth, innerHeight]` |
| `layout_seconds`, `layout_error` | time for the layout map, or its error |
| `fingerprint` | Ladybird only: the 32 x 20 grey-level fingerprint of `load.png` (the regression watch) |
| `blocked` | a bot wall: `BOT_WALL` in the title or the text of an almost empty page, or `BOT_WALL_STRONG` in the first 3,000 characters; for references also a failed load without elements |
| `error_page` | a browser error page, an empty untitled page, a "404"/"not found" title, or a failed load or probe |
| `error` | the probe's own error (then most keys are missing) |

Files: `<browser>-load.png`, `<browser>-layout.json.gz`, `<browser>-dom.html.gz`.

The **layout map** (`js.LAYOUT`) is a JSON list of up to 4,000 rows in document order: `[key, parent index, x, y, w, h, display, position, float, own text length, font size, "children:text prefix", hidden by content-visibility, content-visibility auto]`. The key is a path of `tag`, `tag#id` or `tag.class` (stable ones only) with an ordinal among equal siblings, so it is the same in both browsers. It doesn't descend into svg, iframes, replaced elements, `display: none` or map tile layers (Google Maps, Leaflet).

## idle

| Key | Meaning |
| --- | --- |
| `cpu_idle` | CPU % of the page's own renderer process over three idle seconds, from `/proc` (Ladybird's `WebContent`, Chromium's `--type=renderer`, Firefox's content processes); with several, the one that gets the CPU during a 300 ms busy loop in the page, beyond its own rate |
| `cpu_idle_all` | CPU % summed over all renderer processes |
| `memory_mb` | RSS of the renderer processes |
| `nav` | `{dcl, load}` from the navigation timing entry (ms) |
| `response_ms` | the slowest of three trivial script round trips |

Files: `<browser>-idle.png`.

## interact (merged into the browser's measurements)

| Key | Meaning |
| --- | --- |
| `css` | Ladybird only: the CSS rule comparison. `rules` [ladybird, reference] rule counts, `differing` rules whose checksum differs (at most 300), `dropped`: up to 10 groups `{property, kind, count, examples: [{rule, reference, ladybird}], supported}` - declarations the reference has and Ladybird lacks (or whose function Ladybird lacks), after asking Ladybird directly for shorthands and unprefixed aliases; `supported` is `CSS.supports(property, 'initial')` in Ladybird. Or `error`. |
| `scroll` | scrolling down a screen at a time (at most 12) and back: `height`, `elements`, `resources` (without link prefetches) as [before, after], `bottom` (scrollY at the end) |
| `wheel` | one wheel step (deltaY 400) in the middle of the window: `scrollable` (page at least 200 px taller than the window), `scrolled` (page plus inner scrollers), `viewport_overflow_y` |
| `mobile` | the viewport at 390 x 844 without reloading: `viewport`, `overflow`, `viewport_overflow_x`, `above` ([right edge of boxes entirely above the page, right edge of the others] when overflowing) |
| `menu` | the first visible header menu button (`aria-expanded=false`): `label`, `controls`, `method` (`input` when the button is hit at its center, else `js`), `expanded`, `controlled_visible`, `gone`, `url`; null when there is none |

Files: `<browser>-layout-mobile.json.gz`, `<browser>-mobile.png`.

## reload

| Key | Meaning |
| --- | --- |
| `seconds`, `waited`, `stable`, `error` | the (first) reload, as in `load` |
| `memory` | Ladybird: WebContent RSS after `idle` and after each reload; up to four reloads while memory grows at least 100 MB per reload |
| `elements`, `text` | Ladybird: counts after the first reload |
| `known_tests` | `{page test id: result}` for the known-bug page tests this browser ran (see `webparity.known`); a dict result is a test that failed to run |
| `unresolvable_hosts` | Ladybird: hosts of "Unable to resolve host" failures that don't resolve from the worker either |

Files: `<browser>-reload.png`, `<browser>-layout-reload.json.gz`. The reference reloads at the same time: what differs between its own two loads is noise for the image and layout comparisons.

After the reloads, the leader (Ladybird) writes the log slice so far into `ladybird.log`, runs the detectors once, and asks `Known.page_tests_for` which page tests can explain the error findings; each browser then runs the tests assigned to it (in a 390 x 844 viewport for `mobile` tests).

## Visit-level data used by the detectors

Written by `webparity.visit`: `ladybird.log` (Ladybird's log for the visit), `log_reload_offset` (byte offset where the reload started), `crash-report-N.txt`, `alive` (per browser), `events.jsonl` (console entries with `top` true/false, navigation events, each tagged with the step).
