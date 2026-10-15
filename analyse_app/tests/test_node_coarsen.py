"""#824 — the coarsen step the node pipeline documented and did not have.

`run_spec`'s own docstring listed "6. coarsen — quasi-identifiers narrowed"
and nothing on the node path called anything in `app/privacy/coarsen.py`.
Meanwhile `privacy/projection.py` allowlists `day_offset` ("days from the
index event; never a calendar date") and `node/frame.py` reads it for every
longitudinal series — so the time axis was specified to arrive already
coarsened, and nothing produced it.

Nobody noticed because no node is deployed and `tests/test_frame.py` hands
the pipeline rows that ALREADY carry `day_offset`: the fixture supplied
exactly the field production did not. Same shape as #717.

These tests are therefore written to fail if the step is removed again, and
to pin the index-event decision, because the number leaves the node and
"day 14" means nothing without knowing what day 0 was.

One correction to the ticket, found by writing these: #824 recorded a
spec-declared index event as needing "a spec field that does not exist". It
exists. `spec.IndexEvent` is `{observation: <concept>}`, its docstring is
"the zero point for every day offset in the spec", and `AnalysisSpec` already
REFUSES a spec that uses `window_days` or `over_time` without one. The
decision was taken when the spec was written; only the code honouring it was
missing.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.privacy.coarsen import coarsen_rows, day_offset, index_events


def _row(patient="p1", when="2026-05-01T08:00:00Z", concept="c", **kw):
    return {"patient_guid": patient, "concept": concept, "value": 1.0,
            "effective_at": when, **kw}


class TestTheCalendarDateDoesNotSurvive:

    def test_effective_at_is_REMOVED_not_merely_accompanied(self):
        """Leaving the date beside the offset would make the projection
        allowlist the only thing between a calendar date and the outside, and
        one future addition to STRUCTURAL_FIELDS would be enough."""
        rows, _ = coarsen_rows([_row()])
        assert "effective_at" not in rows[0]
        assert "day_offset" in rows[0]

    def test_no_other_field_is_touched(self):
        rows, _ = coarsen_rows([_row(unit="L", meta={"author_org": "o"})])
        assert rows[0]["unit"] == "L"
        assert rows[0]["meta"] == {"author_org": "o"}
        assert rows[0]["concept"] == "c"

    def test_the_input_rows_are_not_mutated(self):
        src = [_row()]
        coarsen_rows(src)
        assert src[0]["effective_at"] == "2026-05-01T08:00:00Z"

    def test_a_projected_record_cannot_carry_a_date_afterwards(self):
        """The end-to-end property, through the real allowlist."""
        from app.privacy import build_allowlist, project
        from app.spec import AnalysisSpec
        spec = AnalysisSpec.model_validate({
            "title": "t", "purpose": "statistics", "sources": ["cdr1"],
            "cohort": {"include": [{"observation": "c", "op": ">=",
                                    "value": 0}]},
            "variables": [{"name": "v", "from": "c", "agg": "mean"}],
            "analyses": [{"type": "describe", "vars": ["v"]}],
        })
        rows, _ = coarsen_rows([_row()])
        out = project(rows[0], build_allowlist(spec))
        assert out["day_offset"] == 0
        assert not any("2026" in str(v) for v in out.values())


class TestTheIndexEventIsTheSpecsOwnConcept:
    """Day 0 is the patient's FIRST observation of `spec.index_event
    .observation`. Per patient, which is what makes it safe: the analyst
    holds the concept but not the date, so there is no inverse.

    The alternative that looked natural — the spec's cohort window start — is
    re-identifiable BY CONSTRUCTION: the researcher wrote the window, so they
    hold the zero point and `day_offset + window_start` is the exact calendar
    date of every observation. An offset measured from a date the analyst
    knows is a date, spelled differently.
    """

    def test_day_zero_is_the_first_observation_of_the_DECLARED_concept(self):
        rows, _ = coarsen_rows([
            _row(when="2026-05-01", concept="other"),
            _row(when="2026-05-11", concept="index"),
            _row(when="2026-05-21", concept="other"),
        ], index_concept="index")
        by_concept = {(r["concept"], r["day_offset"]) for r in rows}
        assert ("index", 0) in by_concept
        assert ("other", -10) in by_concept
        assert ("other", 10) in by_concept

    def test_offsets_BEFORE_the_index_event_are_negative_not_clamped(self):
        """A baseline measurement is the ordinary case, `window_days` only
        requires start <= end, so [-30, 0] is expressible — and clamping at
        zero would silently discard it."""
        rows, _ = coarsen_rows([
            _row(when="2026-04-01", concept="other"),
            _row(when="2026-05-01", concept="index"),
        ], index_concept="index")
        assert min(r["day_offset"] for r in rows) == -30

    def test_a_patient_with_NO_index_observation_has_NO_zero_point(self):
        """Not a fallback to their earliest row. A fallback is worse than
        missing: their "day 14" would be fourteen days after something else,
        pooled into a figure labelled as fourteen days after the index."""
        rows, undated = coarsen_rows([
            _row(patient="has", when="2026-05-01", concept="index"),
            _row(patient="has", when="2026-05-08", concept="other"),
            _row(patient="hasnt", when="2026-05-01", concept="other"),
        ], index_concept="index")
        assert undated == 1
        hasnt = [r for r in rows if r["patient_guid"] == "hasnt"][0]
        assert hasnt["day_offset"] is None

    def test_each_patient_is_aligned_to_their_OWN_index_event(self):
        """The cost, asserted so it is impossible to read the number as a
        shared calendar day: two patients' day 0 are different dates."""
        rows, _ = coarsen_rows([
            _row(patient="a", when="2026-01-01", concept="index"),
            _row(patient="a", when="2026-01-08", concept="other"),
            _row(patient="b", when="2026-07-01", concept="index"),
            _row(patient="b", when="2026-07-08", concept="other"),
        ], index_concept="index")
        by_patient = {}
        for r in rows:
            by_patient.setdefault(r["patient_guid"], []).append(r["day_offset"])
        assert sorted(by_patient["a"]) == [0, 7]
        assert sorted(by_patient["b"]) == [0, 7]

    def test_one_patients_dates_cannot_shift_anothers_offsets(self):
        """The zero point must not be computed cohort-wide. If it were,
        adding an early patient would silently renumber everyone."""
        base, _ = coarsen_rows([_row(patient="b", when="2026-07-01")])
        with_early, _ = coarsen_rows([
            _row(patient="a", when="2020-01-01"),
            _row(patient="b", when="2026-07-01")])
        b = [r for r in with_early if r["patient_guid"] == "b"][0]
        assert b["day_offset"] == base[0]["day_offset"] == 0

    def test_index_events_reports_the_earliest_per_patient(self):
        got = index_events([_row(patient="a", when="2026-03-04"),
                            _row(patient="a", when="2026-01-02"),
                            _row(patient="b", when="2026-09-09")])
        assert got == {"a": date(2026, 1, 2), "b": date(2026, 9, 9)}

    def test_index_events_honours_the_concept_filter(self):
        got = index_events([_row(patient="a", when="2026-01-02",
                                 concept="other"),
                            _row(patient="a", when="2026-03-04",
                                 concept="index")],
                           concept="index")
        assert got == {"a": date(2026, 3, 4)}


class TestWithNoDeclaredIndexEventTheFallbackIsOrderOnly:
    """A spec with no `index_event` cannot use `window_days` or `over_time` —
    `AnalysisSpec` refuses it. The only remaining reader of `day_offset` is
    `frame._ordered`, which needs the ORDER and nothing else, so the
    patient's own earliest row is a complete zero point there."""

    def test_day_zero_is_the_patients_earliest_row_of_any_concept(self):
        rows, _ = coarsen_rows([
            _row(when="2026-05-11"), _row(when="2026-05-01"),
            _row(when="2026-05-31")])
        assert sorted(r["day_offset"] for r in rows) == [0, 10, 30]

    def test_the_ORDER_is_what_first_and_last_actually_depend_on(self):
        from app.node.frame import _ordered
        rows, _ = coarsen_rows([
            _row(when="2026-05-31", value=3.0),
            _row(when="2026-05-01", value=1.0),
            _row(when=None, value=99.0),
        ])
        got = _ordered(rows)
        assert [r["value"] for r in got[:2]] == [1.0, 3.0]
        assert got[-1]["value"] == 99.0, "an undated row must sort last"


class TestAnUndatedRowIsNotGivenADate:

    def test_it_keeps_a_None_offset_and_is_counted(self):
        rows, undated = coarsen_rows([_row(when="2026-05-01"),
                                      _row(when=None)])
        assert undated == 1
        assert sorted((r["day_offset"] is None) for r in rows) == [False, True]

    def test_a_patient_with_NO_dated_row_has_no_index_and_stays_undated(self):
        rows, undated = coarsen_rows([_row(patient="z", when=None)])
        assert undated == 1 and rows[0]["day_offset"] is None

    def test_an_unparseable_date_stays_undated_rather_than_becoming_a_guess(self):
        rows, undated = coarsen_rows([_row(when="last tuesday")])
        assert undated == 1 and rows[0]["day_offset"] is None

    def test_an_undated_row_is_excluded_from_every_window_and_series(self):
        """Already true in frame.py; asserted here so the two halves of the
        contract are pinned together."""
        from app.node.frame import _in_window, series

        class _V:
            from_ = "c"
            window_days = (0, 30)
        rows, _ = coarsen_rows([_row(when=None)])
        assert _in_window(rows[0], (0, 30)) is False
        assert series(rows, _V()) == []


class TestTheDateFormatsCdrActuallyEmits:

    @pytest.mark.parametrize("text,expected", [
        ("2026-05-01T08:00:00Z", 0),
        ("2026-05-01T08:00:00+00:00", 0),
        ("2026-05-01", 0),
        ("2026-05-11T23:59:59Z", 10),
    ])
    def test_iso_forms_parse(self, text, expected):
        rows, undated = coarsen_rows([_row(when="2026-05-01"), _row(when=text)])
        assert undated == 0
        assert max(r["day_offset"] for r in rows) == expected

    def test_a_datetime_or_date_object_works_too(self):
        rows, undated = coarsen_rows([
            _row(when=date(2026, 5, 1)),
            _row(when=datetime(2026, 5, 3, 12, 0)),
        ])
        assert undated == 0
        assert sorted(r["day_offset"] for r in rows) == [0, 2]

    def test_a_time_of_day_does_not_make_a_partial_day(self):
        """`day_offset` is whole days. 23:59 on day 1 is day 1, not day 1.99
        — a fractional offset would be a sharper time signal than the design
        intends."""
        assert day_offset(datetime(2026, 5, 2, 23, 59),
                          datetime(2026, 5, 1, 0, 1)) == 1


class TestTheNodeRunnerActuallyCallsIt:
    """The step was documented and absent; asserting the function works is
    not the same as asserting the pipeline runs it. (`feedback_test_the_dish_
    not_the_ingredient`.)"""

    def _spec(self):
        from app.spec import AnalysisSpec
        return AnalysisSpec.model_validate({
            "title": "t", "purpose": "statistics", "sources": ["cdr1"],
            "cohort": {"include": [{"observation": "c", "op": ">=",
                                    "value": 0}]},
            "index_event": {"observation": "c"},
            "variables": [{"name": "v", "from": "c", "agg": "last",
                           "window_days": [0, 30]}],
            "analyses": [{"type": "describe", "vars": ["v"]}],
        })

    def _policy(self):
        from app.node import NodePolicy
        return NodePolicy.load(
            "node_id: cdr1\ncdr_base_url: http://x\n"
            "permitted_purposes: [statistics]\n"
            "permitted_analyses: [describe]\nk_min: 1\n", is_text=True)

    class _Reader:
        def __init__(self, rows):
            self.rows = rows

        def excluded_by_spärr(self, guids, ips_base_url, **kw):
            return set()

        def read_observations(self, *, purpose, patient_guids, **kw):
            return [dict(r) for r in self.rows
                    if r["patient_guid"] in set(patient_guids)]

    def _run(self, rows):
        from app.node import run_spec
        from app.privacy import ProjectKey
        return run_spec(
            self._spec(), self._policy(), self._Reader(rows),
            project_key=ProjectKey("p", b"k" * 32),
            ips_base_url="http://ips", ips_api_key="k",
            cohort=sorted({r["patient_guid"] for r in rows}))

    def test_a_windowed_variable_over_DATED_rows_produces_a_result(self):
        """Before this fix the rows arrived with `effective_at` and no
        `day_offset`, so `_in_window` excluded every one of them and a
        describe over 30 days of real data returned nothing — silently."""
        rows = [_row(patient=f"p{i}", when=f"2026-05-{d:02d}", value=float(d))
                for i in range(1, 7) for d in (1, 11, 21)]
        run = self._run(rows)
        assert run.partials, "the windowed variable computed over nothing"
        data = run.partials[0]["data"]
        assert data["n"] == 6 and data["missing"] == 0
        # `agg: last` over window [0, 30] is each patient's day-21 value, so
        # the offsets are not merely present, they are RIGHT: a zero point
        # one day out would pick day 11.
        assert data["sum"] == 6 * 21.0

    def test_rows_that_arrive_UNDATED_are_reported_not_hidden(self):
        rows = [_row(patient=f"p{i}", when=None) for i in range(1, 7)]
        run = self._run(rows)
        assert any("no usable date" in n for n in run.notes), run.notes

    def test_no_calendar_date_reaches_a_partial(self):
        rows = [_row(patient=f"p{i}", when=f"2026-05-{d:02d}", value=float(d))
                for i in range(1, 7) for d in (1, 11, 21)]
        blob = str(self._run(rows).to_json())
        assert "2026-05" not in blob
