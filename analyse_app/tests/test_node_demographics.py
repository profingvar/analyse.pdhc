"""2026-11-20 — a node reads sex and birth year from its own CDR.

Before this, nothing on a real node filled `demographics.*`, so every spec
reading sex reported "(missing)" for every patient (cdr1, 51 of 51). The
synthetic harness carried demographics inline in its rows, which is why no
test saw it.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.node import ConsentUnavailable, NodeReader, ReadError, run_spec
from tests.test_node import KEY, _bundle, _capture, _policy, _spec


def _patient(guid, gender="female", birth="1971-03-04", **extra):
    return {"resourceType": "Patient", "id": guid, "gender": gender,
            "birthDate": birth, **extra}


class TestReadDemographics:

    def _reader(self):
        return NodeReader(base_url="http://cdr", service_key="k")

    def test_it_GETs_Patient_with_the_cohort_as_allowlist_and_purpose(self):
        cap = _capture(_bundle(_patient("p1"), _patient("p2", "male", "1950")))
        with patch("app.node.reader.requests.get", side_effect=cap):
            out = self._reader().read_demographics(
                purpose="statistics", patient_guids=["p2", "p1", "p1"])
        assert cap.url == "http://cdr/api/v1/fhir/Patient"
        assert cap.params["patient"] == "p1,p2"
        assert cap.headers["X-Access-Purpose"] == "statistics"
        assert out == {"p1": {"sex": "female", "birth_year": 1971},
                       "p2": {"sex": "male", "birth_year": 1950}}

    def test_only_sex_and_birth_year_leave_the_reader(self):
        res = _patient("p1", name=[{"family": "X"}],
                       identifier=[{"value": "000000-0000"}])
        cap = _capture(_bundle(res))
        with patch("app.node.reader.requests.get", side_effect=cap):
            out = self._reader().read_demographics(
                purpose="statistics", patient_guids=["p1"])
        assert set(out["p1"]) == {"sex", "birth_year"}
        assert "1971-03-04" not in repr(out) and "000000" not in repr(out)

    def test_unknown_or_absent_values_are_missing_not_guessed(self):
        cap = _capture(_bundle({"resourceType": "Patient", "id": "p1",
                                "gender": "unknown"}))
        with patch("app.node.reader.requests.get", side_effect=cap):
            out = self._reader().read_demographics(
                purpose="statistics", patient_guids=["p1"])
        assert out == {"p1": {"sex": None, "birth_year": None}}

    def test_a_patient_not_asked_about_raises(self):
        cap = _capture(_bundle(_patient("p1"), _patient("intruder")))
        with patch("app.node.reader.requests.get", side_effect=cap):
            with pytest.raises(ReadError, match="not asked about"):
                self._reader().read_demographics(
                    purpose="statistics", patient_guids=["p1"])

    def test_a_503_fails_closed(self):
        cap = _capture(status=503)
        with patch("app.node.reader.requests.get", side_effect=cap):
            with pytest.raises(ConsentUnavailable):
                self._reader().read_demographics(
                    purpose="statistics", patient_guids=["p1"])


class FakeReader:
    def __init__(self, rows, demographics):
        self.rows, self.demo = rows, demographics
        self.demo_asked = None

    def excluded_by_spärr(self, guids, ips_base_url, **kw):
        return set()

    def read_observations(self, *, purpose, patient_guids, **kw):
        return [r for r in self.rows if r["patient_guid"] in set(patient_guids)]

    def read_demographics(self, *, purpose, patient_guids, **kw):
        self.demo_asked = sorted(patient_guids)
        return {g: d for g, d in self.demo.items() if g in set(patient_guids)}


def _rows(n):
    return [{"patient_guid": f"p{i}", "concept": "x", "value": 2.0,
             "effective_at": "2026-05-01T08:00:00Z"} for i in range(n)]


def _sex_spec(**over):
    return _spec(variables=[{"name": "v", "from": "x", "agg": "mean"},
                            {"name": "kon", "from": "demographics.sex"}],
                 analyses=[{"type": "describe", "vars": ["v"]},
                           {"type": "frequency", "vars": ["kon"]}], **over)


def _run(spec, reader, cohort):
    frames = []
    from app.node import runner
    real = runner.build_frame

    def spy(projected, s):
        f = real(projected, s)
        frames.append(f)
        return f
    with patch.object(runner, "build_frame", side_effect=spy):
        run = run_spec(spec, _policy(), reader, project_key=KEY,
                       ips_base_url="http://ips", cohort=cohort)
    return run, frames[0] if frames else []


def test_sex_reaches_the_frame_for_every_patient():
    demo = {f"p{i}": {"sex": "female" if i % 2 else "male", "birth_year": 1960}
            for i in range(12)}
    reader = FakeReader(_rows(12), demo)
    run, frame = _run(_sex_spec(), reader, [f"p{i}" for i in range(12)])
    assert reader.demo_asked == sorted(f"p{i}" for i in range(12))
    assert sorted(r["kon"] for r in frame) == ["female"] * 6 + ["male"] * 6
    assert not any("no usable date" in n for n in run.notes)


def test_a_patient_with_demographics_but_no_observation_is_still_not_returned():
    """Demographics enrich patients the CDR returned observations for; they do
    not create cohort members, and the absence stays reported as such."""
    demo = {f"p{i}": {"sex": "female", "birth_year": 1960} for i in range(12)}
    reader = FakeReader(_rows(10), demo)
    run, frame = _run(_sex_spec(), reader, [f"p{i}" for i in range(12)])
    assert len(frame) == 10
    assert run.excluded["not_returned"] == 2
    assert all(r["kon"] == "female" for r in frame)


def test_a_patient_the_cdr_withholds_demographics_for_has_missing_sex():
    demo = {f"p{i}": {"sex": "male", "birth_year": 1960} for i in range(11)}
    reader = FakeReader(_rows(12), demo)
    run, frame = _run(_sex_spec(), reader, [f"p{i}" for i in range(12)])
    assert sorted((r["kon"] is None) for r in frame) == [False] * 11 + [True]


def test_a_spec_without_demographics_does_not_read_them():
    reader = FakeReader(_rows(12), {})
    _run(_spec(), reader, [f"p{i}" for i in range(12)])
    assert reader.demo_asked is None
