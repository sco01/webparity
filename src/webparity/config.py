# SPDX-License-Identifier: BSD-2-Clause
"""Configuration: ``config.yaml`` (see ``config.example.yaml``), with defaults for everything.

The file is looked up in ``$WEBPARITY_CONFIG``, then ``./config.yaml``, then ``/etc/webparity/config.yaml``.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    # Where visit bundles, recordings, the database and logs live
    "data_dir": "data",
    # The browsers compared on every visit. Ladybird is the engine under test; the others are references.
    "browsers": ["ladybird", "chromium"],
    "endpoints": {
        "ladybird": "http://127.0.0.1:9616",
        "chromium": "http://127.0.0.1:9515",
        "firefox": "ws://127.0.0.1:9222",
    },
    "proxy": {
        # Recording is on by default: the recording is the source of truth for network traffic and makes replays and
        # offline reduction possible. Off: browsers connect directly (HTTP/3 stays possible).
        "record": True,
        "address": "127.0.0.1:8790",
        "control": "http://127.0.0.1:8791",
        # Keep recordings only for visits with error findings and for canary pages
        "keep": "findings",
    },
    "visit": {
        "stable_timeout": 20,  # seconds to wait for a page to stop changing
        "deep": True,  # interaction steps (scroll, wheel, mobile, menu) and the reload
        "steps": ["load", "probe", "idle", "interact", "reload"],
    },
    "worker": {
        # Polite crawling (from How I test Ladybird): at most one visit per site at a time, and a pause per site
        "per_site_interval_hours": 24,
        "max_load": 12.0,  # pause while the 1-minute load average is above this
        # Quiet hours: no visits, so the nightly build and regression watch get the CPU. "HH:MM-HH:MM", local time.
        "quiet_hours": ["01:00-02:30"],
        # Never visited (2026-11-23): servers as addresses or networks ("192.0.2.0/24") and domains with their
        # subdomains ("example.com"), for hosts that must not see webparity's traffic - a server whose firewall
        # blocks the address webparity shares with people who need the sites hosted there, say
        "exclude": {"servers": [], "domains": []},
    },
    "site_lists": [],
    # New sites from the visits (webparity.discover): linked domains in these top-level domains, at most per_visit per
    # visit, and sub pages of front pages; never more than per_domain rows per registrable domain
    "discover": {
        "enabled": True,
        "tlds": ["se"],
        "per_visit": 15,
        "per_domain": 5,
        "subpages": 2,
        "priority": 30,
        "subpage_priority": 25,
        "skip_domains": [],
    },
    "user_agent_note": "",  # never used to fake a user agent; the browsers send their own
    "web": {"listen": "127.0.0.1:8700"},
    "alerts": {
        "webhook": "",  # POST JSON to this URL
        "email": {"to": "", "from": "", "smtp": ""},
        "pushover": {"credentials": "", "app": "", "priority": 0},  # credentials: file with PUSHOVER_TOKEN/USER
        "min_visits_per_hour": 0,  # 0: off; set it to about two thirds of your usual rate
        "probe_endpoint": "",
    },
    "github": {
        "repository": "LadybirdBrowser/ladybird",
        "user_agent": "webparity (+https://github.com/sco01/webparity)",
        "interval_minutes": 60,
    },
    # Known differences and bugs: the YAML directory (default: known/ next to the package)
    "known": {"dir": ""},
    "ladybird": {
        # The Ladybird source checkout, for "is this fix in the build" (git merge-base); read-only
        "source": "/src",
        # The WebDriver log and profile shared with the ladybird container, for crashes and crash reports
        "log": "/run/webparity/ladybird/webdriver.log",
        "profile": "/run/webparity/ladybird/profile",
        "build_info": "/src/Build/release/webparity-build.json",
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def find_config() -> Path | None:
    for candidate in (os.environ.get("WEBPARITY_CONFIG"), "config.yaml", "/etc/webparity/config.yaml"):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def load(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else find_config()
    extra: dict = {}
    if path is not None:
        import yaml

        extra = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = _merge(DEFAULTS, extra)
    for name, env in (
        ("ladybird", "WEBPARITY_LADYBIRD"),
        ("chromium", "WEBPARITY_CHROMIUM"),
        ("firefox", "WEBPARITY_FIREFOX"),
    ):
        if os.environ.get(env):
            config["endpoints"][name] = os.environ[env]
    if os.environ.get("WEBPARITY_BUILD_INFO"):
        config["ladybird"]["build_info"] = os.environ["WEBPARITY_BUILD_INFO"]
    if os.environ.get("WEBPARITY_DATA"):
        config["data_dir"] = os.environ["WEBPARITY_DATA"]
    config["data_dir"] = str(Path(config["data_dir"]).resolve())
    return config
