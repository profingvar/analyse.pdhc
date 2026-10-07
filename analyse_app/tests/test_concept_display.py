"""#758 — names for plan-bound concepts, and what happens when plan is down."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.analyse import concept_display as cd


@pytest.fixture(autouse=True)
def _clear():
    cd._cache.clear()
    yield
    cd._cache.clear()


class _R:
    def __init__(self, status=200, body=None, raises=None):
        self.status_code = status
        self._body = body if body is not None else {
            "parameter": [{"name": "display", "valueString": "B-glucos"}]}
        self._raises = raises

    def json(self):
        if self._raises:
            raise self._raises
        return self._body


def _concepts():
    return [
        {"concept_guid": "g1", "binding": "plan_concept", "display": None},
        {"concept_guid": None, "binding": "external_code", "display": "loinc 1-2"},
        {"concept_guid": "g3", "binding": "plan_concept", "display": "already named"},
        {"concept_guid": None, "binding": "unbound", "display": None},
    ]


def test_a_plan_concept_gets_its_name():
    with patch.object(cd.requests, "get", return_value=_R()):
        n = cd.fill_displays(_concepts(), "http://plan")
    assert n == 1


def test_an_external_code_is_not_looked_up():
    """It already names itself (`loinc 41653-7`); a lookup would be a wasted
    round trip to a sibling."""
    cs = _concepts()
    with patch.object(cd.requests, "get", return_value=_R()) as g:
        cd.fill_displays(cs, "http://plan")
    assert g.call_count == 1          # only g1, not the external or the unbound


def test_an_already_named_concept_is_left_alone():
    cs = _concepts()
    with patch.object(cd.requests, "get", return_value=_R()):
        cd.fill_displays(cs, "http://plan")
    assert cs[2]["display"] == "already named"


def test_plan_being_DOWN_keeps_the_guid_and_does_not_raise():
    """Cosmetic, therefore fail-open. A missing name is a smaller loss than a
    missing source, and the inventory's whole point is showing what exists."""
    cs = _concepts()
    with patch.object(cd.requests, "get",
                      side_effect=cd.requests.RequestException("down")):
        n = cd.fill_displays(cs, "http://plan")
    assert n == 0
    assert cs[0]["display"] is None


def test_a_transient_failure_is_NOT_cached():
    """Caching a reachability blip would blank the names for an hour."""
    with patch.object(cd.requests, "get",
                      side_effect=cd.requests.RequestException("blip")):
        cd.lookup_display("g1", "http://plan")
    assert "g1" not in cd._cache
    with patch.object(cd.requests, "get", return_value=_R()):
        assert cd.lookup_display("g1", "http://plan") == "B-glucos"


def test_a_clean_no_display_IS_cached():
    """A 404 is an answer; asking again every page load is not."""
    with patch.object(cd.requests, "get", return_value=_R(status=404)) as g:
        cd.lookup_display("g1", "http://plan")
        cd.lookup_display("g1", "http://plan")
    assert g.call_count == 1


def test_a_second_call_is_served_from_cache():
    with patch.object(cd.requests, "get", return_value=_R()) as g:
        cd.lookup_display("g1", "http://plan")
        cd.lookup_display("g1", "http://plan")
    assert g.call_count == 1


def test_no_base_url_means_no_lookups():
    """Before this ticket analyse had no plan variable at all. An unconfigured
    deployment must do nothing, not crash."""
    with patch.object(cd.requests, "get", return_value=_R()) as g:
        assert cd.fill_displays(_concepts(), "") == 0
    assert g.call_count == 0


def test_resolution_is_bounded():
    """21 concepts today; a reseed could make it hundreds. An unbounded fan-out
    at a sibling is a denial of service with extra steps."""
    many = [{"concept_guid": f"g{i}", "binding": "plan_concept", "display": None}
            for i in range(cd.MAX_LOOKUPS + 50)]
    with patch.object(cd.requests, "get", return_value=_R()) as g:
        cd.fill_displays(many, "http://plan")
    assert g.call_count == cd.MAX_LOOKUPS


def test_a_malformed_body_does_not_raise():
    with patch.object(cd.requests, "get",
                      return_value=_R(raises=ValueError("not json"))):
        assert cd.lookup_display("g1", "http://plan") is None
