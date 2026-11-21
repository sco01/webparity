<!-- SPDX-License-Identifier: BSD-2-Clause -->
# How the agent works

webparity was built, and is run, together with an AI agent (Claude, by Anthropic) that has shell access to the host
where webparity runs. This page says what the agent does and the rules it follows, so that anyone reading our issues
knows how they were made.

## What the agent does

- Triages new candidates: reads the visit bundles, replays recordings, and decides whether a finding is a Ladybird
  bug, a site bug, a known difference or noise.
- Reduces real bugs to small, hand-checked test cases, and writes issue drafts with what was measured in Ladybird,
  Chromium and Firefox.
- Improves the detectors: every false finding becomes a negative fixture before the detector is fixed, and the change
  is checked with `webparity reanalyze` against saved bundles.
- Writes and maintains webparity's own code and documentation.

## What the agent does not do

- It never files issues, comments or pull requests on Ladybird's repository. A person reads every draft, checks the
  test case and files it. The Ladybird team doesn't want automated issues or pull requests.
- It never changes the engines it tests.
- It doesn't log in to sites, fill in forms, bypass bot protection or fake a user agent.

## Rules on the host

- No package installs, system setting changes or persistent configuration without the operator's approval.
- Processes are stopped by explicit process id, never by pattern.
- The nightly build and the regression watch get the CPU: no heavy work during their window.
- No personal data in calls to outside services; GitHub is read anonymously.
