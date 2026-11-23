# SPDX-License-Identifier: BSD-2-Clause
from types import SimpleNamespace

from webparity.browsers import Handle


def test_a_session_whose_command_timed_out_is_not_alive():
    h = Handle("ladybird", "http://x")
    assert not h.alive()
    h.session = SimpleNamespace(client=SimpleNamespace(_closed=None, timeouts=0))
    assert h.alive()
    h.session.client.timeouts = 1  # e.g. browsingContext.navigate: no answer in 45 s
    assert not h.alive()
    h.session.client.timeouts, h.session.client._closed = 0, OSError("gone")
    assert not h.alive()
