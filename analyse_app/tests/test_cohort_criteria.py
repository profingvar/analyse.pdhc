"""#696 (AN-13) — the spec's inclusion criteria are actually applied.

Before this, `spec.cohort.include` was read by nothing on the node path: a
spec saying "TBSA >= 5" computed over everyone, and the figure came back
labelled with a criterion that had never been applied.
"""
from __future__ import annotations

import pytest

from app.node.cohort_criteria import CohortCriteriaError, apply, members
from app.spec import AnalysisSpec


def _spec(include, **over):
    base = {
        "title": "t", "purpose": "statistics", "sources": ["cdr1"],
        "cohort": {"include": include},
        "variables": [{"name": "v", "from": "tbsa", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


def _row(pid, concept, value):
    return {"pid": pid, "concept": concept, "value": value, "source": "cdr1"}


class TestTheCriterionIsApplied:

    def test_patients_below_the_threshold_are_excluded(self):
        rows = [_row("a", "tbsa", 9), _row("b", "tbsa", 2),
                _row("c", "tbsa", 5)]
        spec = _spec([{"observation": "tbsa", "op": ">=", "value": 5}])
        assert members(rows, spec) == {"a", "c"}

    def test_the_excluded_count_is_reported(self):
        """A cohort smaller than the candidate list is normal and must be
        visible; one silently equal to it is the bug."""
        rows = [_row("a", "tbsa", 9), _row("b", "tbsa", 2)]
        kept, excluded = apply(rows,
                               _spec([{"observation": "tbsa", "op": ">=",
                                       "value": 5}]))
        assert {r["pid"] for r in kept} == {"a"}
        assert excluded == 1

    @pytest.mark.parametrize("op,value,expected", [
        (">=", 5, {"a", "c"}), (">", 5, {"a"}), ("<=", 5, {"b", "c"}),
        ("<", 5, {"b"}), ("==", 5, {"c"}), ("!=", 5, {"a", "b"}),
    ])
    def test_every_operator(self, op, value, expected):
        rows = [_row("a", "tbsa", 9), _row("b", "tbsa", 2),
                _row("c", "tbsa", 5)]
        assert members(rows, _spec([{"observation": "tbsa", "op": op,
                                     "value": value}])) == expected


class TestSemanticsThatAreChoices:

    def test_any_observation_satisfying_it_includes_the_patient(self):
        """'Ever had TBSA >= 5' is how an inclusion criterion reads
        clinically. Testing the aggregate instead would make membership
        depend on an unrelated part of the spec."""
        rows = [_row("a", "tbsa", 1), _row("a", "tbsa", 99),
                _row("b", "tbsa", 1), _row("b", "tbsa", 2)]
        spec = _spec([{"observation": "tbsa", "op": ">=", "value": 5}])
        assert members(rows, spec) == {"a"}

    def test_several_criteria_are_anded(self):
        rows = [_row("a", "tbsa", 9), _row("a", "age_at_burn", 40),
                _row("b", "tbsa", 9), _row("b", "age_at_burn", 5),
                _row("c", "tbsa", 1), _row("c", "age_at_burn", 40)]
        spec = _spec([{"observation": "tbsa", "op": ">=", "value": 5},
                      {"observation": "age_at_burn", "op": ">=", "value": 18}])
        assert members(rows, spec) == {"a"}

    def test_a_different_concept_does_not_satisfy_it(self):
        rows = [_row("a", "something_else", 99)]
        spec = _spec([{"observation": "tbsa", "op": ">=", "value": 5}])
        assert members(rows, spec) == set()

    def test_a_non_numeric_value_is_not_a_match_but_not_an_error(self):
        """A concept can legitimately carry text for some patients."""
        rows = [_row("a", "tbsa", "unknown"), _row("b", "tbsa", 9)]
        spec = _spec([{"observation": "tbsa", "op": ">=", "value": 5}])
        assert members(rows, spec) == {"b"}

    def test_numeric_strings_are_compared_as_numbers(self):
        rows = [_row("a", "tbsa", "9")]
        assert members(rows, _spec([{"observation": "tbsa", "op": ">=",
                                     "value": 5}])) == {"a"}


class TestItRefusesRatherThanGuesses:

    def test_an_age_band_criterion_is_refused(self):
        """Age is not projected into the rows a node reads. Ignoring the
        criterion would compute over every age."""
        spec = _spec([{"age_band": {"from": 18, "to": 65}}])
        with pytest.raises(CohortCriteriaError, match="age"):
            members([_row("a", "tbsa", 9)], spec)

    def test_rows_without_a_concept_are_refused_not_emptied(self):
        """The guard must test the VALUE, not key membership: projection
        fills every allowlisted field, so 'concept' in row is true even when
        the source never supplied one. An empty cohort would read as 'nobody
        qualifies' rather than 'this node cannot tell'."""
        rows = [{"pid": "a", "concept": None, "value": 9}]
        spec = _spec([{"observation": "tbsa", "op": ">=", "value": 5}])
        with pytest.raises(CohortCriteriaError, match="cannot tell"):
            members(rows, spec)

    def test_no_rows_at_all_is_not_an_error(self):
        """A source that read nothing has an empty cohort, honestly."""
        spec = _spec([{"observation": "tbsa", "op": ">=", "value": 5}])
        assert members([], spec) == set()
