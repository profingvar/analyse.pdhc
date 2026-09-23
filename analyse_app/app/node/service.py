"""The node role: turning a sealed request into a NodeRun (#684 / AN-12).

This is the half that runs beside a CDR. It owns its policy, its read client,
its project key and its cohort, and the coordinator supplies none of them —
that is what makes the policy a policy rather than a suggestion.

What the coordinator DOES supply is the question: a spec, signed by the
coordinator, plus the project the run belongs to. Everything else is local.

**The project key is loaded here, from this node's own environment.** It never
travels. Every node in a project must be given the same key by the operator,
because a pseudonym computed under a different key is a different patient as
far as the merge is concerned, and the failure is silent — the counts simply
come out too high. `ProjectKey.from_env` is the only route in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.privacy import ProjectKey
from app.privacy.disclosure import DisclosurePolicy
from app.spec import AnalysisSpec, spec_hash
from app.version import VERSION

from .cohort_source import CohortError, CohortSource, ConfiguredCohortSource
from .policy import NodePolicy, PolicyError
from .reader import ConsentUnavailable, NodeReader, ReadError
from .runner import NodeRefusal, run_spec


class NodeNotConfigured(RuntimeError):
    """This deployment is not set up to act as a node."""


@dataclass
class NodeContext:
    """Everything a node needs, assembled once from its own configuration."""
    policy: NodePolicy
    reader: NodeReader
    ips_base_url: str
    cohort_source: CohortSource
    allow_live: bool = False

    @classmethod
    def from_config(cls, config: Any) -> "NodeContext":
        path = config.get("ANALYSE_NODE_POLICY")
        if not path:
            raise NodeNotConfigured(
                "ANALYSE_NODE_POLICY is not set — a node runs under a policy "
                "file owned by the organisation behind its CDR, and there is "
                "no default")
        policy = NodePolicy.load(path)

        key = config.get("ANALYSE_PDHC_SERVICE_KEY") or ""
        if not key:
            raise NodeNotConfigured(
                "ANALYSE_PDHC_SERVICE_KEY is not set — the node reads its CDR "
                "as a registered service caller")
        ips = config.get("IPS_BASE_URL") or ""
        if not ips:
            # Spärr and consent both live in ips and both fail closed. Without
            # it a node would read nothing at all; saying so at startup beats
            # discovering it as an empty result.
            raise NodeNotConfigured(
                "IPS_BASE_URL is not set — spärr and the consent verdict live "
                "in ips.pdhc and both fail closed, so this node could not "
                "lawfully read anything")

        return cls(
            policy=policy,
            reader=NodeReader(base_url=policy.cdr_base_url, service_key=key),
            ips_base_url=ips,
            cohort_source=ConfiguredCohortSource(config),
            allow_live=bool(config.get("ANALYSE_ALLOW_LIVE_DATA", False)),
        )


def policy_summary(policy: NodePolicy) -> dict[str, Any]:
    """What the coordinator is told about this node's terms.

    Only what the merge needs. The coordinator uses `k_min` to compute the
    strictest contributing policy and can only make the merged result
    stricter, never looser — `DisclosurePolicy.stricter_of` enforces the
    direction, so a node is not trusting the coordinator by sending this.
    """
    return {"k_min": policy.k_min, "may_pool": policy.may_pool,
            "node_id": policy.node_id}


def handle_run(payload: dict[str, Any], ctx: NodeContext, *,
               coordinator_secret: bytes | None = None) -> dict[str, Any]:
    """Run one sealed request. Returns the body to seal back.

    Raises `NodeRefusal` / `PolicyError` for a deliberate refusal, and
    `ConsentUnavailable` / `ReadError` / `CohortError` for a failure to
    establish the facts a lawful read needs. The caller maps those to status
    codes; both must reach the coordinator as a NAMED failure rather than as
    an empty result.
    """
    try:
        spec = AnalysisSpec.model_validate(payload.get("spec") or {})
    except Exception as e:
        raise NodeRefusal(f"the spec in this request does not validate: {e}") from e

    # The spec signature is the coordinator's approval of this question, and
    # it is REQUIRED. It is a separate claim from the envelope signature: the
    # envelope says "the other half of this deployment sent these bytes", this
    # says "the coordinator approved this analysis", over the spec's canonical
    # form rather than over the transmitted octets.
    #
    # Making it optional would be worse than not having it: a node that ran
    # unsigned specs whenever the signature was absent would be a node an
    # attacker could use by simply omitting the field.
    if coordinator_secret:
        from app.coordinator.signing import verify as verify_spec
        verify_spec(spec, payload.get("spec_signature") or "",
                    coordinator_secret)

    project_id = (payload.get("project_id") or "").strip()
    if not project_id:
        raise NodeRefusal("request carries no project_id — the pseudonym key "
                          "is per project and cannot be chosen by default")
    project_key = ProjectKey.from_env(project_id)

    requested = payload.get("k_min")
    requested_disclosure = (DisclosurePolicy(k_min=int(requested))
                            if requested is not None else None)

    cohort = ctx.cohort_source.resolve(spec)

    run = run_spec(
        spec, ctx.policy, ctx.reader,
        project_key=project_key,
        ips_base_url=ctx.ips_base_url,
        cohort=cohort,
        allow_live=ctx.allow_live,
        requested_disclosure=requested_disclosure,
        research_projects=tuple(payload.get("research_projects") or ()),
    )

    body = run.to_json()
    body["version"] = VERSION
    body["policy"] = policy_summary(ctx.policy)
    # Echoed so the coordinator can prove the node answered the question it
    # was asked, and not a different one it had cached.
    body["spec_hash"] = spec_hash(spec)
    return body


__all__ = ["NodeContext", "NodeNotConfigured", "handle_run", "policy_summary",
           "CohortError", "NodeRefusal", "PolicyError", "ConsentUnavailable",
           "ReadError"]
