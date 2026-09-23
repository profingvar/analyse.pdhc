"""A real node on a real port, for tests that need the wire (#684 / AN-12).

Shared by the transport end-to-end tests and the CLI tests, so there is one
implementation of "stand a node up" rather than two that can drift.
"""
from __future__ import annotations

import threading

from werkzeug.serving import make_server

from app import create_app
from app.node.cohort_source import StaticCohortSource
from app.node.service import NodeContext
from app.routes.node_api import EXTENSION_KEY
from app.transport.client import NodeEndpoint

SECRET = "e" * 40
PROJECT = "synth"
#: Matches ProjectKey("synth", b"s" * 32), which the in-process harness uses,
#: so wire results and harness results are comparable.
PROJECT_KEY = "s" * 32


def narrowed(source, *, purposes: str):
    """The same source under a policy that permits fewer purposes."""
    from app.node import NodePolicy
    source.policy = NodePolicy.load(
        f"node_id: {source.node_id}\n"
        f"cdr_base_url: http://127.0.0.1:9046\n"
        f"permitted_purposes: {purposes}\n"
        f"k_min: 5\n", is_text=True)
    return source


class Node:
    """One node, served on a real port for the life of a test."""

    def __init__(self, source, *, secret: str = SECRET):
        self.app = create_app({
            "TESTING": True, "AUTH_MODE": "off",
            "DATABASE_URL": "sqlite:///:memory:",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "ANALYSE_ROLE": "node",
            "ANALYSE_TRANSPORT_SECRET": secret,
        })
        self.app.extensions[EXTENSION_KEY] = NodeContext(
            policy=source.policy,
            reader=source.reader(),
            ips_base_url="http://ips.invalid",
            cohort_source=StaticCohortSource(
                r["patient_guid"] for r in source.rows),
        )
        self._srv = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self._srv.server_port
        self._thread = threading.Thread(target=self._srv.serve_forever,
                                        daemon=True)
        self._thread.start()
        self.node_id = source.node_id

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def endpoint(self) -> NodeEndpoint:
        return NodeEndpoint(self.node_id, self.base_url)

    def close(self):
        self._srv.shutdown()
        self._thread.join(timeout=5)
