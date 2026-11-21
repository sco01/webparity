<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Reducing a finding

A finding from a visit is a whole website: megabytes of HTML, CSS and scripts. Nobody can debug that, and nobody may
publish it. `webparity reduce` shrinks the page, step by step, to a small test case that still shows the finding.

## What counts as "still shows the finding"

The finding is written as a *predicate*: a script that the probe runs in each browser (see `webparity probe`), and a
condition on the results. Without a script the probe reads the test case's `#out` element, or the title.

| Option | The candidate is interesting when |
| --- | --- |
| `--differs` | Ladybird and Chromium both answer, with different values |
| `--ladybird-equals VALUE` | Ladybird's value is `VALUE` (whitespace at the ends ignored) |
| `--ladybird-matches REGEX` | Ladybird's value matches `REGEX` (`re.search`) |
| `--crash` | Ladybird's probe fails with a lost connection or a crash, and every other browser probed is fine |
| `--ladybird-error REGEX` | Ladybird's probe error matches `REGEX`, and every other browser probed is fine |

Write the script so that it returns a short value that captures the bug and nothing else: `--differs` with
`document.body.scrollHeight` is satisfied by almost any change to the page, so the reduction drifts to a different
difference. Prefer a value that names the broken thing, for example the width of the element that is laid out wrong:
`-j "document.querySelector('.price').getBoundingClientRect().width"`. If the selector is gone the script returns
null in both browsers, and the candidate is (correctly) not interesting.

Flaky findings: `--repeat N` probes each candidate N times and requires all runs to hold, which keeps random hits out
of the result; `--repeat N --repeat-any` accepts a candidate when one run holds, for crashes that only happen
sometimes.

## Sources

- **A local HTML file.** Candidates are written next to it (as `_webparity-candidate-*.html`, deleted after each
  probe), so relative references to its CSS, scripts and images keep working.
- **A visit bundle** (its directory or its `visit.json`). The saved DOM (`ladybird-dom.html.gz`, or
  `chromium-dom.html.gz` with `--dom chromium`) is the starting point. The resources in `recording.warc.gz` are written
  to `OUT.work/site/res/<host>/<path>`, and the page's references to them (`src`, `href`, `srcset`, `poster`,
  `url()` in CSS and `style=""`) are rewritten to the local copies, so every candidate runs offline. URLs that a
  script builds at run time are not rewritten; they fail to load, which the predicate sees like any other change.

A saved DOM is the page *after* its scripts ran. Running those scripts again on it is not the same as the original
visit; often the scripts pass simply removes them all. When the finding needs the original load sequence, record the
visit again and reduce the page's original HTML instead (save it from the recording as a local file).

## Example

```
webparity reduce data/visits/2026-11-21/20261121-101500-ab12cd/ -o work/price-width.html \
    -j "document.querySelector('.price').getBoundingClientRect().width" --differs
```

prints the passes as they run and ends with something like

```
note: 213 recorded resources written to work/price-width.html.work/site/res
1843211 -> 412 bytes in 611 probes (88 cached, 1302 s): work/price-width.html
```

A test case that already exists, reduced against a known Ladybird value:

```
webparity reduce tests/cases/flex-gap.html -o /tmp/flex-gap.min.html --ladybird-equals "0 120" \
    -j "[...document.querySelectorAll('.item')].map(e => e.offsetLeft).join(' ')"
```

A crash, with Chromium left out to halve the time per candidate:

```
webparity reduce page.html -o crash.min.html --crash --browsers ladybird --repeat 2
```

## How it reduces

The passes run in this order, and the whole sequence is repeated (at most `--rounds`, default 3) while it still makes
the page smaller:

1. **scripts**: remove whole `<script>` elements; inline local classic external scripts; then remove top-level
   statements (the script is cut after `;` and line breaks outside brackets, strings, comments and templates).
2. **stylesheets**: inline local `<link rel=stylesheet>` files into `<style>`, with their `url()` references rebased.
3. **dom**: remove nodes level by level: one delta debugging run over all children of `<html>`/`<head>`/`<body>`,
   then over all their children, and so on; then unwrap elements (replace them by their children).
4. **css-rules**: remove rules in `<style>`: top-level rules first, then the rules inside `@media`, `@supports`,
   `@layer` and the other group rules.
5. **declarations**: remove declarations from `<style>` rules and `style=""` attributes.
6. **attributes**: remove attributes.
7. **text**: remove whitespace between elements, then shorten text nodes (halves of the words, then of the
   characters).

Each step uses delta debugging (`ddmin`): try removing everything, then halves, then quarters, and so on, keeping every
removal after which the predicate still holds. Every candidate is written to a fresh file and probed; answers are
cached by content, so the same document is never probed twice. `--passes dom,attributes` runs only some passes.

The browsers keep their sessions between candidates (a session that fails, for example after a crash, is replaced);
`--fresh` starts new sessions for every candidate, which is slower but rules out state carried between pages.

## Interrupting, budgets and the work directory

`--budget N` (default 1000) caps the number of probes. The work directory (default `OUT.work`, or `--work-dir`)
holds `best.html` (for a bundle in `OUT.work/site/`, next to the resources), rewritten after every improvement, so a
run that is stopped with Ctrl-C or runs out of budget keeps its progress; the output file is written in both cases
too. To continue, start a new run from `best.html`: it is a local file, and its relative references still work.

If the result still refers to local files, `reduce` lists them. A finished test case should not: inline what is
needed, or replace images by blocks of the same size, before filing it.

## What may go into the repository

Site content is copyrighted. **Only hand-written test cases, or test cases reduced so far that no third-party content
is left, go into the public repository.** That rules out saved pages, recordings, site style sheets and scripts, and
also reduced test cases that still contain the site's text, images, fonts, class names that identify it, or chunks of
its code. Reduce until what is left is generic (a few elements, a few declarations, a few statements), then rewrite it
by hand: neutral text such as `Lorem` or `x`, neutral class names, colored boxes instead of images. Keep visit
bundles, work directories and unfinished reductions in the data directory, which is never published.
