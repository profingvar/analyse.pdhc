"""The wire format between a coordinator and a node (#684 / AN-12).

Everything crossing this boundary is sealed: serialised ONCE, attested over
those exact bytes, and verified over the bytes as received before anything
parses them.

**Why verify before parsing.** The obvious implementation — parse the JSON,
re-serialise it canonically, then check the MAC — is wrong in a way that does
not show up in tests. It makes the signature a property of what the receiver's
parser produced rather than of what the sender sent, so any parser
disagreement (duplicate keys, number formatting, unicode normalisation) is a
gap between what was signed and what is acted on. The spec signature in
`coordinator/signing.py` can safely canonicalise, because a spec is a model
with one canonical form by construction and that is the whole point of AN-1.
A node's partials are not: they carry floats, sketch centroids and counts,
where re-serialisation is exactly where bytes move. So here the rule is the
blunt one — **the MAC covers the transmitted octets**.

**What the MAC binds.** Not just the body. `kind` and `issued_at` are in the
signing string, so a response cannot be replayed as a request, and a captured
envelope cannot be re-dated. Binding the body alone would leave both open.

**What this is not.** HMAC with a shared secret answers "did the other half of
this deployment send this, unaltered". It does not answer "which node", beyond
the fact that the node id travels inside the sealed body. Coordinator and
nodes are one deployment with one secret store, the same argument the spec
signing module makes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

#: Header names. Lowercase on the wire; Flask and requests both fold case.
SIG_HEADER = "X-Analyse-Signature"
KIND_HEADER = "X-Analyse-Kind"
ISSUED_HEADER = "X-Analyse-Issued-At"

CONTENT_TYPE = "application/json"

#: How long a sealed envelope stays acceptable. Long enough to survive a slow
#: node and a clock a few seconds out; short enough that a captured request
#: cannot be replayed tomorrow to make a node re-read patient data.
DEFAULT_MAX_AGE_SECONDS = 300

#: Tolerance for a receiver whose clock is BEHIND the sender's. Without it,
#: two boxes seconds apart reject each other's traffic and the failure looks
#: like a signature problem rather than a clock problem.
CLOCK_SKEW_SECONDS = 60

MIN_SECRET_BYTES = 32


class TransportError(RuntimeError):
    """Anything wrong with an envelope. Never carries the secret."""


class SealError(TransportError):
    """The envelope could not be created."""


class UnsealError(TransportError):
    """The envelope did not verify, or is stale, or is the wrong kind."""


def _require_secret(secret: bytes) -> bytes:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < MIN_SECRET_BYTES:
        raise SealError(
            f"transport secret must be at least {MIN_SECRET_BYTES} bytes")
    return bytes(secret)


def _mac(secret: bytes, kind: str, issued_at: str, body: bytes) -> str:
    """The signing string binds kind and issue time to the body octets.

    Newline-separated with the body last: `kind` and `issued_at` contain no
    newline (both are validated), so no two different triples can produce the
    same signing string.
    """
    preamble = f"{kind}\n{issued_at}\n".encode("utf-8")
    return "hmac-sha256:" + hmac.new(
        secret, preamble + body, hashlib.sha256).hexdigest()


def _clean(label: str, value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise SealError(f"{label} must be a single-line non-empty string")
    return value


def seal(payload: dict[str, Any], secret: bytes, *, kind: str,
         issued_at: float | None = None) -> tuple[bytes, dict[str, str]]:
    """Serialise once and attest the result.

    Returns the exact bytes to put on the wire and the headers that go with
    them. The caller MUST send these bytes unmodified — re-serialising the
    payload, even identically, defeats the point of signing octets.
    """
    secret = _require_secret(secret)
    kind = _clean("kind", kind)
    stamp = _clean("issued_at",
                   str(int(time.time() if issued_at is None else issued_at)))
    try:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False
                          ).encode("utf-8")
    except (TypeError, ValueError) as e:
        # allow_nan=False: a NaN would serialise to a token no strict JSON
        # parser accepts, so the node would produce something the coordinator
        # cannot read, and only in the data-dependent case.
        raise SealError(f"payload is not serialisable as strict JSON: {e}") from e

    return body, {
        "Content-Type": CONTENT_TYPE,
        SIG_HEADER: _mac(secret, kind, stamp, body),
        KIND_HEADER: kind,
        ISSUED_HEADER: stamp,
    }


def unseal(body: bytes, headers: Any, secret: bytes, *, kind: str,
           max_age: int = DEFAULT_MAX_AGE_SECONDS,
           now: float | None = None) -> dict[str, Any]:
    """Verify over the bytes as received, then parse. In that order.

    `headers` is anything with `.get` — a Flask request.headers or a plain
    dict both work.
    """
    secret = _require_secret(secret)

    got_kind = (headers.get(KIND_HEADER) or "").strip()
    stamp = (headers.get(ISSUED_HEADER) or "").strip()
    signature = (headers.get(SIG_HEADER) or "").strip()

    if not signature:
        raise UnsealError("unsigned request — this endpoint accepts only "
                          "envelopes sealed with the transport secret")
    if got_kind != kind:
        # A response replayed at a request endpoint, or vice versa.
        raise UnsealError(
            f"envelope is of kind '{got_kind or '(none)'}', expected '{kind}'")
    try:
        issued = int(stamp)
    except ValueError:
        raise UnsealError("envelope has no usable issue time") from None

    expected = _mac(secret, got_kind, stamp, body)
    if not hmac.compare_digest(expected, signature):
        # Constant-time, and deliberately says nothing about how close it was.
        raise UnsealError("envelope signature does not verify")

    clock = time.time() if now is None else now
    age = clock - issued
    if age > max_age:
        raise UnsealError(
            f"envelope is {int(age)}s old (limit {max_age}s) — refusing a "
            f"possible replay")
    if age < -CLOCK_SKEW_SECONDS:
        raise UnsealError(
            f"envelope is dated {int(-age)}s in the future — check the clocks "
            f"on the coordinator and this node")

    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise UnsealError(f"envelope body is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise UnsealError("envelope body must be a JSON object")
    return parsed


def secret_from_config(config: Any) -> bytes:
    """The shared transport secret, as bytes, or a clear refusal.

    Deliberately not defaulted. A transport secret that falls back to a
    development constant is one that ships to production working, and the
    signature then attests nothing at all.
    """
    raw = (config.get("ANALYSE_TRANSPORT_SECRET") or "") if hasattr(config, "get") \
        else ""
    if not raw:
        raise SealError(
            "ANALYSE_TRANSPORT_SECRET is not set — a coordinator and its "
            "nodes share this secret and neither will talk without it")
    return raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)


#: The two kinds on this wire. Bound into the MAC so a captured response
#: cannot be replayed into a request endpoint.
REQUEST_KIND = "analyse.node.run.request"
RESPONSE_KIND = "analyse.node.run.response"
