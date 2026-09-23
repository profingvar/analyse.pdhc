"""#647 — engine part 1: describe, histogram, frequency, correlation.

The contract under test is that splitting data across nodes and merging the
partials gives the SAME answer as pooling the raw data would have, for every
method claimed exact. Where that is not true — anything needing ranks — the
result must say so rather than look identical.
"""
from __future__ import annotations

import math
import random

import pytest
from scipy import stats as sps

from app.engine import APPROXIMATE, EXACT, correlation, describe, frequency, histogram
from app.engine.sketch import TDigest
from app.privacy import DisclosurePolicy

P = DisclosurePolicy()
TOL = 1e-9


def _split(values, n_nodes, rnd):
    buckets = [[] for _ in range(n_nodes)]
    for v in values:
        buckets[rnd.randrange(n_nodes)].append(v)
    return buckets


class TestDescribeIsExactAcrossNodes:

    @pytest.mark.parametrize("n_nodes", [1, 2, 3, 5])
    def test_mean_and_sd_equal_the_pooled_values(self, n_nodes):
        rnd = random.Random(n_nodes)
        vals = [rnd.gauss(12, 3) for _ in range(600)]
        parts = [describe.local(b, source=f"c{i}")
                 for i, b in enumerate(_split(vals, n_nodes, rnd))]
        got = describe.finalize(describe.merge(parts), P).pooled

        assert got["mean"] == pytest.approx(sum(vals) / len(vals), rel=TOL)
        assert got["sd"] == pytest.approx(
            float(sps.tstd(vals)), rel=1e-9)

    def test_confidence_interval_matches_scipy(self):
        rnd = random.Random(7)
        vals = [rnd.gauss(0, 1) for _ in range(400)]
        parts = [describe.local(b) for b in _split(vals, 3, rnd)]
        got = describe.finalize(describe.merge(parts), P).pooled
        lo, hi = sps.t.interval(0.95, len(vals) - 1,
                                loc=sum(vals) / len(vals),
                                scale=sps.sem(vals))
        assert got["ci95"][0] == pytest.approx(lo, rel=1e-9)
        assert got["ci95"][1] == pytest.approx(hi, rel=1e-9)

    def test_missing_is_carried_not_dropped(self):
        """A mean over 40 of 200 patients and a mean over 200 of 200 are
        different claims."""
        r = describe.finalize(describe.merge([
            describe.local([1.0, 2.0, None, 3.0, None, 4.0, 5.0]),
        ]), P)
        assert r.pooled["missing"] == 2
        assert any("no recorded value" in n for n in r.notes)

    def test_median_is_flagged_approximate_across_nodes(self):
        rnd = random.Random(3)
        vals = [rnd.gauss(5, 1) for _ in range(500)]
        parts = [describe.local(b) for b in _split(vals, 3, rnd)]
        r = describe.finalize(describe.merge(parts), P)
        assert r.exactness == APPROXIMATE
        assert r.pooled["median"] == pytest.approx(
            sorted(vals)[len(vals) // 2], abs=0.3)

    def test_single_node_is_exact(self):
        r = describe.finalize(describe.merge(
            [describe.local([float(i) for i in range(50)])]), P)
        assert r.exactness == EXACT

    def test_small_n_is_suppressed(self):
        r = describe.finalize(describe.merge(
            [describe.local([1.0, 2.0, 3.0])]), P)
        assert r.pooled["suppressed"] is True
        assert "mean" not in r.pooled or r.pooled["mean"] == "<k"

    def test_categorical_counts_merge_exactly(self):
        parts = [describe.local(["a"] * 4 + ["b"] * 6, categorical=True),
                 describe.local(["a"] * 3 + ["b"] * 2, categorical=True)]
        r = describe.finalize(describe.merge(parts), P)
        assert r.exactness == EXACT
        assert r.pooled["counts"]["a"] == 7      # 4 + 3, above k_min
        assert r.pooled["counts"]["b"] == 8

    def test_categorical_levels_below_k_are_still_suppressed(self):
        parts = [describe.local(["a"] * 20 + ["rare"], categorical=True)]
        r = describe.finalize(describe.merge(parts), P)
        assert r.pooled["counts"]["rare"] == "<k"


class TestHistogram:

    def test_counts_merge_exactly(self):
        rnd = random.Random(11)
        vals = [rnd.uniform(0, 10) for _ in range(400)]
        edges = histogram.edges_from(0, 10, 1)
        parts = [histogram.local(b, edges) for b in _split(vals, 3, rnd)]
        merged = histogram.merge(parts)
        assert sum(merged.data["counts"]) == len(vals)

    def test_nodes_must_share_bin_edges(self):
        """If nodes chose their own bins the counts would not be addable and
        the merged histogram would be a picture of nothing."""
        a = histogram.local([1.0], histogram.edges_from(0, 10, 1))
        b = histogram.local([1.0], histogram.edges_from(0, 10, 2))
        with pytest.raises(ValueError, match="different bin edges"):
            histogram.merge([a, b])

    def test_proposed_edges_ignore_the_extremes(self):
        """A range stretched to an outlier gives a histogram of empty bins
        and one spike — and the extremes are single patients."""
        d = TDigest()
        for v in [5.0] * 500:
            d.add(v)
        d.add(9999.0)
        edges = histogram.propose_edges([d], width=1.0)
        assert edges[-1] < 100

    def test_values_outside_the_range_are_counted_not_lost(self):
        edges = histogram.edges_from(0, 10, 1)
        r = histogram.finalize(histogram.merge(
            [histogram.local([-5.0, 50.0] + [5.0] * 20, edges)]), P)
        assert r.pooled["outside_range"] == 2


class TestFrequency:

    def test_cross_tab_merges_exactly(self):
        a = frequency.local(["m", "f", "m"], cross=["y", "n", "y"])
        b = frequency.local(["f", "f"], cross=["y", "n"])
        merged = frequency.merge([a, b])
        assert merged.data["cells"]["f"]["n"] == 2

    def test_chi_square_matches_scipy(self):
        grid = [[30, 20], [15, 35]]
        a = frequency.local(
            ["r0"] * 50 + ["r1"] * 50,
            cross=["c0"] * 30 + ["c1"] * 20 + ["c0"] * 15 + ["c1"] * 35)
        r = frequency.finalize(frequency.merge([a]), P)
        expect = sps.chi2_contingency(grid, correction=False)
        assert r.pooled["chi_square"]["statistic"] == pytest.approx(
            expect.statistic, rel=1e-9)

    def test_small_cells_are_suppressed_with_complements(self):
        a = frequency.local(["r0"] * 12 + ["r1"] * 10,
                            cross=["c0"] * 10 + ["c1"] * 2 + ["c0"] * 7 + ["c1"] * 3)
        r = frequency.finalize(frequency.merge([a]), P)
        flat = [c for row in r.pooled["cells"] for c in row]
        assert "<k" in flat
        assert any("worked out from the totals" in n for n in r.notes)

    def test_it_does_not_claim_an_effect(self):
        a = frequency.local(["r0"] * 30, cross=["c0"] * 15 + ["c1"] * 15)
        r = frequency.finalize(frequency.merge([a]), P)
        joined = " ".join(r.notes).lower()
        assert "effect" not in joined and "causes" not in joined


class TestCorrelation:

    @pytest.mark.parametrize("n_nodes", [1, 2, 4])
    def test_pearson_equals_the_pooled_value(self, n_nodes):
        rnd = random.Random(n_nodes * 13)
        xs = [rnd.gauss(0, 1) for _ in range(500)]
        ys = [x * 0.6 + rnd.gauss(0, 0.8) for x in xs]

        buckets = [[] for _ in range(n_nodes)]
        for pair in zip(xs, ys):
            buckets[rnd.randrange(n_nodes)].append(pair)
        parts = [correlation.local({"x": [p[0] for p in b],
                                    "y": [p[1] for p in b]}, source=f"c{i}")
                 for i, b in enumerate(buckets)]
        r = correlation.finalize(correlation.merge(parts), P)
        assert r.pooled["x|y"]["r"] == pytest.approx(
            float(sps.pearsonr(xs, ys).statistic), rel=1e-9)
        assert r.exactness == EXACT

    def test_pairwise_complete_keeps_partial_rows(self):
        r = correlation.finalize(correlation.merge([correlation.local({
            "x": [1.0, 2.0, 3.0, None] + [float(i) for i in range(10)],
            "y": [1.0, 2.0, None, 4.0] + [float(i) for i in range(10)],
        })]), P)
        assert r.pooled["x|y"]["n"] == 12

    def test_spearman_across_nodes_is_flagged_approximate(self):
        """Ranks depend on the whole distribution, so a node can only rank
        within itself. A reader cannot tell by looking, so the result says."""
        rnd = random.Random(5)
        xs = [rnd.gauss(0, 1) for _ in range(300)]
        ys = [x * 0.5 + rnd.gauss(0, 1) for x in xs]
        half = len(xs) // 2
        parts = [
            correlation.local({"x": xs[:half], "y": ys[:half]},
                              source="a", method="spearman"),
            correlation.local({"x": xs[half:], "y": ys[half:]},
                              source="b", method="spearman"),
        ]
        r = correlation.finalize(correlation.merge(parts), P)
        assert r.exactness == APPROXIMATE
        assert any("not the true pooled value" in n for n in r.notes)

    def test_spearman_on_one_node_is_exact(self):
        rnd = random.Random(6)
        xs = [rnd.gauss(0, 1) for _ in range(60)]
        ys = [x + rnd.gauss(0, 0.5) for x in xs]
        r = correlation.finalize(correlation.merge(
            [correlation.local({"x": xs, "y": ys}, method="spearman")]), P)
        assert r.exactness == EXACT
        assert r.pooled["x|y"]["r"] == pytest.approx(
            float(sps.spearmanr(xs, ys).statistic), rel=1e-9)

    def test_below_the_threshold_is_suppressed(self):
        r = correlation.finalize(correlation.merge([correlation.local(
            {"x": [1.0, 2.0, 3.0], "y": [1.0, 2.0, 3.0]})]), P)
        assert r.pooled["x|y"]["suppressed"] is True

    def test_it_says_correlation_is_not_causation(self):
        r = correlation.finalize(correlation.merge([correlation.local(
            {"x": [float(i) for i in range(20)],
             "y": [float(i) for i in range(20)]})]), P)
        assert any("does not show that one causes" in n for n in r.notes)


class TestPartialsCrossTheWire:

    def test_a_partial_serialises_and_survives_the_round_trip(self):
        from app.engine.base import Partial
        p = describe.local([1.0, 2.0, 3.0], source="cdr1")
        again = Partial.from_json(p.to_json())
        assert again.data["sum"] == p.data["sum"]
        assert again.source == "cdr1"

    def test_a_partial_carries_no_individual_values(self):
        """The property the architecture rests on: what crosses the wire is
        enough to compute the answer and not enough to rebuild a patient.

        Regression guard. The first version of this shipped t-digest
        centroids of weight 1 — each one a patient's exact value — because
        t-digest keeps tail centroids small for accuracy. The patients at the
        extremes were precisely the ones exposed."""
        vals = [111.0, 222.0, 333.0] + [float(i) for i in range(200)]
        blob = repr(describe.local(vals, k_min=5).to_json())
        for sentinel in ("111.0", "222.0", "333.0"):
            assert blob.count(sentinel) == 0

    def test_no_centroid_describes_fewer_patients_than_k_min(self):
        from app.engine.sketch import TDigest
        import random as _r
        rnd = _r.Random(2)
        d = TDigest()
        for _ in range(400):
            d.add(rnd.gauss(0, 1))
        d.add(9999.0)                     # a lone outlier
        d.compress()
        assert any(w < 5 for _, w in d.centroids), "precondition: tails are small"
        for _, w in d.protect(5).centroids:
            assert w >= 5
