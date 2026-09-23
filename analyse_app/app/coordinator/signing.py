"""Spec signing (#651).

A node accepts only specs signed by the coordinator. The signature is over the
CANONICAL form from AN-1, so it commits to the spec's meaning rather than to
one serialisation of it — a node that re-serialises before verifying still
gets the same bytes.

HMAC rather than a public-key signature, deliberately: coordinator and nodes
are one deployment with a shared secret store, and mTLS already establishes
who is talking. A signature here answers "is this the spec the coordinator
approved", not "who sent it".
"""
from __future__ import annotations

import hmac
import hashlib

from app.spec import AnalysisSpec, canonical_json


class SignatureError(ValueError):
    pass


def sign(spec: AnalysisSpec, secret: bytes) -> str:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < 32:
        raise SignatureError("signing secret must be at least 32 bytes")
    mac = hmac.new(bytes(secret), canonical_json(spec).encode("utf-8"),
                   hashlib.sha256)
    return f"hmac-sha256:{mac.hexdigest()}"


def verify(spec: AnalysisSpec, signature: str, secret: bytes) -> None:
    """Raise unless the signature matches. Constant-time comparison, so a
    node does not leak how close a forgery was."""
    expected = sign(spec, secret)
    if not hmac.compare_digest(expected, signature or ""):
        raise SignatureError(
            "spec signature does not verify — a node runs only specs the "
            "coordinator approved")
