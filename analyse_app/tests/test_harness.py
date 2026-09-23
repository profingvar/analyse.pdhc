"""#650 — the test harness: property tests vs SciPy, and the identifier gate.

Two jobs. Prove that federated equals pooled for every method claimed exact,
across data split at random over 1–5 nodes. And fail the build if anything
that looks like a person reaches an output.
"""
from __future__ import annotations

import random

import pytest
from scipy import stats as sps

from app.engine import correlation, describe, frequency, histogram
from app.privacy import DisclosurePolicy, ProjectKey
from app.testing import assert_clean, scan_object, scan_text

P = DisclosurePolicy()
TOL = 1e-9


def _split(values, n_nodes, rnd):
    buckets = [[] for _ in range(n_nodes)]
    for v in values:
        buckets[rnd.randrange(n_nodes)].append(v)
    return buckets


# ── federated == pooled, the property the architecture claims ─────────

class TestFederatedEqualsPooled:

    @pytest.mark.parametrize("n_nodes", [1, 2, 3, 4, 5])
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_describe(self, n_nodes, seed):
        rnd = random.Random(seed * 100 + n_nodes)
        vals = [rnd.gauss(rnd.uniform(-50, 50), rnd.uniform(0.5, 20))
                for _ in range(rnd.randint(200, 900))]
        parts = [describe.local(b, source=f"c{i}")
                 for i, b in enumerate(_split(vals, n_nodes, rnd)) if b]
        got = describe.finalize(describe.merge(parts), P).pooled
        assert got["mean"] == pytest.approx(sum(vals) / len(vals), rel=TOL)
        assert got["sd"] == pytest.approx(float(sps.tstd(vals)), rel=TOL)

    @pytest.mark.parametrize("n_nodes", [1, 2, 3, 4, 5])
    def test_pearson(self, n_nodes):
        rnd = random.Random(n_nodes * 31)
        xs = [rnd.gauss(0, 1) for _ in range(600)]
        ys = [x * rnd.uniform(-1, 1) + rnd.gauss(0, 1) for x in xs]
        buckets = [[] for _ in range(n_nodes)]
        for pair in zip(xs, ys):
            buckets[rnd.randrange(n_nodes)].append(pair)
        parts = [correlation.local({"x": [p[0] for p in b],
                                    "y": [p[1] for p in b]}, source=f"c{i}")
                 for i, b in enumerate(buckets) if b]
        got = correlation.finalize(correlation.merge(parts), P).pooled
        assert got["x|y"]["r"] == pytest.approx(
            float(sps.pearsonr(xs, ys).statistic), rel=TOL)

    @pytest.mark.parametrize("n_nodes", [1, 2, 3, 4, 5])
    def test_histogram_counts(self, n_nodes):
        rnd = random.Random(n_nodes * 17)
        vals = [rnd.uniform(0, 20) for _ in range(700)]
        edges = histogram.edges_from(0, 20, 2)
        parts = [histogram.local(b, edges) for b in _split(vals, n_nodes, rnd) if b]
        merged = histogram.merge(parts)
        pooled = histogram.local(vals, edges)
        assert merged.data["counts"] == pooled.data["counts"]

    @pytest.mark.parametrize("n_nodes", [1, 2, 3, 4, 5])
    def test_frequency_counts(self, n_nodes):
        rnd = random.Random(n_nodes * 7)
        vals = [rnd.choice("abcde") for _ in range(500)]
        parts = [frequency.local(b) for b in _split(vals, n_nodes, rnd) if b]
        merged = frequency.merge(parts)
        pooled = frequency.local(vals)
        assert merged.data["counts"] == pooled.data["counts"]


class TestApproximateMethodsStayInsideTheirBound:

    @pytest.mark.parametrize("seed", range(6))
    def test_median_error_is_documented_and_small(self, seed):
        """Approximate is not the same as unbounded. The sketch's median is
        asserted within 2% of the true one, so a regression that quietly
        degrades it is caught rather than excused by the flag."""
        rnd = random.Random(seed)
        vals = [rnd.gauss(100, 15) for _ in range(3000)]
        parts = [describe.local(b) for b in _split(vals, 4, rnd)]
        got = describe.finalize(describe.merge(parts), P).pooled
        true_median = sorted(vals)[len(vals) // 2]
        assert abs(got["median"] - true_median) / abs(true_median) < 0.02


# ── the gate ──────────────────────────────────────────────────────────

class TestIdentifierScanner:

    def test_it_catches_a_guid(self):
        assert scan_text("id 6521528c-db59-45c5-a492-003c28f27623 here")

    def test_it_catches_a_personnummer_shape(self):
        """Matched on SHAPE, not checksum: a near-miss in an output is still
        someone trying to put one there."""
        assert scan_text("19850101-1234")
        assert scan_text("8501011234")

    def test_it_catches_a_fixture_sentinel(self):
        assert scan_text("value was FORBIDDEN-PNR")

    def test_it_catches_key_material(self):
        assert scan_text("ANALYSE_PROJECT_KEY_PROJ_A=zzz")

    def test_it_does_not_flag_a_pseudonym(self):
        """Pseudonyms must pass, or the gate would block every real output."""
        from app.privacy import pseudonymise
        pid = pseudonymise("pat-1", ProjectKey("p", b"k" * 32))
        assert scan_text(pid) == []

    def test_it_scans_dictionary_keys_too(self):
        """A dict keyed by patient guid discloses exactly as much as one that
        stores it in a value."""
        found = scan_object({"6521528c-db59-45c5-a492-003c28f27623": 3})
        assert found and found[0].pattern == "guid"

    def test_it_walks_nested_structures(self):
        found = scan_object({"a": [{"b": ["FORBIDDEN-NAME"]}]})
        assert found and "[0]" in found[0].where

    def test_the_gate_lists_every_finding_not_just_the_first(self):
        """A leak is rarely alone; stopping at the first would hide the rest."""
        with pytest.raises(AssertionError) as e:
            assert_clean({"a": "FORBIDDEN-NAME", "b": "FORBIDDEN-PNR"})
        assert "2 identifier-like" in str(e.value)


class TestRealOutputsAreClean:
    """The gate applied to what the engine actually produces."""

    def test_a_describe_result_is_clean(self):
        rnd = random.Random(1)
        vals = [rnd.gauss(10, 2) for _ in range(200)]
        r = describe.finalize(describe.merge([describe.local(vals)]), P)
        assert_clean(r.to_json(), where="describe result")

    def test_a_partial_from_a_node_is_clean(self):
        rnd = random.Random(2)
        vals = [rnd.gauss(10, 2) for _ in range(200)]
        assert_clean(describe.local(vals).to_json(), where="describe partial")

    def test_a_frequency_result_over_guid_shaped_values_is_caught(self):
        """The gate must fire on real output too, not only on crafted strings
        — otherwise it proves nothing about the pipeline. A frequency over a
        variable whose VALUES are guids (an org column, say) is exactly how a
        guid could reach a result legitimately-looking."""
        guids = ["6521528c-db59-45c5-a492-003c28f27623"] * 20
        r = frequency.finalize(frequency.merge([frequency.local(guids)]), P)
        assert scan_object(r.to_json()), \
            "the scanner must fire on a real result carrying guids"
