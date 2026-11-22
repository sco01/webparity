<!-- SPDX-License-Identifier: BSD-2-Clause -->
# The issue workflow

webparity finds differences between Ladybird and the reference browsers and keeps one candidate per signature. Turning a
candidate into an issue is work for a person: investigate it, reduce it to a small test case, write the issue, file it,
and follow it until it is fixed. webparity helps with the bookkeeping around that work. **It never files issues, posts
comments or opens pull requests, and it never authenticates to GitHub.**

```
candidate ──draft──▶ draft ──review──▶ a person files it on GitHub ──register──▶ filed
                                                                                    │
                    verified fixed ◀──verify── fixed? (sites requeued) ◀──watch──── closed
```

## 1. Draft

```
webparity draft SIGNATURE [--title "..."]
```

writes `data_dir/drafts/<slug>.md` and records the draft in the `issues` table (state `draft`); the candidate moves from
`new` to `investigating`. The draft starts with a front matter block:

```
---
signatures: ["js-error:TypeError: ..."]
test_case: test-cases/<slug>/
kind: issue
---
```

followed by the fields of Ladybird's bug report form in order (Title, Summary, Steps to reproduce, Expected behavior,
Actual behavior, the reduced test case, …), so it can be pasted field by field, and a last section **Notes (not for the
issue)** with what the database knows: the finding's text, the number of sites, example sites and the latest build.
The notes stay local; nothing under that heading belongs in the issue.

`test_case` is a single file, or a folder whose page is `index.html` (other files beside it, such as a second page for a
cross-origin test, are served from the same folder; `http://localhost:PORT/` reaches the same server on another origin).
A test case should write its result as text into `<pre id="out">`: that is what verification reads later.
"Expected behavior" holds Chromium's (and Firefox's) output in a code block, "Actual behavior" Ladybird's.

## 2. Review

```
webparity draft --check drafts/<slug>.md
```

lists what still keeps a draft from being ready: a missing or nonexistent test case, external URLs in the test case
(it must work offline), a title of 100 characters or more, `TODO` placeholders, missing steps to reproduce, text that
is not in English (å/ä/ö), and personal details such as email addresses, local paths and IP addresses. An empty list
does not mean the draft is good; it means a person can now read it critically. Run the test case yourself in Ladybird,
Chromium and Firefox, and search the tracker for an existing issue before filing.

The web interface's review page (`/review`) shows the drafts waiting to be filed, in queue order, one at a time:

- every field of Ladybird's bug report form, in the form's order, editable; edits are saved in the database
  (`issues.fields`), never in the draft file, and "Reset" returns to the file's text;
- "Copy the GitHub link" / "Open the form on GitHub": the form (`bug_report.yml`) prefilled with the fields as query
  parameters. GitHub resets prefilled fields when you edit them, so make every change on the review page first, and
  open each draft in a new tab (GitHub remembers the form per tab). The Screenshots field is left out of the link: its
  field holds one caption line per image to paste above the dragged-in images. A test case too long for the link is
  left out with a notice, to paste from its field;
- the screenshots named in the draft (`name.png (caption)`), to copy or save and drag into the form;
- the draft's internal note, folded, for background;
- "Filed as #N" (a comment: "Mark as posted"), which records the number and can be undone until the draft is
  registered.

## 3. A person files it

The reviewer files the issue on GitHub from their own account, using the draft as a starting point and changing whatever
needs changing. webparity has no part in this step.

## 4. Register

```
webparity register drafts/<slug>.md 12345
webparity register --comment drafts/<comment>.md 12345
```

links the draft to the issue number: the issue row gets the number, state `filed` and the date, and the candidates of its
signatures get status `filed` and `issue = '#12345'`. A comment is recorded as kind `comment`, state `posted`.

## 5. Watch

```
webparity watch
```

reads every filed issue from the public GitHub REST API, anonymously and read-only, with the configured
`github.user_agent`. Responses are cached with their ETags in `data_dir/github-cache.json`, so an unchanged issue costs
a `304`; the watcher stops when the anonymous rate limit is used up and continues on the next run. Open issues are read
first, so a run cut short by the limit leaves only closed issues unread. It records the state
(`filed`, `closed`, `not-planned`, `duplicate`), the closing date and what closed the issue (`#PR` for a merged pull
request that references it, else `commit <sha>`).

When an issue closes as completed, its candidates become `fixed?` and the sites where the signature was seen are queued
again, so the next visits show whether the finding is gone. A reopened issue puts its candidates back to `filed`.

## 6. Verify

```
webparity verify [12345 ...]
```

runs each filed issue's test case in Ladybird and Chromium (and Firefox when it is configured), reads `#out` four seconds
after the page has loaded, and compares the answers with the draft's expected and actual output (the code block under
the heading, or the field's text without a lead-in line such as "As in Chromium and Firefox:"). Texts that differ only
in numbers count as equal when every number is within 2 units or 2 %. Chromium's answer only counts as a reference when it
is neither `#out`'s placeholder text (the test case did not finish) nor the reported bug itself (the test environment
lacked something). Test cases run ten at a time, so one that hangs only costs its batch. In the nightly chain, verify runs
after watch, so an issue closed since the last night is judged as closed. A test case imported from a draft's source
gets the files the source refers to (an image, say) from the `testfall/...` folder the draft mentions.

| Result | Meaning |
|---|---|
| `fixed` | closed, the build is newer than the closing, and Ladybird gives the expected output |
| `fixed-silently` | still open, but Ladybird gives the expected output: worth a comment by a person |
| `not-fixed` | closed and built, but Ladybird still gives the reported bug |
| `changed` | closed and built, Ladybird gives some third output: look at it |
| `fixed-build-older` / `waiting-for-build` | closed after the build under test |
| `still-broken` | open, and Ladybird still gives the reported bug |
| `manual` | no `#out`, interactive steps (mouse, wheel, keys), or a server script: check by hand |
| `error` | no usable answer from Ladybird |

A `fixed` result moves the candidates from `fixed?` to `fixed`; `not-fixed` moves them back to `filed`. Issues already
verified as fixed are skipped unless they are named.

`webparity index` writes `data_dir/INDEX.md`, the table of issues with their state, signatures, dates and verification,
from the database alone. `webparity import-gallery DIR` imports issues filed before webparity from a gallery directory
(`issues/INDEX.md` and drafts) into the local database.

## Writing good Ladybird issues

Read [How to write good Ladybird issues](https://gist.github.com/sco01/6ec1ca5e5edbef8622fdbbe7165dd410) before filing.

The Ladybird team does not want automated pull requests or automated issues. Everything webparity produces is material
for a person: a person reviews every draft, reproduces the bug, files the issue under their own name and answers the
questions that follow. Never script the filing, and never let a tool post comments.
