"""2026-11-20 — the coordinator merges per ANALYSIS, not per analysis type.

A spec with two `describe`s (sleep and birth year) came back from cdr1 as ONE
describe with n=102: 51 sleep means and 51 birth years pooled into a single
mean of 994, with nothing saying so. The coordinator grouped partials by
`kind`. Each node now tags a partial with its analysis's position in the spec.
"""
from __future__ import annotations

import pytest

from app.coordinator import combine
from app.node import run_spec
from tests.test_node import KEY, _policy, _spec
from tests.test_node_demographics import FakeReader


def _two_describes():
    return _spec(
        variables=[{"name": "somn", "from": "x", "agg": "mean"},
                   {"name": "fodelsear", "from": "demographics.birth_year"},
                   {"name": "kon", "from": "demographics.sex"}],
        analyses=[{"type": "describe", "vars": ["somn"]},
                  {"type": "frequency", "vars": ["kon"]},
                  {"type": "describe", "vars": ["fodelsear"]}])


def _node(node_id, n, sleep, year, offset=0):
    guids = [f"{node_id}-p{i}" for i in range(n)]
    rows = [{"patient_guid": g, "concept": "x", "value": sleep,
             "effective_at": "2026-05-01T08:00:00Z"} for g in guids]
    demo = {g: {"sex": "female", "birth_year": year} for g in guids}
    run = run_spec(_two_describes(), _policy(), FakeReader(rows, demo),
                   project_key=KEY, ips_base_url="http://ips", cohort=guids)
    out = run.to_json() if hasattr(run, "to_json") else dict(run.__dict__)
    out["node_id"] = node_id
    return out


def _combine(*runs):
    return combine(_two_describes(), list(runs), coordinator_version="t")


def test_two_describes_over_different_variables_stay_two_results():
    out = _combine(_node("cdr1", 12, 7.0, 1960), _node("cdr2", 10, 8.0, 1980))
    kinds = [r["kind"] for r in out.results]
    assert kinds == ["describe", "frequency", "describe"]
    somn, _, year = out.results
    assert somn["vars"] == ["somn"] and year["vars"] == ["fodelsear"]
    assert somn["pooled"]["n"] == 22 and year["pooled"]["n"] == 22
    assert somn["pooled"]["mean"] == pytest.approx((12 * 7 + 10 * 8) / 22)
    assert year["pooled"]["mean"] == pytest.approx((12 * 1960 + 10 * 1980) / 22)


def test_each_node_tags_its_partials_with_the_analysis_position():
    run = _node("cdr1", 12, 7.0, 1960)
    assert [p["analysis_index"] for p in run["partials"]] == [0, 1, 2]


def test_a_node_without_the_tag_still_merges_when_unambiguous():
    """A node deployed before this change sends no analysis_index. With one
    analysis per type that is still unambiguous, and must keep working."""
    spec = _spec(sources=["cdr1"])
    run = _node("cdr1", 12, 7.0, 1960)
    run["partials"] = [dict(run["partials"][0], analysis_index=None)]
    out = combine(spec, [run], coordinator_version="t")
    assert len(out.results) == 1 and out.results[0]["pooled"]["n"] == 12


def test_an_untagged_partial_in_an_ambiguous_spec_is_left_out_and_said():
    old = _node("cdr2", 10, 8.0, 1980)
    for p in old["partials"]:
        p.pop("analysis_index", None)
    out = _combine(_node("cdr1", 12, 7.0, 1960), old)
    describes = [r for r in out.results if r["kind"] == "describe"]
    assert [r["pooled"]["n"] for r in describes] == [12, 12]
    assert any("'cdr2'" in n and "left out" in n for n in out.notes)
