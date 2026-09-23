"""The node's HTTP surface (#684 / AN-12).

One route. A node accepts a sealed spec and returns sealed partials, and
offers nothing else — no browsing, no free-form query, no way to ask it about
a patient. The brief's "never sends a free-form query to a node" is held here
by there being nothing to send one to.

**Authentication is the envelope.** This route is outside the SSO gate
deliberately: the caller is a coordinator process, not a person, and there is
no session to validate. The seal over the transmitted bytes is what
authenticates it, and an unsigned or stale request is refused before the body
is parsed.

**Failures are named, never empty.** A refusal, an unavailable consent filter
and an unresolvable cohort each return a distinct status with a reason,
because on the coordinator side all three would otherwise arrive as "this
source contributed nothing" — which reads as a smaller cohort rather than as
a broken one.
"""
from __future__ import annotations

from flask import Blueprint, Response, current_app, request

from app.node.service import (
    CohortError, ConsentUnavailable, NodeContext, NodeNotConfigured,
    NodeRefusal, PolicyError, ReadError, handle_run,
)
from app.transport.envelope import (
    REQUEST_KIND, RESPONSE_KIND, UnsealError, seal, secret_from_config, unseal,
)

bp = Blueprint("node_api", __name__)

#: Where a prebuilt NodeContext lives on the Flask app.
EXTENSION_KEY = "analyse_node_context"


def node_context(app) -> NodeContext:
    """This deployment's node context, built once and cached.

    Cached on `app.extensions` rather than rebuilt per request: the policy is
    a file read and the context holds a read client, and re-reading the policy
    on every request would mean an edit to the file taking effect mid-run,
    with one half of a fan-out answering under the old terms.

    A deployment that assembles its own context (the synthetic harness, and
    tests that stand a node on a real port) puts it here at startup.
    """
    ctx = app.extensions.get(EXTENSION_KEY)
    if ctx is None:
        ctx = NodeContext.from_config(app.config)
        app.extensions[EXTENSION_KEY] = ctx
    return ctx



def _error(status: int, code: str, message: str) -> Response:
    """Plain JSON, never sealed.

    An error is not a result, and sealing it would invite a coordinator to
    treat a verified envelope as a verified answer. The coordinator's client
    reads these by status code.
    """
    from flask import jsonify
    resp = jsonify({"error": code, "message": message})
    resp.status_code = status
    return resp


@bp.post("/api/v1/node/run")
def run():
    try:
        secret = secret_from_config(current_app.config)
    except Exception as e:
        current_app.logger.error("node transport secret missing: %s", e)
        return _error(503, "not_configured", str(e))

    try:
        payload = unseal(request.get_data(), request.headers, secret,
                         kind=REQUEST_KIND)
    except UnsealError as e:
        # 401: the caller is not established as the coordinator. Deliberately
        # not 400 — a bad signature is an authentication failure, and a
        # coordinator retrying on 400 would hammer a node it cannot talk to.
        return _error(401, "envelope_rejected", str(e))

    try:
        ctx = node_context(current_app)
    except NodeNotConfigured as e:
        return _error(503, "not_configured", str(e))

    try:
        body = handle_run(payload, ctx, coordinator_secret=secret)
    except (NodeRefusal, PolicyError) as e:
        # 403: this organisation declines the question. Not a retry.
        return _error(403, "refused", str(e))
    except ConsentUnavailable as e:
        # 503: the facts a lawful read needs could not be established. The
        # node is not saying "no data", it is saying "I could not find out".
        return _error(503, "consent_unavailable", str(e))
    except CohortError as e:
        return _error(503, "cohort_unavailable", str(e))
    except ReadError as e:
        return _error(502, "read_failed", str(e))

    out, headers = seal(body, secret, kind=RESPONSE_KIND)
    return Response(out, status=200, headers=headers)
