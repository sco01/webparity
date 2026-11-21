<!-- SPDX-License-Identifier: BSD-2-Clause -->
# Monitoring

webparity has a small web interface, a Prometheus endpoint and alert rules. All three read the same SQLite
database (`data_dir/webparity.db`) and use only the Python standard library.

## The web interface

```
webparity web          # calls webparity.web.serve(config)
```

It listens on `web.listen` (default `127.0.0.1:8700`). Keep it on localhost and put a reverse proxy with
authentication in front of it if it must be reachable from elsewhere: the pages show URLs and console output from
the sites visited.

The interface is read-only: it opens the database with `mode=ro` per request, answers `POST` and other methods with
`405`, and has no forms that change state (the filter forms use `GET`). Pages are server-rendered HTML without
JavaScript, follow the light or dark system setting and work on a phone.

| Path | What |
|---|---|
| `/` | Health: visits in the last hour and per hour for 24 hours, active workers (visits in the last 15 minutes by worker), queue length, the last nightly run, visits by status and visits with errors, open alerts, the current Ladybird build |
| `/findings` | The latest error findings that are not known, filterable by signature family (`?family=crash`) and detector (`?detector=js`) |
| `/candidates` | Candidates by number of sites, with status, issue link, first and last seen; `?status=new` filters |
| `/candidates/<signature>` | One candidate (URL-encoded signature): its sites, latest visits, and the newest visit's screenshots side by side |
| `/visit/<id>` | One visit: URL, timings, findings, screenshots per browser, console errors from `events.jsonl`, files |
| `/visit/<id>/file/<name>` | A file from the visit bundle |
| `/issues` | Drafts, filed, closed and verified issues |
| `/review` | Drafts waiting for review, with the draft markdown when it lives under `data_dir/drafts` |
| `/metrics` | Prometheus text format |
| `/healthz` | JSON `{"ok": true, "visits_last_hour": 123, "db": "ok"}`; status 503 when the database cannot be read |

### Security

Everything taken from sites (titles, URLs, console text, finding texts, signatures) is HTML-escaped. Every response
carries `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'`,
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.

Bundle files are served only by plain file name (letters, digits, `.`, `_`, `-`; no slashes, no `..`), only when
the resolved path is directly inside that visit's bundle directory, and only for screenshots (`.png`, `.jpg`,
`.webp`) and text (`.json`, `.jsonl`, `.txt`, `.log`, served as `text/plain`). DOM snapshots and recordings are never
served, so site content is never rendered on the interface's origin. Draft files are shown only if they resolve to
a file under `data_dir/drafts`.

The bundle directory is `visits.path` when it exists on this host, otherwise `data_dir/visits/<day>/<visit id>`.

## Prometheus

`GET /metrics`:

| Metric | Type | Labels |
|---|---|---|
| `webparity_visits_total` | counter | `status` |
| `webparity_visits_last_hour` | gauge | |
| `webparity_queue_length` | gauge | |
| `webparity_findings_last_hour` | gauge | `category` (findings of visits started in the last hour) |
| `webparity_candidates` | gauge | `status` |
| `webparity_alerts_open` | gauge | |
| `webparity_nightly_last_success_timestamp` | gauge | (0 when there was never one) |
| `webparity_worker_last_visit_timestamp` | gauge | `worker` |

Example scrape configuration:

```yaml
scrape_configs:
  - job_name: webparity
    static_configs:
      - targets: ["127.0.0.1:8700"]
```

## Alerts

```
webparity alerts       # calls webparity.alerts.run_loop(config)
```

The loop evaluates the rules every minute. A rule that starts firing gets a row in the `alerts` table (`started`,
`message`) and a notification; when it stops firing the row gets `resolved` and a second notification. Nothing is
sent while an alert keeps firing. `notified` records when the last notification went out. Open alerts show on the
health page and in `webparity_alerts_open`.

| Rule | Fires when |
|---|---|
| `low_rate` | Fewer than `alerts.min_visits_per_hour` visits in the last hour. Not while that hour overlaps the quiet hours (`worker.quiet_hours`, a list or comma-separated string of `HH:MM-HH:MM`, may cross midnight), not before the first visit, and not in the first hour after it. `0` turns the rule off. |
| `nightly_failed` | The latest `runs` row of kind `nightly` has a status other than `ok` (a run that is still `running` does not count), or there was no successful nightly run in the last 26 hours (including never). |
| `probe_down` | An endpoint in `alerts.probe_endpoint` (a string, a comma-separated string or a list), or `endpoints.ladybird` and `endpoints.chromium` when it is empty, does not answer `GET <endpoint>/status` with 200 within 5 seconds. Only `http(s)` endpoints are probed. |

### Channels

Both optional; a failing channel is logged and does not stop the loop.

```yaml
alerts:
  webhook: "https://hooks.example.org/webparity"
  email:
    to: "ops@example.org"
    from: "webparity@example.org"
    smtp: "mail.example.org:25"
  min_visits_per_hour: 150
  probe_endpoint: ""
```

The webhook gets one `POST` per change with JSON:

```json
{"rule": "low_rate", "state": "firing", "message": "12 visits in the last hour (minimum 150)", "time": "2026-11-21 12:00:00"}
```

`state` is `firing` or `resolved`; `time` is local time like every timestamp in the database. Email goes out as one
plain text mail per evaluation with changes, over plain SMTP (no TLS or login; use a local relay).
