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


# ── #724 / #725 / #726 ────────────────────────────────────────────────

def _suppressing_spec(**over):
    """Cohort narrow enough that one category falls below k_min.

    10 patients, 4/4/2 across the sources: every source below k_min on its
    own, the pool above it. Nothing in the shipped example ever crossed
    k_min, which is why a suppressed result had never been rendered.
    """
    s = {
        "title": "Kön vid högt mätvärde", "purpose": "statistics",
        "sources": ["cdr1", "cdr2", "cdr3"],
        "cohort": {"include": [
            {"observation": "x", "op": ">=", "value": 18}]},
        "variables": [{"name": "matvarde", "from": "x", "agg": "max"},
                      {"name": "kon", "from": "demographics.sex"}],
        "analyses": [{"type": "frequency", "vars": ["kon"]}],
    }
    s.update(over)
    return s


class TestSuppressionReachesTheReader:
    """#724. The engine suppressed correctly onto a page that showed nothing.

    A `frequency` card used to render as its heading plus one line —
    "the figures are exact even when several sources are combined" — with no
    figures under it. On the one card where a category had been withheld, the
    reader was reassured about numbers that were not there.
    """

    def test_a_withheld_category_is_visible_as_a_number_and_a_reason(
            self, app, client):
        r = client.post("/analysis/run?format=json", json=_suppressing_spec())
        assert r.status_code == 200
        freq = [b for b in r.get_json()["blocks"] if b["kind"] == "frequency"]
        assert freq, "frequency produced no block at all"
        b = freq[0]
        assert b["table"], "the counts were computed and never displayed"
        # data_table escapes, correctly, so the marker is "&lt;5".
        assert "&lt;5" in b["table"], "the withheld count is not on the page"
        assert "6" in b["table"], "the surviving count is not on the page"
        assert b["sentence"] and "5" in b["sentence"], \
            "nothing explains why a category is missing"

    def test_the_page_shows_the_notation_the_sentence_promises(
            self, app, client):
        """SUPPRESSED is the literal "<k" and the sentence says "<5". The
        reader was told to look for one notation and shown another."""
        r = client.post("/analysis/run?format=json", json=_suppressing_spec())
        b = [x for x in r.get_json()["blocks"]
             if x["kind"] == "frequency"][0]
        assert "&lt;k" not in b["table"] and "<k" not in b["table"], \
            "the raw sentinel leaked to the page"

    def test_exactness_is_not_the_only_line_on_a_card(self, app, client):
        """An exactness note qualifies figures. With no figures it is a claim
        about nothing, which is how the frequency card used to read."""
        r = client.post("/analysis/run?format=json", json=_suppressing_spec())
        for b in r.get_json()["blocks"]:
            if b["exactness"]:
                assert b["svg"] or b["table"] or b["sentence"], \
                    f"{b['kind']}: exactness attached to an empty card"

    @pytest.mark.parametrize("analyses,kind", [
        ([{"type": "correlation", "vars": ["matvarde", "v2"]}], "correlation"),
        ([{"type": "completeness"}], "completeness"),
    ])
    def test_every_kind_renders_something(self, app, client, analyses, kind):
        spec = _suppressing_spec(analyses=analyses)
        spec["variables"].append({"name": "v2", "from": "x", "agg": "mean"})
        spec["cohort"]["include"][0]["value"] = 0      # widen, not the point
        r = client.post("/analysis/run?format=json", json=spec)
        assert r.status_code == 200
        blocks = [b for b in r.get_json()["blocks"] if b["kind"] == kind]
        assert blocks and (blocks[0]["svg"] or blocks[0]["table"]
                           or blocks[0]["sentence"]), \
            f"{kind} rendered an empty card"


class TestTheNodesExplanationSurvives:
    """#725. combine() read everything from a node run except its notes."""

    def test_a_node_note_reaches_the_page(self, app, client):
        spec = _suppressing_spec(
            index_event={"observation": "x"},
            analyses=[{"type": "over_time", "var": "matvarde",
                       "bin_days": 30, "range_days": [0, 90]}])
        r = client.post("/analysis/run?format=json", json=spec)
        assert r.status_code == 200
        payload = r.get_json()
        assert not payload["blocks"], "expected over_time to yield nothing"
        assert payload["notes"], \
            "the run returned nothing and said nothing about why"
        assert any("over_time" in n for n in payload["notes"])

    def test_one_problem_at_three_sources_is_one_line(self, app, client):
        spec = _suppressing_spec(
            index_event={"observation": "x"},
            analyses=[{"type": "over_time", "var": "matvarde",
                       "bin_days": 30, "range_days": [0, 90]}])
        notes = client.post("/analysis/run?format=json",
                            json=spec).get_json()["notes"]
        over = [n for n in notes if "over_time" in n]
        assert len(over) == 1, f"repeated once per source: {over}"
        assert "Every source" in over[0]

    def test_the_double_counting_warning_is_actually_shown(self, app, client):
        """linkage_sentence was written, tested, and never called."""
        spec = _suppressing_spec(linkage="none")
        notes = client.post("/analysis/run?format=json",
                            json=spec).get_json()["notes"]
        assert any("mer än en gång" in n for n in notes), \
            "linkage: none warned nobody about double counting"


class TestGroupOperatorVocabulary:
    """#726. Two spellings for one concept, and the wrong one was silent."""

    def _grouped(self, op):
        return _suppressing_spec(
            groups=[{"name": "lag", "where": {"matvarde": {op: 19}}},
                    {"name": "hog", "where": {"matvarde": {"gte": 19}}}],
            analyses=[{"type": "compare_groups", "vars": ["matvarde"]}],
            cohort={"include": [
                {"observation": "x", "op": ">=", "value": 0}]})

    @pytest.mark.parametrize("op", ["<", "lt"])
    def test_both_spellings_produce_the_same_analysis(self, app, client, op):
        """The symbol is what a cohort criterion uses, so a spec author will
        write it. It used to validate, be rejected at every node, and take
        compare_groups out of the results with it — silently."""
        r = client.post("/analysis/run?format=json", json=self._grouped(op))
        assert r.status_code == 200
        kinds = [b["kind"] for b in r.get_json()["blocks"]]
        assert "compare_groups" in kinds, \
            f"operator {op!r} lost the analysis entirely"

    def test_an_unknown_operator_is_refused_before_any_data_is_read(
            self, app, client):
        r = client.post("/analysis/run?format=json", json=self._grouped("=<"))
        assert r.status_code == 400
        assert "unknown operator" in r.get_json()["message"]
