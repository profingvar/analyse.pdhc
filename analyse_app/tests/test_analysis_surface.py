"""#722 — the coordinator's web surface.

`app/ui/` was built for Phase 3 (#655–#658), tested, and imported by nothing:
it renders ENGINE output and the coordinator had only a CLI. So there was no
page for a person to use, and **#688's usability acceptance could not be
performed at all** — every one of its four claims needs a rendered page.

These tests assert the two things that make the page trustworthy rather than
merely present: that it never silently passes synthetic numbers off as real,
and that a spec is refused with the spec's own reasoning.
"""
from __future__ import annotations

import json

import pytest


def _spec():
    return {
        "title": "Blodtryck", "purpose": "statistics",
        "sources": ["cdr1", "cdr2", "cdr3"],
        "cohort": {"include": [
            {"observation": "x", "op": ">=", "value": 0}]},
        "variables": [{"name": "matvarde", "from": "x", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["matvarde"]}],
    }


class TestTheSurfaceExists:
    """The whole point of #722: before it, these routes did not exist."""

    def test_the_routes_are_registered(self, app):
        rules = {str(r) for r in app.url_map.iter_rules()}
        assert "/analysis" in rules
        assert "/analysis/run" in rules

    def test_it_is_role_gated_like_the_rest_of_analyse(self, app):
        """A research surface is not public. The gate is the same
        researcher_required used by the cohort routes."""
        import inspect
        from app.routes import analysis
        src = inspect.getsource(analysis)
        assert src.count("@researcher_required") >= 3


class TestSyntheticIsNeverPassedOffAsReal:
    """The property that matters most here. A number whose origin is
    ambiguous is worse than no number."""

    def test_a_run_with_no_nodes_is_labelled_synthetic(self, app, client):
        app.config["ANALYSE_NODES"] = ""
        app.config["ANALYSE_TRANSPORT_SECRET"] = ""
        r = client.post("/analysis/run?format=json", json=_spec())
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        body = r.get_json()
        assert body["mode"] == "synthetic"

    def test_the_mode_reaches_the_provenance(self, app, client):
        app.config["ANALYSE_NODES"] = ""
        app.config["ANALYSE_TRANSPORT_SECRET"] = ""
        body = client.post("/analysis/run?format=json", json=_spec()).get_json()
        flat = {k: v for k, v in body["provenance"]}
        assert "SYNTHETIC" in flat.get("Data", ""), (
            "the provenance must carry it too — a page banner can be "
            "screenshotted away from its numbers")

    def test_the_rendered_page_says_so(self, app, client):
        app.config["ANALYSE_NODES"] = ""
        app.config["ANALYSE_TRANSPORT_SECRET"] = ""
        page = client.post("/analysis/run", data={"spec": json.dumps(_spec())}
                           ).get_data(as_text=True)
        assert "Syntetiska data" in page

    def test_live_needs_BOTH_nodes_and_the_secret(self, app):
        """The transport secret has no default by design: one that fell back
        to a development constant would ship working and attest nothing. So
        nodes alone must not count as live."""
        from app.routes.analysis import _can_run_live
        with app.test_request_context():
            app.config["ANALYSE_NODES"] = "cdr1=http://n1"
            app.config["ANALYSE_TRANSPORT_SECRET"] = ""
            assert _can_run_live() is False
            app.config["ANALYSE_TRANSPORT_SECRET"] = "s" * 32
            assert _can_run_live() is True


class TestABadSpecIsRefusedWithItsOwnReasoning:

    def test_invalid_json_is_not_a_500(self, app, client):
        r = client.post("/analysis/run", data={"spec": "{not json"})
        assert r.status_code == 400
        assert r.get_json()["error"] == "bad_spec"

    def test_the_spec_validators_message_is_passed_through(self, app, client):
        """The spec carries real reasoning — "index_event is required when
        window_days or over_time is used, day offsets have no zero point
        without it". Replacing that with "invalid spec" throws away the only
        part that helps."""
        bad = _spec()
        bad["analyses"] = [{"type": "over_time", "var": "matvarde",
                            "bin_days": 7, "range_days": [0, 28]}]
        r = client.post("/analysis/run?format=json", json=bad)
        assert r.status_code == 400
        assert r.get_json()["error"] == "invalid_spec"
        assert "index_event" in r.get_json()["message"]


class TestItRendersThroughAppUi:
    """The reason the package existed. If this passes, #688 is performable."""

    def test_a_result_carries_a_plain_language_sentence_or_a_chart(
            self, app, client):
        app.config["ANALYSE_NODES"] = ""
        app.config["ANALYSE_TRANSPORT_SECRET"] = ""
        body = client.post("/analysis/run?format=json", json=_spec()).get_json()
        assert body["blocks"], "the run produced no blocks to render"
        assert any(b.get("sentence") or b.get("svg") for b in body["blocks"])

    def test_a_renderer_failure_does_not_lose_the_numbers(self, app, client,
                                                          monkeypatch):
        """A page that 500s because a sentence template broke has thrown away
        correct figures for a cosmetic reason."""
        from app.ui import sentences
        monkeypatch.setattr(sentences, "describe_sentence",
                            lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
        app.config["ANALYSE_NODES"] = ""
        app.config["ANALYSE_TRANSPORT_SECRET"] = ""
        r = client.post("/analysis/run?format=json", json=_spec())
        assert r.status_code == 200
        assert r.get_json()["blocks"]


class TestAChartActuallyRenders:
    """#688's fourth claim is that charts are server-rendered inline SVG with
    real text, verified with a screen reader. That cannot be assessed if no
    chart appears.

    The first version of the surface called `charts.histogram(..., lang=...)`
    and there is no such parameter, so every histogram raised a TypeError,
    was caught per block, and silently became "(could not render)". The page
    looked fine and the chart was simply absent — found while preparing #688,
    not by a test, which is why this one exists.
    """

    def _histogram_spec(self):
        return {
            "title": "Fördelning", "purpose": "statistics",
            "sources": ["cdr1", "cdr2", "cdr3"],
            "cohort": {"include": [{"observation": "x", "op": ">=", "value": 0}]},
            "variables": [{"name": "matvarde", "from": "x", "agg": "mean"}],
            "analyses": [{"type": "histogram", "var": "matvarde",
                          "bins": {"range": [0, 20], "width": 2}}],
        }

    def test_a_histogram_produces_svg(self, app, client):
        app.config["ANALYSE_NODES"] = ""
        app.config["ANALYSE_TRANSPORT_SECRET"] = ""
        body = client.post("/analysis/run?format=json",
                           json=self._histogram_spec()).get_json()
        block = body["blocks"][0]
        assert block["svg"], f"no chart rendered: {block.get('sentence')}"
        assert "<svg" in block["svg"]

    def test_the_svg_carries_text_not_just_shapes(self, app, client):
        """A picture of numbers with no text is unreadable to a screen reader,
        which is the whole reason these are server-rendered SVG rather than a
        canvas or an image."""
        app.config["ANALYSE_NODES"] = ""
        app.config["ANALYSE_TRANSPORT_SECRET"] = ""
        svg = client.post("/analysis/run?format=json",
                          json=self._histogram_spec()).get_json()["blocks"][0]["svg"]
        assert "<text" in svg, "no text elements — nothing for a reader to read"

    def test_no_renderer_is_called_with_an_argument_it_does_not_take(self, app):
        """The class of bug this whole section exists for: the call compiled,
        ran, and failed only at runtime inside a catch."""
        import inspect
        from app.ui import charts
        sig = inspect.signature(charts.histogram)
        assert "lang" not in sig.parameters, (
            "charts.histogram gained a lang parameter — update the caller in "
            "app/routes/analysis.py, which deliberately does not pass one")
