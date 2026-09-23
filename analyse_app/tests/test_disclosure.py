"""#646 — disclosure control.

The acceptance criterion is an ATTACK, not an assertion about output shape:
given a published table with its margins, try to recover a suppressed cell by
subtraction, and fail. ``_recover`` below is that attack, and the property
test runs it against randomly generated tables.
"""
from __future__ import annotations

import random

import pytest

from app.privacy.disclosure import (
    DEFAULT_K_MIN, SUPPRESSED, DifferencingGuard, DisclosureError,
    DisclosurePolicy, merge_small_bins, round_for_public, safe_correlation,
    safe_group_comparison, safe_range, safe_summary, suppress_counts,
    suppress_table,
)

P = DisclosurePolicy()


# ── the attack ────────────────────────────────────────────────────────

def _recover(published, row_totals, col_totals):
    """Attempt to recover suppressed cells by subtraction.

    The standard attack, and the only one secondary suppression exists to
    stop: whenever a line has exactly ONE unknown, that unknown is the total
    minus the rest. Solving one can expose another, so it iterates to
    exhaustion. Returns the cells it managed to recover.
    """
    grid = [list(r) for r in published]
    n_rows, n_cols = len(grid), len(grid[0])
    recovered = {}
    changed = True
    while changed:
        changed = False
        for i in range(n_rows):
            unknown = [j for j in range(n_cols) if grid[i][j] == SUPPRESSED]
            if len(unknown) == 1:
                j = unknown[0]
                grid[i][j] = row_totals[i] - sum(
                    v for k, v in enumerate(grid[i]) if k != j)
                recovered[(i, j)] = grid[i][j]
                changed = True
        for j in range(n_cols):
            unknown = [i for i in range(n_rows) if grid[i][j] == SUPPRESSED]
            if len(unknown) == 1:
                i = unknown[0]
                grid[i][j] = col_totals[j] - sum(
                    grid[k][j] for k in range(n_rows) if k != i)
                recovered[(i, j)] = grid[i][j]
                changed = True
    return recovered


class TestSecondarySuppressionDefeatsRecovery:

    def test_the_attack_works_without_secondary_suppression(self):
        """Sanity check on the attack itself. If this passed, the property
        test below would be proving nothing."""
        table = [[10, 2, 8], [7, 9, 3]]
        naive = [[SUPPRESSED if 0 < v < 5 else v for v in row] for row in table]
        rt = [sum(r) for r in table]
        ct = [sum(r[j] for r in table) for j in range(3)]
        assert _recover(naive, rt, ct), "the attack should succeed on naive suppression"

    def test_secondary_suppression_defeats_it(self):
        table = [[10, 2, 8], [7, 9, 3]]
        out = suppress_table(table, P)
        rt = [sum(r) for r in table]
        ct = [sum(r[j] for r in table) for j in range(3)]
        assert _recover(out.cells, rt, ct) == {}

    @pytest.mark.parametrize("seed", range(40))
    def test_property_no_random_table_leaks(self, seed):
        """40 random tables, each attacked. A single recovery is a real
        disclosure of a cell below the minimum cell size."""
        rnd = random.Random(seed)
        n_rows, n_cols = rnd.randint(2, 5), rnd.randint(2, 5)
        table = [[rnd.randint(0, 30) for _ in range(n_cols)]
                 for _ in range(n_rows)]
        out = suppress_table(table, P)
        rt = [sum(r) for r in table]
        ct = [sum(r[j] for r in table) for j in range(n_cols)]
        leaked = _recover(out.cells, rt, ct)
        assert leaked == {}, f"seed {seed} leaked {leaked}"

    def test_it_does_not_suppress_more_than_it_must(self):
        """Over-suppression is a real cost, not a safe default: a table that
        blanks itself entirely tells the analyst nothing and pushes them
        toward coarser, less useful questions."""
        out = suppress_table([[10, 2, 8], [7, 9, 3]], P)
        assert out.cells[0][0] == 10
        assert out.cells[1][0] == 7
        assert len(out.suppressed) == 4

    def test_a_true_zero_is_not_suppressed(self):
        """Zero discloses nothing about an individual, and hiding it loses
        real information — 'nobody in this group' is often the finding."""
        out = suppress_table([[0, 10], [12, 11]], P, publish_margins=False)
        assert out.cells[0][0] == 0

    def test_margins_not_published_means_primary_only(self):
        out = suppress_table([[10, 2, 8], [7, 9, 3]], P, publish_margins=False)
        assert out.secondary == set()
        assert out.primary == {(0, 1), (1, 2)}


# ── thresholds ────────────────────────────────────────────────────────

class TestPolicy:

    def test_k_min_may_be_raised(self):
        assert DisclosurePolicy(k_min=10).k_min == 10

    def test_k_min_may_never_go_below_the_floor(self):
        """A coordinator must not be able to talk a node into disclosing more
        than the organisation owning the data allows."""
        with pytest.raises(DisclosureError, match="below the configured floor"):
            DisclosurePolicy(k_min=2, floor=DEFAULT_K_MIN)

    def test_merging_policies_takes_the_stricter_of_each(self):
        merged = DisclosurePolicy(k_min=5).stricter_of(DisclosurePolicy(k_min=12, floor=12))
        assert merged.k_min == 12

    def test_merging_narrows_the_percentile_range(self):
        merged = DisclosurePolicy().stricter_of(
            DisclosurePolicy(percentile_range=(10.0, 90.0)))
        assert merged.percentile_range == (10.0, 90.0)


class TestSummaries:

    def test_mean_suppressed_below_k(self):
        """At n=1 the mean IS the patient's value; at n=2 both values are
        recoverable from mean and SD together."""
        assert safe_summary(4, 3.0, 1.0, P)["suppressed"] is True

    def test_mean_released_at_k(self):
        assert safe_summary(5, 3.0, 1.0, P)["mean"] == 3.0

    def test_correlation_needs_more_than_a_mean(self):
        assert safe_correlation(9, 0.8, P)["suppressed"] is True
        assert safe_correlation(10, 0.8, P)["suppressed"] is False

    def test_comparison_blocked_when_any_group_is_small(self):
        """Releasing the groups that pass would disclose the one that did
        not, by difference from a published total."""
        assert safe_group_comparison({"a": 20, "b": 3}, P)["suppressed"] is True
        assert safe_group_comparison({"a": 20, "b": 8}, P)["suppressed"] is False

    def test_true_min_and_max_are_never_returned(self):
        vals = list(range(100))
        lo, hi = safe_range(vals, P)
        assert lo > min(vals) and hi < max(vals)

    def test_range_suppressed_entirely_below_k(self):
        assert safe_range([1.0, 2.0], P) is None


class TestHistogramsAndCounts:

    def test_small_bins_merge_rather_than_disappear(self):
        """A dropped bin changes the shape of the distribution silently; a
        merged one keeps every patient at coarser resolution."""
        out = merge_small_bins([("0-1", 20), ("1-2", 2), ("2-3", 30)], P)
        assert sum(c for _, c in out) == 52

    def test_a_trailing_small_bin_merges_backwards(self):
        out = merge_small_bins([("0-1", 20), ("1-2", 1)], P)
        assert len(out) == 1 and out[0][1] == 21

    def test_counts_below_k_are_hidden(self):
        out = suppress_counts({"a": 20, "b": 2, "c": 0}, P)
        assert out["a"] == 20 and out["b"] == SUPPRESSED and out["c"] == 0

    def test_public_rounding_is_opt_in(self):
        assert round_for_public(23, P) == 23
        assert round_for_public(23, DisclosurePolicy(round_to=5)) == 25


# ── differencing ──────────────────────────────────────────────────────

class TestDifferencingGuard:

    def test_two_cohorts_differing_by_one_patient_are_blocked(self):
        """Neither query is wrong on its own; only the pair is. That is why
        this needs history rather than a per-query rule."""
        g = DifferencingGuard(P)
        base = {f"p{i}" for i in range(50)}
        assert g.check("u1", base)["allowed"] is True
        assert g.check("u1", base - {"p7"})["allowed"] is False

    def test_a_genuinely_different_cohort_is_allowed(self):
        g = DifferencingGuard(P)
        assert g.check("u1", {f"p{i}" for i in range(50)})["allowed"] is True
        assert g.check("u1", {f"q{i}" for i in range(50)})["allowed"] is True

    def test_an_identical_rerun_is_allowed(self):
        """Difference of zero discloses nobody — re-running a saved recipe
        must not be mistaken for an attack."""
        g = DifferencingGuard(P)
        c = {f"p{i}" for i in range(50)}
        g.check("u1", c)
        assert g.check("u1", c)["allowed"] is True

    def test_history_is_per_user(self):
        g = DifferencingGuard(P)
        base = {f"p{i}" for i in range(50)}
        g.check("u1", base)
        assert g.check("u2", base - {"p7"})["allowed"] is True

    def test_a_refused_probe_is_still_recorded(self):
        """Otherwise an attacker could retry variations indefinitely, each
        compared only against the ones that happened to be allowed."""
        g = DifferencingGuard(P)
        base = {f"p{i}" for i in range(50)}
        g.check("u1", base)
        g.check("u1", base - {"p7"})
        assert len(g.history["u1"]) == 2
