"""#659–#661 — Phase 4: regression, Kaplan-Meier, keyed_hash linkage."""
from __future__ import annotations

import random

import pytest

from app.engine import regression
from app.privacy import DisclosurePolicy
from app.privacy.linkage import (
    LinkageDisabled, LinkageKey, LinkageNotPermitted, deduplicated_count,
    enabled, join_across_nodes, token,
)
from app.testing import assert_clean

P = DisclosurePolicy()
ON = {"ANALYSE_ENABLE_KEYED_HASH": "1"}
KEY = LinkageKey(b"k" * 32)


class TestLinearRegressionIsExact:

    @pytest.mark.parametrize("n_nodes", [1, 2, 4])
    def test_federated_recovers_the_true_coefficients(self, n_nodes):
        rnd = random.Random(n_nodes)
        rows = [[rnd.gauss(0, 1), rnd.gauss(0, 1)] for _ in range(600)]
        y = [2.0 + 3.0 * r[0] - 1.5 * r[1] + rnd.gauss(0, 0.2) for r in rows]
        parts = [regression.linear_local(rows[i::n_nodes], y[i::n_nodes],
                                         source=f"c{i}")
                 for i in range(n_nodes)]
        got = regression.linear_finalize(
            regression.linear_merge(parts), P, names=["x1", "x2"]).pooled
        assert got["coefficients"]["x1"] == pytest.approx(3.0, abs=0.05)
        assert got["coefficients"]["x2"] == pytest.approx(-1.5, abs=0.05)

    def test_federated_equals_pooled_exactly(self):
        rnd = random.Random(9)
        rows = [[rnd.gauss(0, 1)] for _ in range(400)]
        y = [1.0 + 2.0 * r[0] + rnd.gauss(0, 0.5) for r in rows]
        spread = regression.linear_finalize(regression.linear_merge([
            regression.linear_local(rows[:200], y[:200]),
            regression.linear_local(rows[200:], y[200:])]), P).pooled
        pooled = regression.linear_finalize(regression.linear_merge([
            regression.linear_local(rows, y)]), P).pooled
        for k in pooled["coefficients"]:
            assert spread["coefficients"][k] == pytest.approx(
                pooled["coefficients"][k], rel=1e-9)

    def test_a_singular_system_is_refused_not_guessed(self):
        """A plausible-looking answer from a singular system is worse than
        no answer."""
        rows = [[1.0, 2.0] for _ in range(40)]     # perfectly collinear
        y = [1.0] * 40
        r = regression.linear_finalize(regression.linear_merge(
            [regression.linear_local(rows, y)]), P)
        assert r.pooled["suppressed"] is True
        assert any("too closely related" in n for n in r.notes)

    def test_too_few_patients_is_suppressed(self):
        rows = [[1.0], [2.0], [3.0]]
        r = regression.linear_finalize(regression.linear_merge(
            [regression.linear_local(rows, [1.0, 2.0, 3.0])]), P)
        assert r.pooled["suppressed"] is True

    def test_it_does_not_claim_causation(self):
        rnd = random.Random(3)
        rows = [[rnd.gauss(0, 1)] for _ in range(100)]
        y = [r[0] * 2 for r in rows]
        r = regression.linear_finalize(regression.linear_merge(
            [regression.linear_local(rows, y)]), P)
        assert any("does not show that one causes" in n for n in r.notes)


class TestKaplanMeier:

    def test_events_and_at_risk_add_across_nodes(self):
        a = regression.km_local([(1, 2, 50), (2, 3, 48)], source="c1")
        b = regression.km_local([(1, 1, 40), (2, 2, 39)], source="c2")
        merged = regression.km_merge([a, b])
        assert merged.data["intervals"][1]["at_risk"] == 90
        assert merged.data["intervals"][1]["events"] == 3

    def test_survival_decreases_monotonically(self):
        r = regression.km_finalize(regression.km_merge(
            [regression.km_local([(1, 2, 100), (2, 5, 98), (3, 4, 93)])]), P)
        surv = [s["survival"] for s in r.pooled["steps"] if s["survival"]]
        assert surv == sorted(surv, reverse=True)

    def test_thin_periods_are_suppressed(self):
        r = regression.km_finalize(regression.km_merge(
            [regression.km_local([(1, 1, 100), (2, 1, 3)])]), P)
        assert any(s["at_risk"] == "<k" for s in r.pooled["steps"])
        assert any("still being followed" in n for n in r.notes)

    def test_it_describes_rather_than_explains(self):
        r = regression.km_finalize(regression.km_merge(
            [regression.km_local([(1, 1, 100)])]), P)
        assert any("not why" in n for n in r.notes)


class TestKeyedHashLinkage:

    def test_it_is_off_unless_the_flag_is_set(self):
        assert enabled({}) is False
        with pytest.raises(LinkageDisabled, match="behind ANALYSE_ENABLE"):
            token("x", KEY, env={})

    def test_the_same_identity_gives_the_same_token_on_any_node(self):
        """That is the whole mechanism: two nodes agree without either
        revealing who its patients are."""
        assert token("19850101-1234", KEY, env=ON) == \
               token("19850101-1234", KEY, env=ON)

    def test_different_identities_differ(self):
        assert token("a", KEY, env=ON) != token("b", KEY, env=ON)

    def test_the_token_does_not_contain_the_identity(self):
        assert "19850101" not in token("19850101-1234", KEY, env=ON)

    def test_deduplicated_counting_is_what_it_permits(self):
        a = token("p1", KEY, env=ON)
        b = token("p2", KEY, env=ON)
        assert deduplicated_count([[a, b], [a]]) == 2

    def test_joining_across_nodes_is_refused_in_code(self):
        """Present as a function so the attempt fails loudly and names the
        reason, rather than someone building the join out of a set
        intersection and believing it was permitted."""
        with pytest.raises(LinkageNotPermitted, match="trusted mode"):
            join_across_nodes()

    def test_the_count_does_not_return_the_overlap(self):
        """A coordinator holding the overlap could ask a node about those
        specific patients."""
        import inspect
        src = inspect.getsource(deduplicated_count)
        assert "return len(seen)" in src

    def test_a_weak_linkage_key_is_refused(self):
        with pytest.raises(ValueError, match="at least 32 bytes"):
            LinkageKey(b"short")

    def test_the_key_refuses_to_render_itself(self):
        assert "redacted" in repr(LinkageKey(b"SECRETKEYMATERIAL" + b"x" * 20))


class TestPhase4OutputsAreClean:

    def test_no_result_carries_an_identifier(self):
        rnd = random.Random(5)
        rows = [[rnd.gauss(0, 1)] for _ in range(100)]
        y = [r[0] for r in rows]
        assert_clean(regression.linear_finalize(regression.linear_merge(
            [regression.linear_local(rows, y)]), P).to_json(), where="linear")
        assert_clean(regression.km_finalize(regression.km_merge(
            [regression.km_local([(1, 2, 100)])]), P).to_json(), where="km")
