# SPDX-License-Identifier: BSD-2-Clause
import urllib.error
import urllib.request

import pytest

from webparity import canary

LADYBIRD_UA = "Mozilla/5.0 (X11; Linux x86_64) Ladybird/0.1.0 AppleWebKit/537.36"


@pytest.fixture
def base():
    url, server = canary.start_server()
    yield url
    server.shutdown()


def get(url, ua="Mozilla/5.0"):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua}), timeout=5)


def test_every_canary_page_is_served(base):
    for entry in canary.entries(base):
        with get(entry["url"]) as r:
            body = r.read().decode()
        assert r.status == 200 and "<title>" in body and "const LB" in body


def test_only_the_clean_page_expects_nothing(base):
    entries = canary.entries(base)
    assert [e["name"] for e in entries if not e["expect"]] == ["clean"]
    assert len({e["name"] for e in entries}) == len(entries)


def test_fail_js_drops_only_ladybird(base):
    with get(base + "/fail.js") as r:
        assert r.read() == b"window.ok=1;"
    with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
        get(base + "/fail.js", LADYBIRD_UA).read()


def test_crash_me_only_for_ladybird(base, monkeypatch):
    import time

    called = []
    monkeypatch.setattr(canary, "crash_newest_webcontent", lambda: called.append(1) or True)
    with pytest.raises(urllib.error.HTTPError):  # other browsers get a 404
        get(base + "/crash-me")
    with get(base + "/crash-me", LADYBIRD_UA) as r:
        assert r.status == 204
    time.sleep(0.4)
    assert called == [1]


def test_unknown_path_is_404(base):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base + "/nothing")
    assert exc.value.code == 404
