# SPDX-License-Identifier: BSD-2-Clause
"""Ladybird's WebDriver log and crash reports: exceptions, failed loads, crashes, missing features, security reasons.

The visit bundle has ``ladybird.log`` (the log slice for the visit: stdout and stderr of WebDriver and all Ladybird
processes) and ``crash-report-N.txt`` (Ladybird's own crash reports from the profile).
``visit.data["log_reload_offset"]``
is the byte offset where the reload started: script exceptions are counted only before it (when Ladybird leaves the page
for the reload, the page's visibilitychange handlers run and throw - the same happens in Chromium, where nobody sees
it).
Crashes and everything else count over the whole log.
"""

from __future__ import annotations

import re

CRASH = re.compile(
    r"process crashed|panicked at|VERIFICATION FAILED|ASSERTION FAILED|Segmentation fault|SIGSEGV|SIGABRT"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Out-of-memory: the kernel kills WebContent when the container reaches its memory limit, with no crash line in the log.
# The old explorer read the container's cgroup oom_kill counter; the browsers run in their own containers now, so only
# what the log says explicitly counts (an allocation failure or a kill message).
OOM = re.compile(r"(?i)\bout of memory\b|std::bad_alloc|\boom[- ]kill")

# Security criteria: a crash that may be more than a controlled abort
SECURITY_SIGNALS = {"SIGSEGV", "SIGBUS", "SIGILL", "SIGFPE", "SIGTRAP"}
TRAP_SIGNALS = {"SIGILL", "SIGTRAP"}
VERIFY_REPORT = re.compile(r"^(?:Verification|Assertion) failed:", re.M)
SECURITY_VALUE = re.compile(
    r"(?i)use.after|dangling|already freed|double free|corrupt|misaligned|unaligned|null pointer|"
    r"is_nullish\(\)|is_object\(\)|is_cell\(\)|is_string\(\)|is_number\(\)|is_function\(\)|is_empty\(\)"
)
SECURITY_CODE = re.compile(r"/(?:jit|gc|heap)/|LibJS/JIT|LibGC|/Heap/|asm_int|JitCompiler")
OUT_OF_MEMORY = "out-of-memory"
# Logged by the top document after the load and the reload (visit.py), so its WebContent process is known: exceptions
# from other processes come from cross-site iframes (2026-11-22, reCAPTCHA's anchor frame on www.gstatic.com scripts)
TOP_MARKER = "@@webparity-top@@"
PROCESS = re.compile(r"\bWebContent\((\d+)\)")


def _norm(line: str) -> str:
    return re.sub(r"\d+", "N", re.sub(r"https?://\S+", "<url>", line)).strip()[:160]


def crash_locations(text: str) -> list[str]:
    """The crash locations in the log (2026-11-09); they become the signature crash:<location>, so different crashes
    are different candidates. Rust panic: the file name and the message on the line after "panicked at" (line numbers
    change between builds); VERIFY/ASSERT: the expression and the file name; otherwise the signal. Most specific
    first."""
    lines, out = text.splitlines(), []
    for i, line in enumerate(lines):
        line = re.sub(r"^[\d.]+ ", "", ANSI.sub("", line))
        m = re.search(r"panicked at (?:\S*/)?([\w.-]+\.rs):\d+", line)
        if m:
            msg = next(
                (
                    x.strip()
                    for x in lines[i + 1 : i + 3]
                    if x.strip() and "panicked at" not in x and not x.strip().startswith("note:")
                ),
                "",
            )
            out.append(f"{m.group(1)}: {re.sub(r'[0-9]+', 'N', msg)[:90]}".rstrip(": "))
            continue
        m = re.search(r"(VERIFICATION|ASSERTION) FAILED: (.*?) at (?:\S*/)?([\w.-]+\.(?:cpp|h|mm)):\d+", line)
        if m:
            out.append(f"{'VERIFY' if m.group(1) == 'VERIFICATION' else 'ASSERT'} {m.group(2)[:70]} @ {m.group(3)}")
            continue
        m = re.search(r"\b(SIGSEGV|SIGABRT|SIGBUS|SIGILL|Segmentation fault)\b", line)
        if m:
            out.append("SIGSEGV" if m.group(1) == "Segmentation fault" else m.group(1))
    seen: list[str] = []
    for x in out:
        if x not in seen:
            seen.append(x)
    return sorted(seen, key=lambda x: x.startswith("SIG"))[:5]


def parse(text: str) -> dict:
    """A log slice -> exceptions, failed loads, crashes, missing features, console errors, curl errors."""
    text = ANSI.sub("", text or "")
    exceptions: dict[str, int] = {}
    failed: list[dict] = []
    missing: dict[str, int] = {}
    curl_errors: list[str] = []
    console_errors = 0
    # The host of the first stack line per exception (2026-11-08): exceptions from cross-origin iframes are told apart
    # by
    # it when the BiDi events can't tell (see the script_error detector)
    exception_hosts: dict[str, str] = {}
    exception_stacks: dict[str, list[str]] = {}
    exception_pids: dict[str, set[str]] = {}
    top_pids: set[str] = set()
    last = None
    for line in text.splitlines():
        if TOP_MARKER in line:
            p = PROCESS.search(line)
            if p:
                top_pids.add(p.group(1))
            continue
        if last is not None:
            v = re.search(r"\bat .*?https?://([^/\s):]+)", line)
            if v:
                exception_hosts.setdefault(last, v.group(1))
                stack = exception_stacks.setdefault(last, [])
                if len(stack) < 5:
                    stack.append(re.sub(r"^.*?\bat ", "at ", line).strip()[:200])
                continue
            if not re.search(r"^\S* ?\S*:?\s+at ", line):
                last = None
        if "Unhandled JavaScript exception" in line:
            m = _norm(re.sub(r".*exception( \(in promise\))?:\s*", "", line))
            exceptions[m] = exceptions.get(m, 0) + 1
            p = PROCESS.search(line)
            if p:
                exception_pids.setdefault(m, set()).add(p.group(1))
            last = m
        elif "Failed load of" in line:
            m = re.search(r'Failed load of: "([^"]*)", Error: (.*?)(, Duration|$)', line)
            if m and len(failed) < 100:
                failed.append({"url": m.group(1)[:300], "error": m.group(2)[:120]})
        elif "(js error)" in line:
            console_errors += 1
        elif "Unable to map error" in line:
            m = re.search(r'Unable to map error \((\d+)\): "([^"]*)"', line)
            if m and len(curl_errors) < 20:
                curl_errors.append(f"{m.group(1)}: {m.group(2)}")
        for m in re.finditer(r"FIXME: Unimplemented[^\n]*|TODO\([^)]*\)|\b[\w .:'-]{3,80} is not implemented", line):
            k = _norm(m.group(0))
            missing[k] = missing.get(k, 0) + 1
    crash = sorted({_norm(re.sub(r"^[\d.]+ ", "", x)) for x in text.splitlines() if CRASH.search(x)})[:5]
    locations = crash_locations(text)
    if any(OOM.search(x) for x in text.splitlines()):
        crash = crash or ["WebContent ran out of memory"]
        locations = [f"{OUT_OF_MEMORY} (log)"] + [x for x in locations if not x.startswith(OUT_OF_MEMORY)]
    return {
        "exceptions": exceptions,
        "exception_hosts": exception_hosts,
        "exception_stacks": exception_stacks,
        "exception_pids": {k: sorted(v) for k, v in exception_pids.items()},
        "top_pids": sorted(top_pids),
        "failed_loads": failed,
        "missing": missing,
        "console_errors": console_errors,
        "crash": crash,
        "crash_locations": locations,
        "curl_errors": curl_errors,
    }


def parse_crash_report(text: str, name: str = "") -> dict:
    """A Ladybird crash report: process, signal (Termination signal, else Captured signal), whether it is a VERIFY."""
    fields = dict(re.findall(r"^([A-Z][\w ]+): (.*)$", text[:4000], re.M))
    return {
        "process": fields.get("Process", "?"),
        "signal": fields.get("Termination signal") or fields.get("Captured signal") or "?",
        "commit": fields.get("Git commit"),
        "file": name,
        # The text is not kept in findings, so the VERIFY exception in security_reasons needs the flag (2026-11-11:
        # otherwise VERIFY traps were flagged as security again)
        "verify": bool(VERIFY_REPORT.search(text)),
    }


def security_reasons(crash: dict) -> list[str]:
    """Why a crash may be a security bug, or an empty list. Out-of-memory never counts.

    ``crash`` has ``crash`` (log lines), ``locations`` and ``reports`` (parsed crash reports)."""
    locations = crash.get("locations") or []
    if not crash.get("crash") or (locations and locations[0].startswith(OUT_OF_MEMORY)):
        return []
    reasons, text = [], "\n".join(map(str, (crash.get("crash") or []) + locations))
    reports = crash.get("reports") or []
    # VERIFY/ASSERT stop with __builtin_trap(): SIGILL on x86_64, SIGTRAP on arm64 - a controlled abort, no sign of
    # memory
    # corruption (2026-11-10, Document.cpp:2442). Applies to reports with "Verification failed:" and, without reports,
    # to
    # the log's signal when the log has a VERIFY/ASSERT location.
    verify = any(p.startswith(("VERIFY ", "ASSERT ")) for p in locations)
    signals = {r.get("signal") for r in reports if not (r.get("signal") in TRAP_SIGNALS and r.get("verify"))}
    for p in locations:
        m = re.match(r"SIG\w+", p)
        if m and not (verify and m.group(0) in TRAP_SIGNALS):
            signals.add(m.group(0))
    if signals & SECURITY_SIGNALS:
        reasons.append("signal " + "/".join(sorted(signals & SECURITY_SIGNALS)))
    others = sorted({r.get("process") for r in reports} - {"WebContent", "?", None})
    if others:
        reasons.append("process " + "/".join(others))
    if SECURITY_VALUE.search(text):
        reasons.append("invalid value")
    if SECURITY_CODE.search(text):
        reasons.append("JIT/GC")
    return reasons


def _text(bundle, limit: int | None = None) -> str:
    raw = bundle.read_file("ladybird.log") or b""
    if limit is not None:
        raw = raw[: max(0, limit)]
    return raw.decode("utf-8", "replace")


def for_bundle(bundle) -> dict:
    """Everything the detectors need from Ladybird's log and crash reports, parsed once per bundle object.

    ``full``: the whole log; ``first``: the log up to the reload (exceptions); ``crash``: crash lines, locations and
    reports merged (a report gives the process and signal the log may lack: a SIGSEGV without a crash line, or a crash
    in RequestServer/ImageDecoder); ``text``: the raw log."""
    cache = bundle.__dict__.setdefault("_ladybird_log", {})
    if cache:
        return cache
    text = _text(bundle)
    full = parse(text)
    offset = bundle.data.get("log_reload_offset")
    first = parse(_text(bundle, offset)) if offset is not None else full
    reports = []
    for i in range(1, 10):
        name = f"crash-report-{i}.txt"
        content = bundle.read_file(name)
        if content is None:
            break
        reports.append(parse_crash_report(content.decode("utf-8", "replace"), name))
    crash = list(full["crash"])
    locations = list(full["crash_locations"])
    if reports:
        if not crash:
            crash = [f"{r['process']} crashed ({r['signal']})" for r in reports][:5]
        if not locations:
            locations = [f"{r['signal']} in {r['process']}" for r in reports][:5]
        elif locations[0].startswith("SIG") and " in " not in locations[0]:
            locations[0] += f" in {reports[0]['process']}"
    alive = (bundle.data.get("alive") or {}).get("ladybird")
    if alive is False and not crash:
        crash = ["the WebDriver session ended"]
    cache.update(
        {
            "text": text,
            "full": full,
            "first": first,
            "crash": {"crash": crash, "locations": locations, "reports": reports},
        }
    )
    return cache
