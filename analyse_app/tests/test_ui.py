"""#655–#658 — the frontend: question cards, charts, sentences, recipes, i18n.

The design goal these test against, verbatim from the brief: a clinician or
project coordinator WITHOUT statistical training gets from a question to a
correct, readable answer in under two minutes, WITHOUT CHOOSING A STATISTICAL
TEST.
"""
from __future__ import annotations

import pytest

from app.spec import AnalysisSpec
from tests.conftest import make_app
from app.ui import charts, i18n, questions, recipes, sentences
from app.ui.palette import OKABE_ITO, series_style


def _spec(**over):
    base = {
        "title": "t", "purpose": "statistics", "sources": ["cdr1"],
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 1}]},
        "variables": [{"name": "v", "from": "x", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


class TestQuestionCardsPickTheMethod:

    def test_the_user_never_chooses_a_test(self):
        """A user who must pick between Pearson and Spearman has already been
        asked a statistics question, whatever the button says."""
        built = questions.build_analyses("related_continuous", ["a", "b"])
        assert built[0]["type"] == "correlation"
        assert built[0]["method"] == "pearson"      # chosen for them

    def test_two_continuous_variables_never_offer_a_scatter(self):
        """A scatter plots one mark per patient — a per-patient dataset drawn
        as a picture."""
        card = questions.BY_KEY["related_continuous"]
        assert "scatter" not in card.explain_en.lower().split("never")[0]
        assert "never a scatter" in card.explain_en.lower()

    def test_only_answerable_cards_are_offered(self):
        """Offering a card that cannot run and failing afterwards is worse
        than not offering it: the user has already committed."""
        offered = {q.key for q in questions.applicable({"a": "categorical"},
                                                       has_groups=False)}
        assert "related_continuous" not in offered
        assert "frequency" in offered

    def test_compare_is_hidden_without_groups(self):
        offered = {q.key for q in questions.applicable({"a": "continuous"},
                                                       has_groups=False)}
        assert "compare" not in offered
        offered = {q.key for q in questions.applicable({"a": "continuous"},
                                                       has_groups=True)}
        assert "compare" in offered

    def test_every_card_has_both_languages(self):
        for q in questions.CARDS:
            assert q.label_sv and q.label_en
            assert q.explain_sv and q.explain_en

    def test_an_unknown_card_is_a_loud_error(self):
        with pytest.raises(KeyError):
            questions.build_analyses("nope", ["a"])


class TestChartsAreReadableAndSafe:

    def test_a_suppressed_bin_is_visible_not_a_gap(self):
        """A gap reads as 'no patients'; the two mean opposite things."""
        svg = charts.histogram(
            [{"label": "0-1", "count": 20}, {"label": "1-2", "count": "<k"}],
            title="Pain")
        assert "&lt;5" in svg

    def test_every_chart_carries_a_title_and_description(self):
        svg = charts.histogram([{"label": "a", "count": 9}], title="T")
        assert "<title>" in svg and "<desc>" in svg
        assert 'role="img"' in svg

    def test_figures_are_repeated_as_a_table(self):
        """A screen reader gets numbers rather than 'image', and a sighted
        reader who distrusts a picture can check it."""
        html = charts.data_table(["bin", "patients"], [["0-1", 20]],
                                 caption="Pain")
        assert "<caption>" in html and "20" in html

    def test_the_heatmap_replaces_the_scatter(self):
        svg = charts.heatmap([[10, 3], [2, 30]], ["low", "high"],
                             ["no", "yes"], title="X by Y")
        assert svg.startswith("<svg")
        assert "&lt;5" not in svg      # these counts are all real

    def test_a_suppressed_heatmap_cell_shows_as_suppressed(self):
        svg = charts.heatmap([[10, "<k"]], ["r"], ["a", "b"], title="t")
        assert "&lt;5" in svg

    def test_labels_are_escaped(self):
        svg = charts.histogram([{"label": "<script>x</script>", "count": 9}],
                               title="t")
        assert "<script>" not in svg

    def test_colour_never_carries_meaning_alone(self):
        """Roughly one man in twelve cannot separate the first two hues, and
        print is often greyscale."""
        a, b = series_style(0), series_style(1)
        assert a["colour"] != b["colour"]
        assert a["dash"] != b["dash"]

    def test_a_subcohort_keeps_its_colour(self):
        assert series_style(2)["colour"] == OKABE_ITO[2]
        assert series_style(10)["colour"] == OKABE_ITO[2]   # wraps consistently

    def test_an_empty_chart_says_so_rather_than_drawing_nothing(self):
        assert "nothing to show" in charts.histogram([], title="t")


class TestSentencesAreTemplated:

    def test_it_matches_the_briefs_worked_example_shape(self):
        s = sentences.comparison_sentence(
            "TBSA < 20%", "TBSA >= 20%",
            {"difference": 1.3, "ci95": [0.6, 2.0]}, "en")
        assert "1.3" in s and "0.6" in s and "2.0" in s
        assert "higher" in s

    @pytest.mark.parametrize("lang", ["sv", "en"])
    def test_no_sentence_claims_causation(self, lang):
        produced = [
            sentences.describe_sentence(
                "v", {"n": 40, "mean": 1.0, "ci95": [0.5, 1.5]}, lang),
            sentences.comparison_sentence(
                "a", "b", {"difference": 1.0, "ci95": [0.5, 1.5]}, lang),
            sentences.exactness_sentence("approximate", lang),
            sentences.suppression_sentence(5, lang),
        ]
        joined = " ".join(produced).lower()
        for banned in ("effekt", "orsakar", "effect", "causes", "leads to"):
            assert banned not in joined, f"{banned!r} in: {joined}"

    def test_a_comparison_says_it_is_not_a_cause(self):
        s = sentences.comparison_sentence(
            "a", "b", {"difference": 1.0, "ci95": [0.5, 1.5]}, "en")
        assert "not a cause" in s

    def test_effect_size_comes_before_any_p_value(self):
        s = sentences.comparison_sentence(
            "a", "b", {"difference": 1.0, "ci95": [0.5, 1.5],
                       "p_value": 0.01}, "en")
        assert "p" not in s.split("confidence interval")[0].split()[-1].lower()
        assert "1.0" in s

    def test_missing_data_is_mentioned_not_hidden(self):
        s = sentences.describe_sentence(
            "v", {"n": 40, "mean": 1.0, "ci95": [0, 2], "missing": 12}, "en")
        assert "12" in s

    def test_a_suppressed_result_is_described_in_words(self):
        s = sentences.describe_sentence("v", {"suppressed": True}, "en")
        assert "Too few patients" in s

    def test_the_double_counting_warning_appears_for_linkage_none(self):
        assert sentences.linkage_sentence("none", 2, "en")
        assert sentences.linkage_sentence("shared_guid", 2, "en") == ""
        assert sentences.linkage_sentence("none", 1, "en") == ""


class TestI18n:

    def test_swedish_is_the_default(self):
        assert i18n.DEFAULT_LANG == "sv"
        assert i18n.t("step.cohort") == "Urval"

    def test_both_languages_are_complete(self):
        """A half-translated release must fail rather than ship English into
        a Swedish page."""
        assert i18n.missing_keys() == {}

    def test_a_missing_key_returns_the_key_loudly(self):
        """A blank label looks like a design choice; a key in the middle of a
        page does not."""
        assert i18n.t("no.such.key") == "no.such.key"

    def test_the_privacy_banner_states_pseudonyms_are_personal_data(self):
        for lang in ("sv", "en"):
            s = i18n.t("privacy.banner", lang, k=5)
            assert "personuppgifter" in s or "personal data" in s

    def test_there_is_a_live_data_warning(self):
        assert "SKARPA" in i18n.t("mode.live", "sv")
        assert "LIVE" in i18n.t("mode.live", "en")


class TestRecipes:

    def test_a_share_link_carries_only_an_id(self):
        """A cohort definition is potentially identifying, and a URL is
        pasted into chat, logged by proxies and kept in history."""
        r = recipes.make_recipe("My cohort", _spec(title="over 85 at clinic X"),
                                recipe_id="r-123")
        url = r.share_url("https://analyse.pdhc.se")
        assert url.endswith("/recipe/r-123")
        assert "over 85" not in url
        assert "cohort" not in url.split("/recipe/")[1]

    def test_a_recipe_round_trips_to_the_same_spec_hash(self):
        s = _spec()
        r = recipes.make_recipe("n", s, recipe_id="r1")
        from app.spec import spec_hash
        assert r.spec_hash == spec_hash(s)

    def test_suppressed_cells_export_as_suppressed(self):
        """Exporting the real number 'because it is only a CSV' is how a
        suppressed figure reaches a spreadsheet and then a slide."""
        csv_text = recipes.aggregates_csv(
            {"results": [{"kind": "describe",
                          "pooled": {"mean": 4.2, "n": "<k"}}]})
        assert "<k" in csv_text

    def test_a_report_carries_its_provenance(self):
        rows = dict(recipes.provenance_rows({
            "provenance": {"spec_hash": "sha256:abc", "purpose": "statistics",
                           "k_min_applied": 5,
                           "snapshots": {"cdr1": "2026-09-23T10:00:00Z"}},
            "sources": [{"source": "cdr1", "ok": True}]}))
        assert rows["Spec hash"] == "sha256:abc"
        assert rows["Minimum group size applied"] == "5"
        assert "Data from cdr1" in rows

    def test_an_unanswered_source_is_named_in_the_report(self):
        rows = dict(recipes.provenance_rows({
            "provenance": {}, "sources": [
                {"source": "cdr2", "ok": False, "reason": "timeout"}]}))
        assert "timeout" in rows["Source cdr2"]

    def test_the_csv_carries_per_source_as_well_as_pooled(self):
        csv_text = recipes.aggregates_csv({"results": [{
            "kind": "describe", "pooled": {"mean": 1.0},
            "by_source": {"cdr1": {"mean": 2.0}}}]})
        assert "(pooled)" in csv_text and "cdr1" in csv_text


class TestOutputsStayClean:

    def test_no_ui_helper_emits_an_identifier(self):
        from app.testing import assert_clean
        assert_clean({
            "chart": charts.histogram([{"label": "a", "count": 9}], title="t"),
            "sentence": sentences.describe_sentence(
                "v", {"n": 40, "mean": 1.0, "ci95": [0, 2]}),
            "recipe": recipes.make_recipe("n", _spec(), recipe_id="r1").to_json(),
        }, where="ui output")


# --- the researcher landing (#688) ------------------------------------------

def _landing(client):
    r = client.get("/researcher")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_landing_leads_with_what_data_exists():
    """The complaint that prompted this: the page opened on a blank JSON box.

    A predicate written against data that is not there is the commonest way to
    waste an afternoon, so availability comes first and the editor second.
    """
    html = _landing(make_app().test_client())
    assert "Available data" in html
    assert "/api/inventory" in html
    assert html.index("Available data") < html.index("Cohort predicate")


def test_landing_offers_question_shaped_starting_points():
    html = _landing(make_app().test_client())
    assert "Start from a question" in html
    for q in ("Everyone, every source", "Working-age adults", "Older adults"):
        assert q in html


def test_every_starter_is_a_predicate_the_resolver_accepts():
    """Offering a starting point that cannot run is worse than offering none.

    The starters are server-side data (views.COHORT_STARTERS) precisely so this
    can be checked by importing them. The first version defined them as a
    JavaScript literal and this test regex-scraped the rendered page, which
    broke on the first nested brace — a test that can only inspect its subject
    through a regex is testing the regex.
    """
    from app.analyse.cohort import CohortFilter
    from app.routes.views import COHORT_STARTERS

    top = {"cdr_ids", "demographics", "conditions", "medications",
           "type_canonical"}
    demo = {"age_min", "age_max", "sex", "region"}
    assert len(COHORT_STARTERS) >= 4

    for s in COHORT_STARTERS:
        assert s["q"] and s["why"], "a starter needs a question and a reason"
        f = s["filter"]
        assert set(f) <= top, f"unknown key in {s['q']}: {set(f) - top}"
        assert set(f.get("demographics") or {}) <= demo, (
            f"unknown demographics key in {s['q']}: "
            f"{set(f['demographics']) - demo}")
        # The real contract: it has to survive the parser the route uses.
        CohortFilter.from_dict(f)


def test_the_rendered_page_actually_carries_the_starters():
    """Guards the wiring, not just the data — a correct list that never
    reaches the template would pass the test above and ship a blank page."""
    from app.routes.views import COHORT_STARTERS
    html = _landing(make_app().test_client())
    for s in COHORT_STARTERS:
        assert s["q"] in html


def test_landing_does_not_promise_analyses_the_spec_cannot_request():
    """linear_regression and kaplan_meier are in the engine REGISTRY but absent
    from the spec's Analysis union, so no spec can ask for them. The page must
    not advertise them."""
    html = _landing(make_app().test_client())
    assert "linear_regression" not in html
    assert "kaplan_meier" not in html
    assert "regression" not in html.lower() or "survival" not in html.lower()
