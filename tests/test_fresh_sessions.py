# SPDX-License-Identifier: BSD-2-Clause
"""Every session starts without earlier cookies, storage and cache: Ladybird's WebDriver is restarted on an empty
profile, Firefox gets a user context of its own."""

import pytest

from webparity import bidi


class FakeLadybird:
    """The ladybird container's start script: SIGUSR1 starts a new WebDriver generation."""

    def __init__(self, run, restarts=True, ready=True):
        self.run, self.restarts, self.ready, self.signals = run, restarts, ready, 0
        (run / "supervisor.pid").write_text("4242\n")
        (run / "profile-generation").write_text("1\n")

    def kill(self, pid, sig):
        assert pid == 4242
        self.signals += 1
        if self.restarts:
            (self.run / "profile-generation").write_text("2\n")

    def webdriver(self, base, method, path, body=None, timeout=90):
        assert (method, path) == ("GET", "/status")
        return {"ready": self.ready}


@pytest.fixture
def ladybird(tmp_path, monkeypatch):
    def make(**kwargs):
        fake = FakeLadybird(tmp_path, **kwargs)
        monkeypatch.setattr(bidi, "_is_supervisor", lambda pid: True)
        monkeypatch.setattr(bidi.os, "kill", fake.kill)
        monkeypatch.setattr(bidi, "webdriver", fake.webdriver)
        return fake

    return make


def test_restarts_the_webdriver_and_waits_for_it(ladybird, tmp_path):
    fake = ladybird()
    assert bidi.fresh_ladybird_profile("http://wd", str(tmp_path), timeout=5)
    assert fake.signals == 1


def test_leaves_a_running_session_alone(ladybird, tmp_path):
    fake = ladybird(ready=False)
    assert not bidi.fresh_ladybird_profile("http://wd", str(tmp_path), timeout=5)
    assert fake.signals == 0


def test_without_the_start_script_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.setattr(bidi.os, "kill", lambda *a: pytest.fail("signalled"))
    assert not bidi.fresh_ladybird_profile("http://wd", str(tmp_path), timeout=5)
    (tmp_path / "supervisor.pid").write_text("4242")
    monkeypatch.setattr(bidi, "_is_supervisor", lambda pid: False)  # the pid is some other process
    assert not bidi.fresh_ladybird_profile("http://wd", str(tmp_path), timeout=5)


def test_a_webdriver_that_does_not_come_back_is_an_error(ladybird, tmp_path):
    ladybird(restarts=False)
    with pytest.raises(bidi.BidiError):
        bidi.fresh_ladybird_profile("http://wd", str(tmp_path), timeout=0.5)


class FakeFirefoxClient:
    def __init__(self, url, timeout=30):
        self.calls = []
        self.user_contexts = ["default", "left-behind"]
        self.tabs = [{"context": "tab-1"}, {"context": "tab-2"}]
        FakeFirefoxClient.last = self

    def command(self, method, params=None, timeout=60):
        self.calls.append((method, params))
        if method == "browser.getUserContexts":
            return {"userContexts": [{"userContext": u} for u in self.user_contexts]}
        if method == "browsingContext.getTree":
            return {"contexts": list(self.tabs)}
        if method == "browser.createUserContext":
            return {"userContext": "visit"}
        if method == "browsingContext.create":
            return {"context": "visit-window"}
        return {}

    def close(self):
        pass


def test_firefox_session_gets_its_own_user_context(monkeypatch):
    monkeypatch.setattr(bidi, "BidiClient", FakeFirefoxClient)
    session = bidi.Session.firefox("ws://ff")
    client = FakeFirefoxClient.last
    assert session.context == "visit-window"
    assert ("browser.removeUserContext", {"userContext": "left-behind"}) in client.calls
    assert ("browsingContext.close", {"context": "tab-2"}) in client.calls
    assert ("browsingContext.create", {"type": "window", "userContext": "visit"}) in client.calls
    assert not any(m == "browsingContext.close" and p["context"] == "tab-1" for m, p in client.calls)
    session.end()
    assert client.calls[-2:] == [("browser.removeUserContext", {"userContext": "visit"}), ("session.end", {})]
