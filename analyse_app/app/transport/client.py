"""The coordinator's client, and the fan-out (#684 / AN-12).

The rule that shapes this file: **a source that did not answer must be named
as a source that did not answer.** Every failure path below ends in the
`failures` dict that `combine()` turns into a degraded `SourceStatus` and a
note on the result. None of them ends in a silent omission.

That is not defensive tidiness. A merged figure over four of five sources is a
different number from the same figure over five, and the two are
indistinguishable to a reader unless the result says which it is. A dropped
node would make an analysis quietly about a smaller population than the
analyst asked about, and nothing downstream could detect it.

Fan-out is concurrent because nodes are independent and a serial fan-out makes
the slowest node the run's latency. It is bounded because an unbounded pool
against a long source list is a way to take out one's own CDRs.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Any

import requests

from app.privacy.disclosure import DisclosurePolicy
from app.spec import AnalysisSpec, spec_hash

from .envelope import (
    REQUEST_KIND, RESPONSE_KIND, TransportError, UnsealError, seal, unseal,
)

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_WORKERS = 8

RUN_PATH = "/api/v1/node/run"


class NodeUnavailable(RuntimeError):
    """A node did not produce a usable answer. Carries an operator-readable
    reason, which is what the result will show beside that source."""


@dataclass(frozen=True)
class NodeEndpoint:
    node_id: str
    base_url: str

    @property
    def run_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{RUN_PATH}"


def endpoints_from_config(config: Any) -> list[NodeEndpoint]:
    """`ANALYSE_NODES` as a mapping of node_id -> base url."""
    raw = config.get("ANALYSE_NODES") or {}
    if isinstance(raw, str):
        out = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            node_id, _, url = chunk.partition("=")
            if not url:
                raise ValueError(
                    f"ANALYSE_NODES entry '{chunk}' must be '<node_id>=<url>'")
            out.append(NodeEndpoint(node_id.strip(), url.strip()))
        return out
    return [NodeEndpoint(k, v) for k, v in sorted(raw.items())]


class NodeClient:
    """One node, one sealed round trip."""

    def __init__(self, endpoint: NodeEndpoint, secret: bytes, *,
                 timeout: float = DEFAULT_TIMEOUT,
                 session: requests.Session | None = None):
        self.endpoint = endpoint
        self._secret = secret
        self.timeout = timeout
        self._session = session or requests

    def run(self, spec: AnalysisSpec, *, project_id: str,
            spec_signature: str | None = None,
            k_min: int | None = None,
            research_projects: tuple[str, ...] = ()) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "spec": spec.model_dump(mode="json", by_alias=True),
            "project_id": project_id,
        }
        if spec_signature:
            payload["spec_signature"] = spec_signature
        if k_min is not None:
            payload["k_min"] = int(k_min)
        if research_projects:
            payload["research_projects"] = list(research_projects)

        body, headers = seal(payload, self._secret, kind=REQUEST_KIND)

        try:
            # `data=body`, never `json=payload`: the signature is over these
            # exact octets, and letting requests re-serialise would break it
            # in whatever way that library's encoder differs.
            r = self._session.post(self.endpoint.run_url, data=body,
                                   headers=headers, timeout=self.timeout)
        except requests.Timeout as e:
            raise NodeUnavailable(
                f"did not answer within {self.timeout:g}s") from e
        except requests.RequestException as e:
            raise NodeUnavailable(f"could not be reached: {e}") from e

        if r.status_code != 200:
            raise NodeUnavailable(_reason(r))

        try:
            out = unseal(r.content, r.headers, self._secret,
                         kind=RESPONSE_KIND)
        except UnsealError as e:
            raise NodeUnavailable(f"returned an unusable envelope: {e}") from e
        except TransportError as e:
            raise NodeUnavailable(str(e)) from e

        answered = out.get("spec_hash")
        expected = spec_hash(spec)
        if answered and answered != expected:
            # A node answering a different question than it was asked is not a
            # degraded source, it is a wrong number wearing the right label.
            raise NodeUnavailable(
                f"answered a different spec ({answered[:12]}…, expected "
                f"{expected[:12]}…)")
        out.setdefault("node_id", self.endpoint.node_id)
        return out


def _reason(r: requests.Response) -> str:
    """An operator-readable reason from a node's error response."""
    code, message = "", ""
    try:
        blob = r.json() or {}
        code = str(blob.get("error") or "")
        message = str(blob.get("message") or "")
    except ValueError:
        message = (r.text or "")[:200]
    if r.status_code == 401:
        return (f"rejected the coordinator's credentials ({message or code}) "
                f"— check ANALYSE_TRANSPORT_SECRET on both sides")
    if r.status_code == 403:
        return f"refused this analysis: {message or code}"
    if r.status_code == 503:
        return f"could not answer: {message or code}"
    return f"returned HTTP {r.status_code}: {message or code or 'no detail'}"


def fan_out(spec: AnalysisSpec, endpoints: list[NodeEndpoint], secret: bytes,
            *, project_id: str, spec_signature: str | None = None,
            k_min: int | None = None,
            research_projects: tuple[str, ...] = (),
            timeout: float = DEFAULT_TIMEOUT,
            max_workers: int = DEFAULT_MAX_WORKERS,
            session: requests.Session | None = None,
            ) -> tuple[list[dict[str, Any]], dict[str, str],
                       dict[str, DisclosurePolicy]]:
    """Ask every node, in parallel. Returns (runs, failures, policies).

    Never raises for a node-level problem — that is the caller's `failures`
    dict, and losing one source degrades that source rather than the run.
    """
    runs: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    policies: dict[str, DisclosurePolicy] = {}

    if not endpoints:
        return runs, failures, policies

    def _one(ep: NodeEndpoint):
        return NodeClient(ep, secret, timeout=timeout, session=session).run(
            spec, project_id=project_id, spec_signature=spec_signature,
            k_min=k_min, research_projects=research_projects)

    workers = max(1, min(max_workers, len(endpoints)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, ep): ep for ep in endpoints}
        for fut in concurrent.futures.as_completed(futures):
            ep = futures[fut]
            try:
                out = fut.result()
            except NodeUnavailable as e:
                failures[ep.node_id] = str(e)
                continue
            except Exception as e:                      # noqa: BLE001
                # Deliberately broad. An unexpected exception here would
                # otherwise propagate and fail the WHOLE run, turning one
                # sick node into a platform-wide outage of the analysis tool.
                failures[ep.node_id] = f"unexpected client error: {e}"
                continue

            runs.append(out)
            pol = out.get("policy") or {}
            if "k_min" in pol:
                k = int(pol["k_min"])
                # floor=k: this node's own minimum. The coordinator can make
                # the merged policy stricter and cannot make it looser.
                policies[out.get("node_id", ep.node_id)] = DisclosurePolicy(
                    k_min=k, floor=k)

    return runs, failures, policies
