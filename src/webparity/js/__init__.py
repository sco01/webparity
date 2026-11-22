# SPDX-License-Identifier: BSD-2-Clause
# ruff: noqa: E501 - the scripts are JavaScript with long lines, kept as in the browser
"""The in-page scripts of a visit, as Python strings.

Every script is a statement body with its own ``return`` (``Handle.js``); scripts that take arguments read them from
``arguments``. The same script runs in every browser, so the measurements are symmetric. Ported from the old
explorer's probe (SOND), layout map (LAYOUT), CSS rule comparison (CSS_STEG1/2/3) and interaction scripts; the dated
comments say which false finding each detail fixed.
"""

# Waiting for a stable page: elements, resources, text length, readyState
STABLE = """return [document.querySelectorAll('*').length, performance.getEntriesByType('resource').length,
        document.body ? document.body.innerText.length : 0, document.readyState]"""

# The page probe: the same script in every browser. Cheap: no getComputedStyle over the whole page.
PROBE = r"""
const cs = Array.from(document.querySelectorAll('canvas'));
const txt = document.body ? document.body.innerText : '';
// Text in <option> is not counted (2026-11-08): Chromium includes it in innerText, Ladybird does not (option has
// display: none) - it gave less-content on school portals. Only options the browser itself considers visible are subtracted.
const optText = Array.from(document.querySelectorAll('select option')).slice(0, 5000).reduce((s, o) => s + (getComputedStyle(o).display !== 'none' ? o.textContent.trim().length + 1 : 0), 0);
const R = performance.getEntriesByType('resource');
const per = {};
for (const x of R) per[x.initiatorType || 'other'] = (per[x.initiatorType || 'other'] || 0) + 1;
// New Relic: only /jserrors/ are error reports - bam.nr-data.net/rum, /connect and /events are ordinary metrics (2026-11-08)
const ERROR_REPORT = /sentry\.io|ingest\.sentry|\/api\/\d+\/envelope|bugsnag|browser-intake-[a-z0-9-]*datadoghq|rollbar\.com\/api|\/jserrors\//;
// Error texts a page shows to its users (English and Swedish variants)
const ERROR_TEXT = /something went wrong|an error (has )?occurred|ett fel (har )?uppstod|ett fel har uppstått|något gick fel|oops[!,.]|unsupported browser|browser (is )?not supported|update your browser|upgrade your browser|uppdatera din webbläsare|din webbläsare stöds inte|please enable javascript|aktivera javascript|application error: a client-side exception|minified react error/gi;
// Cookie banners (English and Swedish words)
const COOKIE = /cookie|kakor|samtycke|consent|gdpr|personuppgifter/i;
const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
const box = r => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
const inView = r => r.width >= 100 && r.height >= 80 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
const roots = ['#root', '#__next', '#app', '#___gatsby', '[ng-version]'].map(q => document.querySelector(q)).filter(Boolean);
const de = document.documentElement;
const iframes = [];
for (const f of document.querySelectorAll('iframe')) {
  const r = f.getBoundingClientRect(); if (!inView(r)) continue;
  let same = false, el = null, tx = null;
  try { const d = f.contentDocument; if (d) { same = true; el = d.querySelectorAll('*').length; tx = d.body ? d.body.innerText.length : 0; } } catch (e) {}
  let src = ''; try { const u = new URL(f.src, location.href); src = (u.host + u.pathname).slice(0, 80); } catch (e) {}
  iframes.push({box: box(r), src, same_origin: same, elements: el, text: tx});
  if (iframes.length >= 10) break;
}
const canvases = cs.map(c => c.getBoundingClientRect()).filter(inView).slice(0, 10).map(box);
const videos = Array.from(document.querySelectorAll('video')).map(v => v.getBoundingClientRect()).filter(inView).slice(0, 5).map(box);
const hash = s => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; return h.toString(36); };
const css = {}, cssKnown = {}; let cssCross = 0;
for (const s of Array.from(document.styleSheets)) {
  let n = null; try { n = s.cssRules.length; } catch (e) { cssCross++; continue; }
  let k; if (s.href) { try { const u = new URL(s.href); k = u.host + u.pathname; } catch (e) { k = s.href.slice(0, 120); } }
  else { const t = s.ownerNode ? s.ownerNode.textContent || '' : ''; k = 'inline:' + t.length + ':' + hash(t); }
  css[k] = (css[k] || 0) + n;
  // Rules Ladybird drops for known reasons (2026-11-09), counted at the top level like cssRules.length: View Transitions
  // pseudo-classes (only apply during a transition, which cannot start in Ladybird), @starting-style (#14121), :-webkit-any()
  // and ::-webkit-scrollbar (Chromium only; Firefox drops :-webkit-any too)
  for (const r of s.cssRules) { const t = r.cssText || '';
    const o = /:active-view-transition/.test(t) ? 'view-transitions' : /^@starting-style/.test(t) ? '#14121' : /:-webkit-any\(/.test(t) ? '-webkit-any' : /::-webkit-scrollbar/.test(t) ? '-webkit-scrollbar' : null;
    if (o) { cssKnown[k] = cssKnown[k] || {}; cssKnown[k][o] = (cssKnown[k][o] || 0) + 1; } }
}
const fixed = new Map();
for (let gx = 1; gx < 6; gx++) for (let gy = 1; gy < 6; gy++) {
  for (const e of document.elementsFromPoint(innerWidth * gx / 6, innerHeight * gy / 6)) {
    if (fixed.has(e) || e === de || e === document.body) continue;
    const p = getComputedStyle(e).position;
    if (p === 'fixed' || p === 'sticky') { fixed.set(e, 1); }
  }
}
const large = [];
for (const e of fixed.keys()) {
  const r = e.getBoundingClientRect(); const area = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
  if (area < 0.15 * innerWidth * innerHeight) continue;
  const t = (e.innerText || '').slice(0, 600);
  large.push({box: box(r), share: Math.round(100 * area / (innerWidth * innerHeight)), cookie: COOKIE.test(t) || COOKIE.test(e.id + ' ' + e.className), text: t.slice(0, 80)});
  if (large.length >= 6) break;
}
let zero = 0, off = 0, textNodes = 0; const zeroEx = [], offEx = [];
const tw = document.createTreeWalker(document.body || de, NodeFilter.SHOW_TEXT);
const seen = new Set();
for (let n = tw.nextNode(); n && textNodes < 5000; n = tw.nextNode()) {
  const t = n.nodeValue.trim(); if (t.length < 20) continue;
  const e = n.parentElement; if (!e || seen.has(e)) continue; seen.add(e); textNodes++;
  if (e.closest('script,style,noscript,template,svg,select,option')) continue;
  const rs = e.getClientRects(); if (!rs.length) continue;
  const r = e.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) { zero++; if (zeroEx.length < 5) zeroEx.push(t.slice(0, 40)); }
  else if (r.right + scrollX < -5 || r.bottom + scrollY < -5) { off++; if (offEx.length < 5) offEx.push(t.slice(0, 40)); }
}
const errFam = document.fonts ? Array.from(document.fonts).filter(f => f.status === 'error').map(f => f.family.replace(/["']/g, '').trim()) : [];
const fontSrc = {};
for (const s of Array.from(document.styleSheets)) { let rr; try { rr = s.cssRules; } catch (e) { continue; }
  for (const r of rr) if (r.type === 5) { const fam = (r.style.getPropertyValue('font-family') || '').replace(/["']/g, '').trim();
    if (errFam.includes(fam) && !fontSrc[fam]) fontSrc[fam] = (r.style.getPropertyValue('src') || '').slice(0, 300); } }
// Internal links (2026-11-09): subpage candidates - visible links to the same host, not files, login, checkout or search
// (English and Swedish path words)
const internal = [], seenI = new Set();
for (const a of document.links) {
  let u; try { u = new URL(a.href); } catch (e) { continue; }
  if (u.hostname !== location.hostname || !/^https?:$/.test(u.protocol)) continue;
  const p = u.pathname; if (p === '/' || p === location.pathname) continue;
  if (/\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|pptx?|mp4|mp3|xml|json|ics|txt|rss)$/i.test(p)) continue;
  if (/log-?in|logga-?in|logout|sign-?in|konto|account|kassa|checkout|cart|varukorg|admin|search|\/sok\b|print|feed/i.test(p + u.search)) continue;
  const r = a.getBoundingClientRect(); if (r.width < 1 || r.height < 1) continue;
  const k = (u.origin + p).slice(0, 300); if (seenI.has(k)) continue; seenI.add(k); internal.push(k);
  if (internal.length >= 150) break;
}
// Template (2026-11-09): the WordPress or Shopify theme - sites with the same theme have the same page templates
const template = (() => { const h = document.documentElement.innerHTML.slice(0, 400000);
  const wp = h.match(/\/wp-content\/themes\/([\w-]{2,40})\//); if (wp) return 'wp:' + wp[1].toLowerCase();
  try { if (window.Shopify && Shopify.theme && Shopify.theme.name) return 'shopify:' + String(Shopify.theme.name).toLowerCase().replace(/[^a-z0-9-]+/g, '-').slice(0, 40); } catch (e) {}
  return null; })();
const linkDomains = new Set();
for (const a of document.links) { try { const h = new URL(a.href).hostname; if (h && h !== location.hostname) linkDomains.add(h); } catch (e) {} if (linkDomains.size >= 60) break; }
// The same address fetched again and again by the top document (#14392: a stylesheet importing itself). Resource Timing is
// per document, so 30 iframes each fetching a player stylesheet once do not count (2026-11-17); data: URLs are not entries.
const counts = {};
for (const x of R) counts[x.name] = (counts[x.name] || 0) + 1;
const repeated = Object.entries(counts).filter(([u, n]) => n >= 20).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([u, n]) => [u.slice(0, 2000), n]);
return {title: document.title.slice(0, 80), url: location.href.slice(0, 300), content_type: document.contentType, elements: document.querySelectorAll('*').length,
        text: Math.max(0, txt.length - optText), canvas: cs.length, canvas_visible: canvases.length,
        resources: per, resources_total: R.length, resource_list: R.slice(0, 500).map(x => x.name.slice(0, 300)), resource_types: R.slice(0, 500).map(x => x.initiatorType || ''),
        repeated,
        error_reports: R.filter(x => ERROR_REPORT.test(x.name)).length,
        broken_images: Array.from(document.images).filter(i => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src) && visible(i)).length,
        font_errors: document.fonts ? Array.from(document.fonts).filter(f => f.status === 'error').length : null,
        font_error_names: document.fonts ? Array.from(document.fonts).filter(f => f.status === 'error').slice(0, 5).map(f => `${f.family} ${f.weight} ${f.style}`) : [],
        // Errors without a replacement (2026-11-08): a failing font does not count when another @font-face with the same
        // family, weight and style loaded (two rules for an icon font, the first with a relative path that gives 404).
        // All failing and loaded fonts (2026-11-08): font errors count only when the reference loaded the same font - otherwise
        // it is the site's 404 that Ladybird reaches because a fontconfig alias is missing (Chromium finds the font locally).
        font_errors_all: document.fonts ? Array.from(document.fonts).filter(f => f.status === 'error').slice(0, 40).map(f => `${f.family.replace(/["']/g, '')} ${f.weight} ${f.style}`) : [],
        fonts_loaded: document.fonts ? Array.from(document.fonts).filter(f => f.status === 'loaded').slice(0, 60).map(f => `${f.family.replace(/["']/g, '')} ${f.weight} ${f.style}`) : [],
        font_errors_unreplaced: document.fonts ? (() => { const fs = Array.from(document.fonts), k = f => `${f.family}|${f.weight}|${f.style}`;
          const ok = new Set(fs.filter(f => f.status === 'loaded').map(k)); return fs.filter(f => f.status === 'error' && !ok.has(k(f))).length; })() : null,
        // visibility (2026-11-17): a video player with two video elements in the same place hides the one it does not use -
        // in Ladybird the MSE element stayed at readyState 0 while the mp4 element played
        video_stalled: Array.from(document.querySelectorAll('video')).filter(v => (v.currentSrc || v.src || v.querySelector('source')) && v.readyState === 0 && visible(v) && getComputedStyle(v).visibility !== 'hidden').length,
        video_total: document.querySelectorAll('video').length,
        overflow: Math.max(0, de.scrollWidth - de.clientWidth), height: de.scrollHeight,
        // The viewport's overflow-x (2026-11-10): html's, or body's when html has visible - hidden/clip cannot be scrolled by the user
        viewport_overflow_x: (() => { const h = getComputedStyle(document.documentElement), b = document.body && getComputedStyle(document.body); return h.overflowX !== 'visible' ? h.overflowX : (b ? b.overflowX : 'visible'); })(),
        error_texts: Array.from(new Set((txt.match(ERROR_TEXT) || []).map(t => t.toLowerCase()))).slice(0, 5),
        empty_root: roots.length > 0 && roots.every(e => e.innerHTML.trim().length < 50),
        links: document.links.length, images: document.images.length,
        iframes, canvases, videos, css, css_known: cssKnown, css_cross_origin: cssCross, large_fixed: large,
        zero_size: zero, zero_size_examples: zeroEx, off_page: off, off_page_examples: offEx,
        net_error_page: document.querySelectorAll('*').length < 120 && document.body ? ((document.body.innerText.match(/\bERR_[A-Z0-9_]{4,}\b/) || [])[0] || null) : null,
        scroll_lock: (() => { const ov = e => e ? getComputedStyle(e).overflowY : ''; return /hidden|clip/.test(ov(document.documentElement) + ' ' + ov(document.body)) && document.documentElement.scrollHeight <= innerHeight + 2; })(),
        // <object> without data but with content (2026-11-21: theme cards inside <object> for links in links) - Ladybird does
        // not show the fallback content (PR #15190); counted to see how common it is
        objects_without_data: Array.from(document.querySelectorAll('object')).filter(o => !(o.getAttribute('data') || '').trim() && o.children.length).length,
        // Scripts and stylesheets as elements in the final DOM (2026-11-21): missing-resources ignores what Chromium's preload
        // scanner fetched but the page then removed (consent scripts)
        dom_resources: Array.from(new Set(Array.from(document.querySelectorAll('script[src], link[href]')).map(e => e.src || e.href).filter(u => /^https?:/.test(u)))).slice(0, 400),
        // Hosts the top document itself fetched scripts from (2026-11-21: a weather widget in an iframe loads Bootstrap from a CDN
        // without jQuery - the exception has the CDN's host, not the iframe's, and was counted as the page's own)
        top_script_hosts: Array.from(new Set(performance.getEntriesByType('resource').filter(e => e.initiatorType === 'script').map(e => { try { return new URL(e.name).hostname; } catch (e) { return ''; } }).concat(Array.from(document.scripts).map(s => { try { return s.src ? new URL(s.src).hostname : ''; } catch (e) { return ''; } })).filter(Boolean))).slice(0, 200),
        frame_hosts: Array.from(new Set(Array.from(document.querySelectorAll('iframe')).filter(f => { try { return !f.contentDocument; } catch (e) { return true; } }).map(f => { try { return new URL(f.src, location.href).hostname; } catch (e) { return ''; } }).filter(Boolean))).slice(0, 20),
        font_sources: fontSrc, font_error_families: Array.from(new Set(errFam)).slice(0, 8), link_domains: Array.from(linkDomains), internal_links: internal, template,
        user_agent: navigator.userAgent, dpr: devicePixelRatio, viewport: [innerWidth, innerHeight]};
"""

# The layout map (2026-11-09, "compare elements instead of pixels"): position, size and computed display for up to 4,000
# elements in document order, with a key that is the same in every browser (a path of tag, stable id or first class, and
# the ordinal among siblings with the same tag and class). The layout detector pairs them up and points out the element
# where a difference starts. Does not descend into svg, iframes, script/style, elements with display: none, maps or
# carousels.
# Row: [key, parent index, x, y, w, h, display, position, float, own text length, font size, "children:text prefix",
#       hidden by content-visibility, content-visibility: auto, an input's type (2026-11-22)]
LAYOUT = r"""
const MAX = 4000, out = [], REPLACED = /^(IMG|SVG|VIDEO|IFRAME|INPUT|BUTTON|SELECT|TEXTAREA|CANVAS|OBJECT|EMBED|AUDIO|PROGRESS|METER)$/;
const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|LINK|META|HEAD|TITLE|BR|WBR)$/;
const CAROUSEL = ['flickity-viewport', 'slick-list', 'swiper-wrapper', 'splide__track', 'owl-stage-outer', 'glide__track', 'carousel-inner', 'slideshow'];
const stable = x => x && /^[A-Za-z][\w-]{0,40}$/.test(x) && !/\d{3,}|[0-9a-f]{6,}/i.test(x);
if (!document.body) return [];
const sx = scrollX, sy = scrollY;
const stack = [[document.body, '', -1]];
while (stack.length && out.length < MAX) {
  const [e, pk, pi] = stack.pop();
  const tag = e.tagName.toUpperCase();
  const i = out.length;
  const r = e.getBoundingClientRect(), c = getComputedStyle(e);
  let text = 0, fp = '';
  for (const n of e.childNodes) if (n.nodeType === 3) { const t = n.nodeValue.trim(); if (t) { text += t.length; if (fp.length < 12) fp += t.slice(0, 12 - fp.length); } }
  // 1 when the element is under content-visibility: hidden (a closed <details>, hidden=until-found, 2026-11-09) -
  // checkVisibility() is false for them, but not for display: none (those are skipped below anyway).
  // contentVisibilityAuto (2026-11-10): also content Chromium skips under content-visibility: auto off screen (sections got
  // placeholder height in Chromium but full height in Ladybird). cva: the element itself has auto.
  const hidden = c.display !== 'none' && typeof e.checkVisibility === 'function' && !e.checkVisibility({contentVisibilityAuto: true}) ? 1 : 0;
  const cva = c.contentVisibility === 'auto' ? 1 : 0;
  out.push([pk, pi, Math.round(r.left + sx), Math.round(r.top + sy), Math.round(r.width), Math.round(r.height), c.display, c.position, c.cssFloat,
           text, parseFloat(c.fontSize) || 16, e.childElementCount + ':' + fp.replace(/\s+/g, ' '), hidden, cva,
           tag === 'INPUT' ? (e.type || '') : '']);
  // Maps (2026-11-18): Google Maps and Leaflet put the tiles into the DOM in the order they load, so "tile 7" is a different
  // tile in each browser (positions differed by multiples of 256 px - 2/3 of layout:position:block>block). The map is
  // compared, not the tiles in it.
  // Carousels (2026-11-22): sliders that advance on a timer show a different slide in each browser (Flickity on
  // umarellrestaurant.se swapped between two visits) - the carousel's viewport is compared, not the slides in it.
  if (c.display === 'none' || tag === 'SVG' || tag === 'IFRAME' || REPLACED.test(tag)
      || (e.classList && (e.classList.contains('gm-style') || e.classList.contains('leaflet-map-pane')
          || CAROUSEL.some(k => e.classList.contains(k))))) continue;
  const counter = {}, children = [];
  for (const b of e.children) {
    const t = b.tagName.toUpperCase(); if (SKIP.test(t)) continue;
    const id = b.getAttribute('id'), cl = (b.getAttribute('class') || '').trim().split(/\s+/)[0];
    const sig = t.toLowerCase() + (stable(id) ? '#' + id : stable(cl) ? '.' + cl : '');
    counter[sig] = (counter[sig] || 0) + 1;
    children.push([b, pk + '/' + sig + ':' + counter[sig], i]);
  }
  for (let k = children.length - 1; k >= 0; k--) stack.push(children[k]);
}
out[0][0] = 'body';
return out;
"""

# Dropped CSS declarations (2026-11-09): step 1 gives a checksum per style rule (the properties and the function names in
# the values, except those that only serialize differently: rgb, calc, var ...); step 2 fetches properties and values for
# the rules that differ; step 3 asks Ladybird directly for properties missing from its list (shorthands, aliases).
CSS_COMMON = r"""
const NOISE = /^(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|calc|min|max|clamp|var|url|local|format|attr|env)$/i;
const hash = s => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; return h.toString(36); };
const rules = [];
const walk = (rr, k, d) => { if (d > 6) return; for (const r of rr) { if (rules.length >= 20000) return;
  if (r.selectorText && r.style) rules.push([k + '|' + r.selectorText, r]);      // not @page (empty selectorText, print only)
  if (r.cssRules && r.cssRules.length) walk(r.cssRules, k + (r.conditionText ? '@' + r.conditionText : r.name ? '@' + r.name : '@'), d + 1); } };
for (const s of Array.from(document.styleSheets)) { let rr; try { rr = s.cssRules; } catch (e) { continue; }
  let k; if (s.href) { try { const u = new URL(s.href); k = u.host + u.pathname; } catch (e) { k = s.href.slice(0, 120); } }
  else { const t = s.ownerNode ? s.ownerNode.textContent || '' : ''; k = 'inline:' + t.length + ':' + hash(t); }
  walk(rr, k, 0); }
const seen = {};
const key = sel => { seen[sel] = (seen[sel] || 0) + 1; return hash(sel) + '.' + seen[sel]; };
const funcs = v => (v.match(/[-\w]+(?=\()/g) || []).filter(f => !NOISE.test(f)).join(',');
"""
CSS_STEP1 = (
    CSS_COMMON
    + r"""
const out = {};
for (const [sel, r] of rules) { const p = Array.from(r.style).sort(); out[key(sel)] = hash(p.map(x => x + ':' + funcs(r.style.getPropertyValue(x))).join(';')); }
return out;
"""
)
CSS_STEP2 = (
    CSS_COMMON
    + r"""
const want = new Set(arguments[0]), out = {};
for (const [sel, r] of rules) { const k = key(sel); if (!want.has(k)) continue;
  const p = {}; for (const x of Array.from(r.style)) p[x] = r.style.getPropertyValue(x).slice(0, 120);
  out[k] = {sel: sel.slice(-100), p}; }
return out;
"""
)
CSS_STEP3 = (
    CSS_COMMON
    + r"""
const want = {}; for (const [k, p] of arguments[0]) (want[k] = want[k] || []).push(p);
const out = {};
for (const [sel, r] of rules) { const k = key(sel); if (!want[k]) continue; out[k] = {}; for (const p of want[k]) out[k][p] = r.style.getPropertyValue(p); }
return out;
"""
)
CSS_SUPPORTS = "return arguments[0].map(p => CSS.supports(p, 'initial'))"

# Scrolling: one screen down, and the state [scrollY, scrollHeight, elements, resources]
SCROLL_STATE = (
    "return [Math.round(scrollY), document.documentElement.scrollHeight, document.querySelectorAll('*').length, "
    "performance.getEntriesByType('resource').filter(e => e.initiatorType !== 'link').length]"
)
SCROLL_STEP = (
    "window.scrollBy(0, Math.round(innerHeight * 0.9));\n"
    "return [Math.round(scrollY), document.documentElement.scrollHeight, document.querySelectorAll('*').length, "
    "performance.getEntriesByType('resource').length]"
)
# Instant, not smooth (2026-11-21: on sites with scroll-behavior: smooth the reset was still animating when the wheel test
# measured - several sites gave scroll-locked with 0 or negative scrolled distance)
SCROLL_TOP = "window.scrollTo({top: 0, left: 0, behavior: 'instant'}); return 1"
# The wheel test: how far the page and inner elements are scrolled, and the viewport
WHEEL_READ = (
    "let t = Math.round(scrollY); for (const e of document.querySelectorAll('body *')) if (e.scrollTop > 0) t += e.scrollTop; "
    "return [t, document.documentElement.scrollHeight, innerHeight, innerWidth];"
)
VIEWPORT_OVERFLOW_X = (
    "(() => { const h = getComputedStyle(document.documentElement), b = document.body && getComputedStyle(document.body); "
    "return h.overflowX !== 'visible' ? h.overflowX : (b ? b.overflowX : 'visible'); })()"
)
VIEWPORT_OVERFLOW_Y = VIEWPORT_OVERFLOW_X.replace("overflowX", "overflowY")
# Boxes entirely above the page (2026-11-10: a mobile menu at top: -750px): Chromium does not count their width in
# scrollWidth, Ladybird and Firefox do - not Ladybird's bug. [right edge of boxes entirely above, right edge of the others]
# among elements that go past the right edge.
ABOVE = (
    "(() => { const W = document.documentElement.clientWidth; let a = 0, o = 0; for (const e of document.querySelectorAll('body *')) "
    "{ const r = e.getBoundingClientRect(); if (!r.width || r.right <= W + 2) continue; if (r.bottom + scrollY <= 0) a = Math.max(a, r.right); "
    "else o = Math.max(o, r.right); } return [Math.round(a), Math.round(o)]; })()"
)
OVERFLOW = "return Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"

# The menu test: the first visible menu button (aria-expanded=false) in the header. No links, no forms.
MENU_FIND = r"""
const C = Array.from(document.querySelectorAll('header button[aria-expanded="false"], nav button[aria-expanded="false"], [role=banner] button[aria-expanded="false"], button[aria-expanded="false"][aria-controls]'));
const b = C.find(e => { const r = e.getBoundingClientRect(); return r.width > 8 && r.height > 8 && r.top >= 0 && r.bottom <= innerHeight && !e.closest('form') && !e.disabled && (e.getAttribute('type') || '').toLowerCase() !== 'submit'; });
if (!b) return null;
b.setAttribute('data-webparity-menu', '1');
const r = b.getBoundingClientRect();
return {label: ((b.getAttribute('aria-label') || b.innerText || '').trim().replace(/\s+/g, ' ')).slice(0, 40), controls: b.getAttribute('aria-controls'),
        center: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]};
"""
# Whether the element at the button's center is the button (a click there reaches it), as WebDriver's click would check
MENU_HIT = r"""
const b = document.querySelector('[data-webparity-menu]'); if (!b) return false;
const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
return !!e && (e === b || b.contains(e));
"""
MENU_JS_CLICK = "const b = document.querySelector('[data-webparity-menu]'); if (b) b.click(); return 1"
MENU_READ = r"""
const b = document.querySelector('[data-webparity-menu]');
if (!b) return {gone: true, url: location.href.slice(0, 300)};
const id = b.getAttribute('aria-controls'); const m = id ? document.getElementById(id) : null;
const r = m ? m.getBoundingClientRect() : null;
return {expanded: b.getAttribute('aria-expanded') === 'true', controlled_visible: r ? r.width > 0 && r.height > 0 : null, url: location.href.slice(0, 300)};
"""

NAV_TIMING = (
    "const n = performance.getEntriesByType('navigation')[0]; return n ? "
    "{dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd)} : null"
)
BODY_TEXT = "return (document.body && document.body.innerText || '').slice(0, 3000)"
COUNTS = "return [document.querySelectorAll('*').length, document.body ? document.body.innerText.length : 0]"
OUTER_HTML = "return document.documentElement.outerHTML"
# A short busy loop in the page: the renderer process that gets the CPU is the page's own (see measure.cpu)
BUSY = "const t = performance.now(); while (performance.now() - t < 300) {} return 1"
