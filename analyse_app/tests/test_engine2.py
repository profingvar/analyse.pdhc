"""#652 — engine part 2: compare_groups, over_time, completeness."""
from __future__ import annotations

import random

import pytest
from scipy import stats as sps

from app.engine import compare_groups, completeness, over_time
from app.privacy import DisclosurePolicy
from app.testing import assert_clean

P = DisclosurePolicy()


class TestCompareGroups:

    def _two_groups(self, n_nodes=2, seed=1):
        rnd = random.Random(seed)
        a = [rnd.gauss(10, 2) for _ in range(200)]
        b = [rnd.gauss(12, 2) for _ in range(200)]
        parts = []
        for i in range(n_nodes):
            parts.append(compare_groups.local(
                {"A": a[i::n_nodes], "B": b[i::n_nodes]}, source=f"c{i}"))
        return a, b, parts

    @pytest.mark.parametrize("n_nodes", [1, 2, 3])
    def test_welch_matches_scipy_across_nodes(self, n_nodes):
        a, b, parts = self._two_groups(n_nodes)
        r = compare_groups.finalize(compare_groups.merge(parts), P)
        got = r.pooled["comparisons"]["A vs B"]
        want = sps.ttest_ind(b, a, equal_var=False)
        assert got["t"] == pytest.approx(float(want.statistic), rel=1e-9)
        assert got["p_value"] == pytest.approx(float(want.pvalue), rel=1e-9)

    def test_mean_difference_and_ci_are_exact(self):
        a, b, parts = self._two_groups(3)
        got = compare_groups.finalize(
            compare_groups.merge(parts), P).pooled["comparisons"]["A vs B"]
        assert got["difference"] == pytest.approx(
            sum(b) / len(b) - sum(a) / len(a), rel=1e-9)
        assert got["ci95"][0] < got["difference"] < got["ci95"][1]

    def test_the_standardised_difference_is_reported(self):
        """The Table 1 view in AN-13 shows this to non-experts in preference
        to p-values."""
        _, _, parts = self._two_groups()
        got = compare_groups.finalize(
            compare_groups.merge(parts), P).pooled["comparisons"]["A vs B"]
        assert got["smd"] is not None
        assert abs(got["smd"]) == pytest.approx(1.0, abs=0.5)

    def test_a_small_group_suppresses_the_whole_comparison(self):
        """Releasing the groups that pass would disclose the one that did
        not, by difference."""
        parts = [compare_groups.local(
            {"A": [float(i) for i in range(40)], "B": [1.0, 2.0]})]
        r = compare_groups.finalize(compare_groups.merge(parts), P)
        assert r.pooled["suppressed"] is True
        assert any("too few patients" in n for n in r.notes)

    def test_it_never_claims_an_effect(self):
        _, _, parts = self._two_groups()
        r = compare_groups.finalize(compare_groups.merge(parts), P)
        joined = " ".join(r.notes).lower()
        assert "effect" not in joined
        assert "caused the other" in joined


class TestOverTime:

    def _points(self, n=400, seed=2):
        rnd = random.Random(seed)
        return [(rnd.randrange(0, 90), rnd.gauss(5, 1)) for _ in range(n)]

    @pytest.mark.parametrize("n_nodes", [1, 2, 4])
    def test_bin_means_are_exact_across_nodes(self, n_nodes):
        pts = self._points()
        parts = [over_time.local(pts[i::n_nodes], bin_days=7,
                                 range_days=(0, 90), source=f"c{i}")
                 for i in range(n_nodes)]
        merged = over_time.merge(parts)
        pooled = over_time.local(pts, bin_days=7, range_days=(0, 90))
        assert [b["n"] for b in merged.data["bins"]] == \
               [b["n"] for b in pooled.data["bins"]]
        for m, p in zip(merged.data["bins"], pooled.data["bins"]):
            assert m["sum"] == pytest.approx(p["sum"], rel=1e-12)

    def test_mismatched_bins_are_refused(self):
        a = over_time.local([(1, 1.0)], bin_days=7, range_days=(0, 90))
        b = over_time.local([(1, 1.0)], bin_days=14, range_days=(0, 90))
        with pytest.raises(ValueError, match="different bins"):
            over_time.merge([a, b])

    def test_sparse_periods_are_suppressed_not_dropped(self):
        pts = [(1, 5.0)] * 2 + [(40, 5.0)] * 40
        r = over_time.finalize(over_time.merge(
            [over_time.local(pts, bin_days=7, range_days=(0, 90))]), P)
        labels = [b["label"] for b in r.pooled["bins"]]
        assert len(labels) == len(set(labels))
        assert any(b["n"] == "<k" for b in r.pooled["bins"])
        assert any("time periods are hidden" in n for n in r.notes)

    def test_it_says_time_is_relative_not_calendar(self):
        r = over_time.finalize(over_time.merge(
            [over_time.local(self._points(), bin_days=7, range_days=(0, 90))]), P)
        assert any("not by calendar date" in n for n in r.notes)


class TestCompleteness:

    def _rows(self, n=60):
        return [{"pid": f"pid{i % 20}", "value": (None if i % 5 == 0 else 1.0),
                 "meta.author_org": "org-a" if i % 2 else "org-b"}
                for i in range(n)]

    def test_per_patient_counts_leave_as_a_distribution(self):
        """Sending per-patient numbers would be sending a per-patient
        dataset."""
        p = completeness.local(self._rows(), ["value"])
        assert "obs_per_patient" in p.data
        assert all(k.isdigit() for k in p.data["obs_per_patient"])
        assert "pid0" not in repr(p.to_json())

    def test_missingness_is_reported_as_a_rate(self):
        r = completeness.finalize(completeness.merge(
            [completeness.local(self._rows(), ["value"])]), P)
        assert r.pooled["missingness"]["value"]["percent_missing"] == \
            pytest.approx(20.0)

    def test_rows_by_author_org_uses_the_field_cdr_665_added(self):
        r = completeness.finalize(completeness.merge(
            [completeness.local(self._rows(), ["value"])]), P)
        assert set(r.pooled["rows_by_author_org"]) == {"org-a", "org-b"}

    def test_unrecorded_author_is_explained_not_hidden(self):
        rows = [{"pid": f"p{i}", "value": 1.0} for i in range(30)]
        r = completeness.finalize(completeness.merge(
            [completeness.local(rows, ["value"])]), P)
        assert any("older rows predate it" in n for n in r.notes)

    def test_heavy_missingness_is_called_out(self):
        rows = [{"pid": f"p{i}", "value": None if i % 3 else 1.0}
                for i in range(60)]
        r = completeness.finalize(completeness.merge(
            [completeness.local(rows, ["value"])]), P)
        assert any("describes the patients who happened to be measured" in n
                   for n in r.notes)

    def test_counts_merge_across_nodes(self):
        rows = self._rows(60)
        parts = [completeness.local(rows[:30], ["value"], source="a"),
                 completeness.local(rows[30:], ["value"], source="b")]
        merged = completeness.merge(parts)
        assert merged.data["n_rows"] == 60


class TestOutputsAreClean:

    def test_no_result_carries_an_identifier(self):
        rnd = random.Random(9)
        a = [rnd.gauss(10, 2) for _ in range(60)]
        b = [rnd.gauss(11, 2) for _ in range(60)]
        for r in (
            compare_groups.finalize(compare_groups.merge(
                [compare_groups.local({"A": a, "B": b})]), P),
            over_time.finalize(over_time.merge(
                [over_time.local([(i % 80, 1.0) for i in range(300)],
                                 bin_days=7, range_days=(0, 90))]), P),
            completeness.finalize(completeness.merge(
                [completeness.local(
                    [{"pid": f"p{i}", "value": 1.0} for i in range(40)],
                    ["value"])]), P),
        ):
            assert_clean(r.to_json(), where=r.kind)
