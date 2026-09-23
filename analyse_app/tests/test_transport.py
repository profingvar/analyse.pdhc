"""#684 (AN-12) — the envelope: what it binds, and what it refuses."""
from __future__ import annotations

import json
import time

import pytest

from app.transport.envelope import (
    ISSUED_HEADER, KIND_HEADER, REQUEST_KIND, RESPONSE_KIND, SIG_HEADER,
    SealError, UnsealError, seal, secret_from_config, unseal,
)

SECRET = b"s" * 32
OTHER = b"t" * 32


def _sealed(payload=None, **kw):
    return seal(payload or {"hello": "värld"}, SECRET, kind=REQUEST_KIND, **kw)


class TestRoundTrip:

    def test_a_sealed_envelope_opens(self):
        body, headers = _sealed()
        assert unseal(body, headers, SECRET, kind=REQUEST_KIND) == {"hello": "värld"}

    def test_the_body_is_utf8_not_escaped(self):
        """Swedish text stays itself, so the bytes match what a human reads
        when they need to see what was signed."""
        body, _ = _sealed()
        assert "värld" in body.decode("utf-8")


class TestWhatTheMacBinds:

    def test_a_changed_body_does_not_verify(self):
        body, headers = _sealed()
        with pytest.raises(UnsealError, match="does not verify"):
            unseal(body.replace(b"v\xc3\xa4rld", b"other"), headers, SECRET,
                   kind=REQUEST_KIND)

    def test_a_different_secret_does_not_verify(self):
        body, headers = _sealed()
        with pytest.raises(UnsealError, match="does not verify"):
            unseal(body, headers, OTHER, kind=REQUEST_KIND)

    def test_a_response_cannot_be_replayed_as_a_request(self):
        """kind is inside the MAC, so relabelling the header is not enough
        and re-signing is not possible without the secret."""
        body, headers = seal({"x": 1}, SECRET, kind=RESPONSE_KIND)
        with pytest.raises(UnsealError, match="kind"):
            unseal(body, headers, SECRET, kind=REQUEST_KIND)

    def test_the_issue_time_cannot_be_moved(self):
        body, headers = _sealed(issued_at=time.time() - 10_000)
        headers[ISSUED_HEADER] = str(int(time.time()))   # look fresh
        with pytest.raises(UnsealError, match="does not verify"):
            unseal(body, headers, SECRET, kind=REQUEST_KIND)


class TestRefusals:

    def test_an_unsigned_request_is_refused(self):
        body, headers = _sealed()
        headers[SIG_HEADER] = ""
        with pytest.raises(UnsealError, match="unsigned"):
            unseal(body, headers, SECRET, kind=REQUEST_KIND)

    def test_a_stale_envelope_is_refused(self):
        body, headers = _sealed(issued_at=time.time() - 10_000)
        with pytest.raises(UnsealError, match="refusing a possible replay"):
            unseal(body, headers, SECRET, kind=REQUEST_KIND)

    def test_an_envelope_from_the_future_names_the_clocks(self):
        body, headers = _sealed(issued_at=time.time() + 10_000)
        with pytest.raises(UnsealError, match="clocks"):
            unseal(body, headers, SECRET, kind=REQUEST_KIND)

    def test_small_clock_skew_is_tolerated(self):
        """Two boxes seconds apart must not look like a signature failure."""
        body, headers = _sealed(issued_at=time.time() + 5)
        unseal(body, headers, SECRET, kind=REQUEST_KIND)

    def test_a_weak_secret_is_refused(self):
        with pytest.raises(SealError, match="at least 32 bytes"):
            seal({}, b"short", kind=REQUEST_KIND)

    def test_a_nan_is_refused_at_seal_time(self):
        """Not at the far end, data-dependently, in production."""
        with pytest.raises(SealError, match="strict JSON"):
            seal({"v": float("nan")}, SECRET, kind=REQUEST_KIND)

    def test_a_non_object_body_is_refused(self):
        body = json.dumps([1, 2]).encode()
        _, headers = seal({}, SECRET, kind=REQUEST_KIND)
        # re-MAC the list body so only the shape is wrong
        from app.transport.envelope import _mac
        headers[SIG_HEADER] = _mac(SECRET, REQUEST_KIND,
                                   headers[ISSUED_HEADER], body)
        with pytest.raises(UnsealError, match="JSON object"):
            unseal(body, headers, SECRET, kind=REQUEST_KIND)


class TestSecretFromConfig:

    def test_a_missing_secret_is_an_error_not_a_default(self):
        with pytest.raises(SealError, match="ANALYSE_TRANSPORT_SECRET"):
            secret_from_config({})

    def test_a_configured_secret_is_bytes(self):
        assert secret_from_config({"ANALYSE_TRANSPORT_SECRET": "x" * 32}) \
            == b"x" * 32
