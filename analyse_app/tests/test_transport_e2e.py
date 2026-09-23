"""#684 (AN-12) — the wire, end to end over real HTTP.

The acceptance question for this ticket: **do two processes on different
ports produce the same answer the in-process harness produces?** If they do
not, the transport is changing the result, which is the one thing it must
never do.

Everything here goes over a real socket: real werkzeug servers on real ports,
real `requests` calls, real sealing and verification. Only the CDR behind each
node is synthetic — standing up containerised CDRs is gap G6 and a different
ticket.
"""
from __future__ import annotations

import pytest

from app.coordinator import NoSourcesAnswered, run_distributed
from app.routes.node_api import EXTENSION_KEY
from app.spec import AnalysisSpec
from app.testing import synth
from app.transport.client import NodeEndpoint
from tests.nodeserver import PROJECT, PROJECT_KEY, SECRET, Node as _Node, narrowed as _narrowed


def _spec(**over):
    base = {
        "title": "PEF across sources", "purpose": "statistics",
        "sources": ["cdr1", "cdr2"],
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 0}]},
        "variables": [{"name": "value", "from": "value", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["value"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


@pytest.fixture()
def project_key(monkeypatch):
    """The node loads the project key from ITS OWN environment; the key never
    travels. Every node in a project must be given the same one."""
    monkeypatch.setenv(f"ANALYSE_PROJECT_KEY_{PROJECT.upper()}", PROJECT_KEY)


@pytest.fixture()
def sources():
    return synth.build(nodes=2, patients=400, seed=7)


@pytest.fixture()
def nodes(sources):
    started = [_Node(s) for s in sources]
    yield started
    for n in started:
        n.close()


class TestParityWithInProcess:

    def test_over_the_wire_matches_in_process(self, nodes, sources, project_key):
        """The whole point of the ticket. Same spec, same data, two
        transports — the figures must be identical, not merely close."""
        spec = _spec()
        wire = run_distributed(spec, [n.endpoint for n in nodes],
                               SECRET.encode(), project_id=PROJECT)
        local = synth.run(spec, sources)

        assert [s.source for s in sorted(wire.sources, key=lambda s: s.source)] \
            == [s.source for s in sorted(local.sources, key=lambda s: s.source)]
        assert all(s.ok for s in wire.sources)
        assert wire.results == local.results

    def test_every_source_is_accounted_for(self, nodes, project_key):
        wire = run_distributed(_spec(), [n.endpoint for n in nodes],
                               SECRET.encode(), project_id=PROJECT)
        assert {s.source for s in wire.sources} == {"cdr1", "cdr2"}
        assert sum(s.n_patients for s in wire.sources) > 0

    def test_no_patient_identifier_crosses_the_wire(self, nodes, project_key):
        """The coordinator must never hold identifiers. The synthetic ids are
        TEST-P… precisely so the AN-7 scanner flags them on sight."""
        from app.testing import scan_object
        wire = run_distributed(_spec(), [n.endpoint for n in nodes],
                               SECRET.encode(), project_id=PROJECT)
        assert scan_object(wire.to_json(), where="coordinator result") == []


class TestDegradation:

    def test_an_unreachable_node_degrades_that_source_only(self, nodes,
                                                           project_key):
        """Losing one source must not lose the others' figures."""
        dead = NodeEndpoint("cdr9", "http://127.0.0.1:1")   # nothing listens
        wire = run_distributed(_spec(), [nodes[0].endpoint, dead],
                               SECRET.encode(), project_id=PROJECT,
                               timeout=2.0)
        by_src = {s.source: s for s in wire.sources}
        assert by_src["cdr1"].ok is True
        assert by_src["cdr9"].ok is False
        assert "could not be reached" in (by_src["cdr9"].reason or "")
        assert wire.results, "the reachable source's figures must survive"

    def test_the_result_says_a_source_was_missing(self, nodes, project_key):
        dead = NodeEndpoint("cdr9", "http://127.0.0.1:1")
        wire = run_distributed(_spec(), [nodes[0].endpoint, dead],
                               SECRET.encode(), project_id=PROJECT,
                               timeout=2.0)
        assert any("did not answer" in n for n in wire.notes), \
            "a pooled figure over fewer sources must say so"

    def test_all_nodes_failing_is_an_error_not_an_empty_result(self,
                                                              project_key):
        """An empty result renders as suppressed cells, which reads as a tiny
        cohort rather than as nothing having run."""
        dead = [NodeEndpoint("cdr8", "http://127.0.0.1:1"),
                NodeEndpoint("cdr9", "http://127.0.0.1:1")]
        with pytest.raises(NoSourcesAnswered) as e:
            run_distributed(_spec(), dead, SECRET.encode(),
                            project_id=PROJECT, timeout=2.0)
        assert "cdr8" in str(e.value) and "cdr9" in str(e.value)


class TestTheNodeRefuses:

    def test_a_wrong_transport_secret_is_rejected(self, nodes, project_key):
        """And the reason names the setting to check, because "401" on its own
        sends an operator looking at the wrong thing."""
        with pytest.raises(NoSourcesAnswered) as e:
            run_distributed(_spec(), [n.endpoint for n in nodes],
                            ("x" * 40).encode(), project_id=PROJECT,
                            timeout=5.0)
        assert "ANALYSE_TRANSPORT_SECRET" in str(e.value)

    def test_a_purpose_the_policy_forbids_is_refused_at_the_node(
            self, sources, project_key):
        """The refusal happens where the policy lives. A coordinator that
        filtered the request beforehand would be deciding on the
        organisation's behalf, and its decision would be the one that could be
        changed."""
        narrow = synth.build(nodes=1, patients=50, seed=3)
        strict = _Node(_narrowed(narrow[0], purposes="[statistics]"))
        try:
            with pytest.raises(NoSourcesAnswered) as e:
                run_distributed(_spec(purpose="research"),
                                [strict.endpoint], SECRET.encode(),
                                project_id=PROJECT, timeout=5.0)
            assert "refused" in str(e.value)
            assert "research" in str(e.value)
        finally:
            strict.close()

    def test_a_node_that_cannot_resolve_its_cohort_says_so(self, sources,
                                                           project_key):
        """Not an empty answer. An unresolvable cohort and an empty one both
        contribute nothing, but they mean opposite things."""
        from app.node.cohort_source import ConfiguredCohortSource
        src = sources[0]
        node = _Node(src)
        node.app.extensions[EXTENSION_KEY].cohort_source = \
            ConfiguredCohortSource({})
        try:
            with pytest.raises(NoSourcesAnswered) as e:
                run_distributed(_spec(), [node.endpoint], SECRET.encode(),
                                project_id=PROJECT, timeout=5.0)
            assert "cohort" in str(e.value).lower()
        finally:
            node.close()
