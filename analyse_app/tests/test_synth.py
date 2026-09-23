"""#653 — the synthetic multi-source environment.

This is the first test that runs the WHOLE loop: real node code (policy,
spärr, read, pseudonymise, project, coarsen, compute, suppress) on several
sources, merged by the real coordinator. Only the transport is substituted.
"""
from __future__ import annotations

import pytest

from app.spec import AnalysisSpec
from app.testing import assert_clean, synth


def _spec(sources, **over):
    base = {
        "title": "synthetic run", "purpose": "statistics",
        "sources": list(sources),
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 1}]},
        "variables": [{"name": "v", "from": "x", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


class TestTheWholeLoop:

    @pytest.mark.parametrize("nodes", [1, 2, 3, 5])
    def test_it_runs_end_to_end_for_any_number_of_sources(self, nodes):
        """A single CDR is a federation with one node — one code path."""
        srcs = synth.build(nodes=nodes, patients=300, seed=nodes)
        out = synth.run(_spec([s.node_id for s in srcs]), srcs)
        assert len(out.sources) == nodes
        assert all(s.ok for s in out.sources)
        assert out.results and out.results[0]["pooled"]["n"] > 0

    def test_blocked_patients_never_reach_the_result(self):
        srcs = synth.build(nodes=2, patients=400, seed=7,
                           blocked_fraction=0.25)
        total_ids = sum(len({r["patient_guid"] for r in s.rows}) for s in srcs)
        out = synth.run(_spec([s.node_id for s in srcs]), srcs)
        counted = sum(s.n_patients for s in out.sources)
        assert counted < total_ids, "some patients must have been excluded"

    def test_the_result_carries_no_identifier(self):
        srcs = synth.build(nodes=3, patients=300, seed=3)
        out = synth.run(_spec([s.node_id for s in srcs]), srcs)
        assert_clean(out.to_json(), where="synthetic run result")

    def test_synthetic_patient_ids_are_deliberately_unmistakable(self):
        """TEST- ids are flagged by the scanner on sight, so synthetic data
        reaching a real output is caught rather than blending in."""
        from app.testing import scan_text
        srcs = synth.build(nodes=1, patients=10, seed=1)
        assert scan_text(srcs[0].rows[0]["patient_guid"])

    def test_no_names_addresses_or_personnummer_are_generated(self):
        """Brief §0. The generator must not be the thing that puts a
        realistic identifier into a fixture."""
        from app.testing import scan_text
        srcs = synth.build(nodes=2, patients=60, seed=2)
        for s in srcs:
            for row in s.rows[:200]:
                for key in ("name", "personnummer", "address", "phone"):
                    assert key not in row
                findings = scan_text(str(row.get("demographics")))
                assert findings == []

    def test_a_no_pool_source_is_kept_out_of_the_total(self):
        srcs = synth.build(nodes=2, patients=300, seed=4)
        srcs[1].policy = synth._policy(srcs[1].node_id, may_pool=False)
        out = synth.run(_spec([s.node_id for s in srcs]), srcs)
        assert any("does not permit pooling" in n for n in out.notes)

    def test_a_source_that_refuses_the_purpose_degrades_the_run(self):
        srcs = synth.build(nodes=3, patients=300, seed=5)
        # this organisation permits nothing
        from app.node import NodePolicy
        srcs[2].policy = NodePolicy.load(
            f"node_id: {srcs[2].node_id}\ncdr_base_url: u\n", is_text=True)
        out = synth.run(_spec([s.node_id for s in srcs]), srcs)
        statuses = {s.source: s.ok for s in out.sources}
        assert statuses[srcs[2].node_id] is False
        assert out.results, "the other two sources still produced a result"

    def test_the_same_seed_gives_the_same_data(self):
        a = synth.build(nodes=2, patients=100, seed=11)
        b = synth.build(nodes=2, patients=100, seed=11)
        assert a[0].rows[:5] == b[0].rows[:5]


class TestFederatedEqualsPooledOnSyntheticData:

    def test_the_pooled_mean_matches_a_single_source_holding_everything(self):
        """The property AN-7 proves on raw lists, re-proved through the whole
        node and coordinator path rather than on the engine alone."""
        srcs = synth.build(nodes=4, patients=800, seed=21, blocked_fraction=0)
        spread = synth.run(_spec([s.node_id for s in srcs]), srcs)

        merged_rows = [r for s in srcs for r in s.rows]
        one = synth.build(nodes=1, patients=1, seed=99)[0]
        one.rows = merged_rows
        one.blocked = set()
        single = synth.run(_spec([one.node_id]), [one])

        assert spread.results[0]["pooled"]["mean"] == pytest.approx(
            single.results[0]["pooled"]["mean"], rel=1e-9)
