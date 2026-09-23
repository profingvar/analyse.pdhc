"""#651 — the coordinator: signing, merge orchestration, degradation."""
from __future__ import annotations

import pytest

from app.coordinator import SignatureError, combine, sign, verify
from app.engine import describe
from app.privacy.disclosure import DisclosurePolicy
from app.spec import AnalysisSpec

SECRET = b"s" * 32
OTHER = b"t" * 32


def _spec(**over):
    base = {
        "title": "t", "purpose": "statistics", "sources": ["cdr1", "cdr2"],
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 1}]},
        "variables": [{"name": "v", "from": "x", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


def _run(node_id, values, may_pool=True):
    return {"node_id": node_id, "may_pool": may_pool,
            "n_patients": len(values), "version": "1.0",
            "partials": [describe.local(values, source=node_id).to_json()]}


class TestSigning:

    def test_a_valid_signature_verifies(self):
        s = _spec()
        verify(s, sign(s, SECRET), SECRET)

    def test_a_different_secret_does_not(self):
        s = _spec()
        with pytest.raises(SignatureError):
            verify(s, sign(s, OTHER), SECRET)

    def test_a_modified_spec_does_not(self):
        """The signature is over the CANONICAL form, so it commits to the
        spec's meaning."""
        sig = sign(_spec(), SECRET)
        with pytest.raises(SignatureError):
            verify(_spec(title="something else"), sig, SECRET)

    def test_key_order_does_not_break_a_signature(self):
        """A node that re-serialises before verifying must still get the same
        bytes, or verification would depend on transport details."""
        a = _spec()
        b = AnalysisSpec.model_validate(
            dict(reversed(list(a.model_dump(mode="json", by_alias=True).items()))))
        verify(b, sign(a, SECRET), SECRET)

    def test_a_missing_signature_is_refused(self):
        with pytest.raises(SignatureError):
            verify(_spec(), "", SECRET)

    def test_a_weak_secret_is_refused(self):
        with pytest.raises(SignatureError, match="at least 32 bytes"):
            sign(_spec(), b"short")


class TestMerge:

    def test_pooled_and_per_source_are_both_returned(self):
        vals_a = [float(i) for i in range(20)]
        vals_b = [float(i) for i in range(20, 50)]
        out = combine(_spec(), [_run("cdr1", vals_a), _run("cdr2", vals_b)],
                      coordinator_version="1.0")
        res = out.results[0]
        pooled_mean = sum(vals_a + vals_b) / len(vals_a + vals_b)
        assert res["pooled"]["mean"] == pytest.approx(pooled_mean, rel=1e-9)
        assert set(res["by_source"]) == {"cdr1", "cdr2"}

    def test_an_offline_source_degrades_rather_than_failing_the_run(self):
        """Losing Uppsala must not mean losing the Östergötland figures."""
        out = combine(_spec(), [_run("cdr1", [float(i) for i in range(30)])],
                      coordinator_version="1.0",
                      failures={"cdr2": "connection refused"})
        assert out.results, "the surviving source still produced a result"
        statuses = {s.source: s.ok for s in out.sources}
        assert statuses == {"cdr1": True, "cdr2": False}
        assert any("did not answer" in n for n in out.notes)

    def test_a_no_pool_node_is_excluded_from_the_total(self):
        """Its organisation permitted a figure attributable to them, not a
        contribution to someone else's."""
        out = combine(_spec(),
                      [_run("cdr1", [float(i) for i in range(30)]),
                       _run("cdr2", [999.0] * 30, may_pool=False)],
                      coordinator_version="1.0")
        # the outlying source must not drag the pooled mean
        assert out.results[0]["pooled"]["mean"] < 100
        assert any("does not permit pooling" in n for n in out.notes)

    def test_the_strictest_contributing_policy_governs_the_merge(self):
        """A merge of individually-safe partials can be unsafe, and no single
        node could have seen that."""
        out = combine(_spec(),
                      [_run("cdr1", [float(i) for i in range(30)]),
                       _run("cdr2", [float(i) for i in range(30)])],
                      coordinator_version="1.0",
                      node_policies={"cdr1": DisclosurePolicy(k_min=5),
                                     "cdr2": DisclosurePolicy(k_min=25,
                                                              floor=25)})
        assert out.provenance["k_min_applied"] == 25

    def test_provenance_carries_the_spec_hash_and_snapshots(self):
        out = combine(_spec(), [_run("cdr1", [float(i) for i in range(30)])],
                      coordinator_version="2.1",
                      snapshots={"cdr1": "2026-09-23T10:00:00Z"})
        assert out.provenance["spec_hash"].startswith("sha256:")
        assert out.provenance["coordinator_version"] == "2.1"
        assert out.provenance["snapshots"]["cdr1"].startswith("2026")

    def test_every_source_is_accounted_for_in_the_result(self):
        """A reader must never see a pooled number without knowing what is
        in it."""
        out = combine(_spec(), [_run("cdr1", [float(i) for i in range(30)])],
                      coordinator_version="1.0",
                      failures={"cdr2": "timeout"})
        assert len(out.sources) == 2

    def test_the_result_carries_no_identifiers(self):
        from app.testing import assert_clean
        out = combine(_spec(), [_run("cdr1", [float(i) for i in range(40)]),
                                _run("cdr2", [float(i) for i in range(40)])],
                      coordinator_version="1.0")
        assert_clean(out.to_json(), where="coordinator result")
