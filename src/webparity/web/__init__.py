# SPDX-License-Identifier: BSD-2-Clause
"""The monitoring web interface: server-rendered pages over the database, read-only, standard library only.

Start it with ``serve(config)``; it listens on ``web.listen`` (default ``127.0.0.1:8700``). See docs/monitoring.md.
"""

from webparity.web.server import make_server, serve

__all__ = ["make_server", "serve"]
