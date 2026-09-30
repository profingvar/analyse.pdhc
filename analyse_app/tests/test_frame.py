"""#715 — the node dispatch never selected rows by variable.

Rows arrive LONG (one per observation, `concept` naming the variable); every
engine expects WIDE per-patient records. Nothing bridged the two, so each
analysis computed over every observation regardless of which variable it
named. Measured before the fix, on a dataset where variable `a` had 10 rows:

    describe(vars=["a"])      n = 20   (both concepts)
    frequency(vars=["a"])     counts ran past 9 into b's range
    histogram(var="a")        20 rows binned
    correlation(["a","b"])    n = 0    (read keys that never exist)
    compare_groups            None, silently
    completeness              None, silently
    over_time                 None, silently

All 467 tests passed throughout, because none exercised the dispatch with
more than one concept. These are the tests that would have caught it.
"""
from __future__ import annotations

import pytest

from app.engine import REGISTRY
from app.node.frame import build, series
from app.node.policy import NodePolicy
from app.node.runner import DispatchError, _dispatch
from app.privacy.disclosure import DisclosurePolicy
from app.spec.models import AnalysisSpec

POLICY = NodePolicy.load(
    "node_id: n1\ncdr_base_url: u\nk_min: 5\npermitted_purposes: [statistics]\n",
    is_text=True)
DISC = DisclosurePolicy(k_min=5)


def _spec(**over):
    base = {
        "title": "t", "purpose": "statistics", "sources": ["n1"],
        "index_event": {"observation": "x"},
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 0}]},
        "variables": [{"name": "a", "from": "x", "agg": "mean"},
                      {"name": "b", "from": "y", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["a"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


def _two_concepts():
    """`a` = concept x, values 0..9. `b` = concept y, values 0,2..18."""
    return ([{"pid": f"p{i}", "concept": "x", "value": float(i), "day_offset": i}
             for i in range(10)] +
            [{"pid": f"p{i}", "concept": "y", "value": float(i) * 2,
              "day_offset": i} for i in range(10)])


def _run(spec, analysis, rows):
    frame = build(rows, spec)
    return _dispatch(REGISTRY.get(analysis.type), analysis, frame, rows,
                     spec, POLICY, DISC)


class TestAnAnalysisSeesOnlyItsOwnVariable:
    """The defect, asserted in the exact shape it took."""

    def test_describe_counts_that_variable_not_every_observation(self):
        spec = _spec()
        p = _run(spec, spec.analyses[0], _two_concepts())
        d = p.to_json()["data"]
        assert d["n"] == 10, "20 means it took both concepts — the #715 bug"
        assert d["sum"] == 45.0          # 0+1+...+9, so mean 4.5

    def test_histogram_bins_that_variable_only(self):
        spec = _spec(analyses=[{"type": "histogram", "var": "a",
                                "bins": {"range": [0, 10], "width": 2}}])
        d = _run(spec, spec.analyses[0], _two_concepts()).to_json()["data"]
        assert sum(d["counts"]) + d["under"] + d["over"] == 10

    def test_correlation_reads_real_columns(self):
        """It read r["a"] on a row that has no such key, so every pair was
        empty and the coordinator finalized a result computed over nothing."""
        spec = _spec(analyses=[{"type": "correlation", "vars": ["a", "b"]}])
        d = _run(spec, spec.analyses[0], _two_concepts()).to_json()["data"]
        pair = d["pairs"]["a|b"]
        assert pair["n"] == 10, "n=0 is the #715 bug"
        assert pair["sx"] == 45.0 and pair["sy"] == 90.0

    def test_the_three_that_returned_nothing_now_return_something(self):
        for analysis in ({"type": "completeness"},
                         {"type": "over_time", "var": "a", "bin_days": 7,
                          "range_days": [0, 28]}):
            spec = _spec(analyses=[analysis])
            assert _run(spec, spec.analyses[0], _two_concepts()) is not None


class TestNothingIsSkippedSilently:
    """A missing number with no explanation is worse than an error: the
    researcher cannot tell a suppressed result from one never computed."""

    def test_an_uncomputable_analysis_raises_a_reportable_reason(self):
        spec = _spec(analyses=[{"type": "over_time", "var": "a", "bin_days": 7,
                                "range_days": [0, 28]}])
        rows = [{"pid": "p1", "concept": "y", "value": 1.0, "day_offset": 1}]
        with pytest.raises(DispatchError, match="nothing to plot"):
            _run(spec, spec.analyses[0], rows)

    def test_compare_groups_says_so_when_a_group_is_empty(self):
        spec = _spec(groups=[{"name": "lo", "where": {"a": {"lt": 5}}},
                             {"name": "hi", "where": {"a": {"gte": 99}}}],
                     analyses=[{"type": "compare_groups", "vars": ["a"]}])
        with pytest.raises(DispatchError, match="at least two groups"):
            _run(spec, spec.analyses[0], _two_concepts())

    def test_every_spec_type_either_dispatches_or_explains(self):
        """The invariant. A type the spec can express must never vanish from
        the results without a word."""
        cases = {
            "describe": {"type": "describe", "vars": ["a"]},
            "frequency": {"type": "frequency", "vars": ["a"]},
            "histogram": {"type": "histogram", "var": "a",
                          "bins": {"range": [0, 10], "width": 2}},
            "correlation": {"type": "correlation", "vars": ["a", "b"]},
            "completeness": {"type": "completeness"},
            "over_time": {"type": "over_time", "var": "a", "bin_days": 7,
                          "range_days": [0, 28]},
            "compare_groups": {"type": "compare_groups", "vars": ["a"]},
        }
        from app.spec.models import Analysis
        import typing
        declared = set()
        for m in typing.get_args(typing.get_args(Analysis)[0]):
            declared.add(typing.get_args(m.model_fields["type"].annotation)[0])
        assert declared == set(cases), (
            f"a spec type has no case here: {declared ^ set(cases)}")

        for kind, analysis in cases.items():
            over = {"analyses": [analysis]}
            if kind == "compare_groups":
                over["groups"] = [{"name": "lo", "where": {"a": {"lt": 5}}},
                                  {"name": "hi", "where": {"a": {"gte": 5}}}]
            spec = _spec(**over)
            try:
                got = _run(spec, spec.analyses[0], _two_concepts())
            except DispatchError as e:
                assert str(e), f"{kind}: refused without a reason"
                continue
            assert got is not None, f"{kind}: returned None with no explanation"


class TestHowAbsenceIsRepresented:
    """Settled by `completeness`, which counts None as missing — so a patient
    with no qualifying observation must be PRESENT with a null, not dropped."""

    def test_a_patient_with_no_observation_is_present_and_null(self):
        spec = _spec()
        rows = [{"pid": "p1", "concept": "x", "value": 1.0, "day_offset": 0},
                {"pid": "p2", "concept": "y", "value": 9.0, "day_offset": 0}]
        frame = build(rows, spec)
        assert len(frame) == 2
        p2 = next(r for r in frame if r["pid"] == "p2")
        assert p2["a"] is None and p2["b"] == 9.0

    def test_count_of_nothing_is_zero_not_missing(self):
        spec = _spec(variables=[{"name": "n", "from": "x", "agg": "count"}],
                     analyses=[{"type": "describe", "vars": ["n"]}])
        frame = build([{"pid": "p1", "concept": "y", "value": 1.0}], spec)
        assert frame[0]["n"] == 0

    def test_slope_needs_two_points(self):
        spec = _spec(variables=[{"name": "s", "from": "x", "agg": "slope"}],
                     analyses=[{"type": "describe", "vars": ["s"]}])
        one = build([{"pid": "p1", "concept": "x", "value": 1.0,
                      "day_offset": 0}], spec)
        assert one[0]["s"] is None
        two = build([{"pid": "p1", "concept": "x", "value": 0.0, "day_offset": 0},
                     {"pid": "p1", "concept": "x", "value": 10.0,
                      "day_offset": 5}], spec)
        assert two[0]["s"] == pytest.approx(2.0)

    def test_no_event_is_not_day_zero(self):
        """Treating it as 0 would put every never-affected patient at the far
        left of a survival curve."""
        spec = _spec(
            variables=[{"name": "t", "from": "x", "agg": "time_to_first_event"}],
            analyses=[{"type": "describe", "vars": ["t"]}])
        frame = build([{"pid": "p1", "concept": "y", "value": 1.0,
                        "day_offset": 3}], spec)
        assert frame[0]["t"] is None


class TestTheWindow:

    def test_both_ends_are_inclusive(self):
        spec = _spec(variables=[{"name": "a", "from": "x", "agg": "count",
                                 "window_days": [0, 30]}],
                     analyses=[{"type": "describe", "vars": ["a"]}])
        rows = [{"pid": "p1", "concept": "x", "value": 1.0, "day_offset": d}
                for d in (-1, 0, 15, 30, 31)]
        assert build(rows, spec)[0]["a"] == 3        # 0, 15 and 30

    def test_an_undated_observation_is_outside_every_window(self):
        spec = _spec(variables=[{"name": "a", "from": "x", "agg": "count",
                                 "window_days": [0, 30]}],
                     analyses=[{"type": "describe", "vars": ["a"]}])
        rows = [{"pid": "p1", "concept": "x", "value": 1.0}]
        assert build(rows, spec)[0]["a"] == 0


class TestGroupMembership:

    def test_a_record_missing_the_predicate_variable_is_not_a_member(self):
        """Absence is not a failed comparison. Treating None as "did not
        match" would move every incompletely-recorded patient into the other
        group without saying so."""
        spec = _spec(groups=[{"name": "lo", "where": {"a": {"lt": 5}}},
                             {"name": "hi", "where": {"a": {"gte": 5}}}],
                     analyses=[{"type": "compare_groups", "vars": ["b"]}])
        rows = _two_concepts() + [
            {"pid": "ghost", "concept": "y", "value": 99.0, "day_offset": 0}]
        p = _run(spec, spec.analyses[0], rows)
        groups = p.to_json()["data"]["groups"]
        assert groups["lo"]["n"] + groups["hi"]["n"] == 10   # not 11


class TestOverTimeKeepsTheSeries:

    def test_it_uses_observations_not_one_value_per_patient(self):
        """Collapsing first would destroy the thing being drawn."""
        spec = _spec()
        rows = [{"pid": "p1", "concept": "x", "value": float(d), "day_offset": d}
                for d in range(10)]
        assert len(series(rows, spec.variables[0])) == 10
        assert len(build(rows, spec)) == 1
