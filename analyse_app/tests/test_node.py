"""#648 — the node: policy, read client, spärr and consent exclusion.

The node is where the platform's trust boundary actually sits. These tests
are mostly about REFUSAL: what the node declines, and whether it declines in
the safe direction when something is unavailable.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.node import (
    ConsentUnavailable, NodePolicy, NodeReader, NodeRefusal, PolicyError,
    ReadError, check_data_mode, run_spec,
)
from app.privacy import ProjectKey
from app.privacy.disclosure import DisclosurePolicy
from app.spec import AnalysisSpec

KEY = ProjectKey("proj", b"k" * 32)

POLICY_YAML = """
node_id: cdr1
cdr_base_url: http://127.0.0.1:9046
permitted_purposes: [statistics, quality_registry]
permitted_analyses: [describe, frequency]
k_min: 5
"""


def _policy(text=POLICY_YAML):
    return NodePolicy.load(text, is_text=True)


def _bundle(*resources):
    """A cdr searchset Bundle, in the shape `_bundle_searchset` emits."""
    return {"resourceType": "Bundle", "type": "searchset",
            "entry": [{"resource": r} for r in resources]}


def _obs(patient="p1", concept="c1", value=1.0, unit="L",
         when="2026-05-01T08:00:00Z", org=None,
         system="urn:pdhc:concept"):
    res = {
        "resourceType": "Observation",
        "subject": {"reference": f"Patient/{patient}"},
        "code": {"coding": [{"system": system, "code": concept}]},
        "valueQuantity": {"value": value, "unit": unit},
    }
    if when is not None:
        res["effectiveDateTime"] = when
    if org:
        res["performer"] = [{"identifier": {"value": org}}]
    return res


class _capture:
    """Stand-in for `requests.get` that records the call and replies with a
    Bundle. Unlike a bare Mock it asserts the keyword shape the client uses,
    so a signature change does not pass silently."""

    def __init__(self, *bundles, status=200, text=""):
        self._bundles = list(bundles) or [_bundle()]
        self.status = status
        self.text = text
        self.calls: list[dict] = []

    @property
    def headers(self):
        return self.calls[-1]["headers"]

    @property
    def params(self):
        return self.calls[-1]["params"]

    @property
    def url(self):
        return self.calls[-1]["url"]

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {},
                           "headers": headers or {}})
        body = self._bundles[min(len(self.calls) - 1, len(self._bundles) - 1)]
        outer = self

        class _R:
            status_code = outer.status
            text = outer.text

            @staticmethod
            def json():
                return body
        return _R()


def _spec(**over):
    base = {
        "title": "t", "purpose": "statistics", "sources": ["cdr1"],
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 1}]},
        "variables": [{"name": "v", "from": "x", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


class TestPolicyLoading:

    def test_a_misspelled_key_fails_loudly(self):
        """A typo in a policy file is a control the organisation believes it
        has and does not."""
        with pytest.raises(PolicyError, match="unknown policy keys"):
            _policy("node_id: a\ncdr_base_url: u\nk_minimum: 5\n")

    def test_an_unknown_purpose_is_refused_at_load(self):
        with pytest.raises(PolicyError, match="unknown purposes"):
            _policy("node_id: a\ncdr_base_url: u\npermitted_purposes: [care]\n")

    def test_no_stated_purposes_means_the_node_answers_nothing(self):
        """The correct posture for a policy someone forgot to fill in."""
        p = _policy("node_id: a\ncdr_base_url: u\n")
        with pytest.raises(PolicyError, match="does not permit purpose"):
            p.check(_spec())

    def test_malformed_yaml_does_not_default_to_permissive(self):
        with pytest.raises(PolicyError):
            _policy("node_id: [unclosed\n")


class TestPolicyEnforcement:

    def test_a_forbidden_purpose_is_refused(self):
        with pytest.raises(PolicyError, match="does not permit purpose"):
            _policy().check(_spec(purpose="research"))

    def test_a_forbidden_analysis_type_is_refused(self):
        with pytest.raises(PolicyError, match="does not permit analysis"):
            _policy().check(_spec(
                variables=[{"name": "a", "from": "x", "agg": "mean"},
                           {"name": "b", "from": "y", "agg": "mean"}],
                analyses=[{"type": "correlation", "vars": ["a", "b"]}]))

    def test_the_coordinator_cannot_lower_k_min(self):
        """A policy a coordinator could relax would be a suggestion."""
        node = _policy("node_id: a\ncdr_base_url: u\nk_min: 20\n"
                       "permitted_purposes: [statistics]\n")
        got = node.disclosure(DisclosurePolicy(k_min=5))
        assert got.k_min == 20

    def test_the_coordinator_can_ask_for_stricter(self):
        node = _policy()
        assert node.disclosure(DisclosurePolicy(k_min=25, floor=25)).k_min == 25

    def test_may_use_other_orgs_rows_defaults_to_no(self):
        """Answerable at all only since cdr #665 added author_org_guid."""
        assert _policy().may_use_other_orgs_rows is False


class TestDataMode:

    def test_synthetic_is_the_default(self):
        assert _policy().data_mode == "synthetic"

    def test_live_requires_a_deliberate_act(self):
        live = _policy("node_id: a\ncdr_base_url: u\ndata_mode: live\n"
                       "permitted_purposes: [statistics]\n")
        with pytest.raises(NodeRefusal, match="requires an explicit allow_live"):
            check_data_mode(live)
        check_data_mode(live, allow_live=True)     # must not raise

    def test_an_unknown_mode_is_refused(self):
        with pytest.raises(PolicyError, match="data_mode must be"):
            _policy("node_id: a\ncdr_base_url: u\ndata_mode: production\n")


class TestFailClosed:

    def _reader(self):
        return NodeReader(base_url="http://cdr", service_key="k")

    def test_spärr_unavailable_blocks_everyone_not_nobody(self):
        r = self._reader()
        with patch("app.node.reader.requests.post",
                   side_effect=Exception("ips down")):
            with pytest.raises(ConsentUnavailable, match="treated as blocked"):
                r.excluded_by_spärr(["p1"], "http://ips")

    # #823: these three used to patch `requests.post` and assert on the
    # HEADERS. They passed for as long as the client called an endpoint that
    # returns 404 on every CDR, because a mock answers any URL. Mocking the
    # transport verb tests the verb; the thing that was wrong was the PATH.
    # `TestTheReadEndpointActuallyExists` below pins that instead.

    def test_a_503_from_the_cdr_fails_closed(self):
        """cdr returns 503 when the consent filter cannot answer. A missing
        verdict is not a reason to proceed with unfiltered data."""
        r = self._reader()

        class _R:
            status_code = 503
            text = ""
        with patch("app.node.reader.requests.get", return_value=_R()):
            with pytest.raises(ConsentUnavailable, match="fails closed"):
                r.read_observations(purpose="statistics", patient_guids=["p1"])

    def test_the_purpose_header_is_actually_sent(self):
        """Without it the CDR passes machine callers through unfiltered —
        which for an analysis node means reading data a patient objected to."""
        captured = _capture()
        with patch("app.node.reader.requests.get", side_effect=captured):
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p1"])
        assert captured.headers["X-Access-Purpose"] == "statistics"

    def test_research_sends_its_project_guids(self):
        captured = _capture()
        with patch("app.node.reader.requests.get", side_effect=captured):
            self._reader().read_observations(
                purpose="research", patient_guids=["p1"],
                research_projects=["proj-1", "proj-2"])
        assert captured.headers["X-Research-Project-Guids"] == "proj-1,proj-2"


class TestRunOrdering:

    class FakeReader:
        def __init__(self, rows, blocked=()):
            self.rows = rows
            self.blocked = set(blocked)
            self.asked_for = None

        def excluded_by_spärr(self, guids, ips_base_url, **kw):
            return {g for g in guids if g in self.blocked}

        def read_observations(self, *, purpose, patient_guids, **kw):
            self.asked_for = sorted(patient_guids)
            return [r for r in self.rows
                    if r["patient_guid"] in set(patient_guids)]

    def _rows(self, n, start=0):
        # `concept` is what cohort criteria select on (#696); real rows carry
        # it and this fixture did not, which is part of why nothing noticed
        # that spec.cohort was never applied.
        return [{"patient_guid": f"p{i}", "concept": "x", "value": float(i)}
                for i in range(start, start + n)]

    def test_blocked_patients_are_never_read_at_all(self):
        """Not filtered after: an aggregate computed over a blocked patient
        has used their data even if the number is discarded."""
        reader = self.FakeReader(self._rows(20), blocked={"p3", "p7"})
        run = run_spec(_spec(), _policy(), reader, project_key=KEY,
                       ips_base_url="http://ips",
                       cohort=[f"p{i}" for i in range(20)])
        assert "p3" not in reader.asked_for
        assert "p7" not in reader.asked_for
        assert run.excluded["blocked"] == 2

    def test_consent_withholding_is_counted(self):
        reader = self.FakeReader(self._rows(10))      # CDR returns only 10
        run = run_spec(_spec(), _policy(), reader, project_key=KEY,
                       ips_base_url="http://ips",
                       cohort=[f"p{i}" for i in range(20)])
        assert run.excluded["consent"] == 10

    def test_the_run_returns_partials_not_rows(self):
        reader = self.FakeReader(self._rows(30))
        run = run_spec(_spec(), _policy(), reader, project_key=KEY,
                       ips_base_url="http://ips",
                       cohort=[f"p{i}" for i in range(30)])
        blob = repr(run.to_json())
        assert run.partials
        assert "patient_guid" not in blob
        assert "p17" not in blob

    def test_an_empty_eligible_cohort_returns_early_and_says_so(self):
        reader = self.FakeReader(self._rows(5),
                                 blocked={f"p{i}" for i in range(5)})
        run = run_spec(_spec(), _policy(), reader, project_key=KEY,
                       ips_base_url="http://ips",
                       cohort=[f"p{i}" for i in range(5)])
        assert run.partials == []
        assert any("could be read" in n for n in run.notes)

    def test_a_no_pool_node_says_so_in_its_result(self):
        pol = _policy("node_id: cdr1\ncdr_base_url: u\nmay_pool: false\n"
                      "permitted_purposes: [statistics]\n")
        reader = self.FakeReader(self._rows(20))
        run = run_spec(_spec(), pol, reader, project_key=KEY,
                       ips_base_url="http://ips",
                       cohort=[f"p{i}" for i in range(20)])
        assert run.may_pool is False
        assert any("does not permit its rows to be pooled" in n
                   for n in run.notes)

    def test_policy_is_checked_before_anything_is_read(self):
        reader = self.FakeReader(self._rows(20))
        with pytest.raises(PolicyError):
            run_spec(_spec(purpose="research"), _policy(), reader,
                     project_key=KEY, ips_base_url="http://ips",
                     cohort=["p1"])
        assert reader.asked_for is None


class TestCohortIsAppliedAtTheNode:
    """#696 — the candidate list is candidates, not the cohort."""

    def test_the_candidate_list_is_narrowed_by_the_criteria(self):
        from app.node import run_spec
        from tests.test_node import _policy, _spec, KEY        # noqa: F401
        rows = [{"patient_guid": f"p{i}", "concept": "x", "value": float(i)}
                for i in range(10)]

        class R:
            def excluded_by_spärr(self, guids, ips_base_url, **kw):
                return set()

            def read_observations(self, *, purpose, patient_guids, **kw):
                want = set(patient_guids)
                return [r for r in rows if r["patient_guid"] in want]

        # _spec()'s criterion is x >= 1, so p0 (value 0.0) must drop out.
        run = run_spec(_spec(), _policy(), R(), project_key=KEY,
                       ips_base_url="http://ips",
                       cohort=[f"p{i}" for i in range(10)])
        assert run.n_patients == 9
        assert run.excluded["cohort"] == 1


class TestTheReadEndpointActuallyExists:
    """#823 — the client called `POST /api/v1/observations/search`, which no
    CDR serves (404 on cdr2 and cdr3, measured 2026-10-13) and which exists
    nowhere in cdr.pdhc's tree.

    The old tests could not catch it: they patched `requests.post` and
    asserted on headers, and a mock answers any URL. So these assert on the
    VERB, the PATH and the PARAMETER SPELLINGS instead — the three things
    that have to match a service in another repo, which is the class of bug
    static analysis inside one repo structurally cannot find (#708).
    """

    def _reader(self):
        return NodeReader(base_url="http://cdr", service_key="k")

    def test_it_GETs_the_fhir_Observation_search(self):
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p1"])
        assert cap.url == "http://cdr/api/v1/fhir/Observation", (
            "this is the only bulk observation read a CDR serves")

    def test_nothing_still_posts_to_the_endpoint_that_does_not_exist(self):
        """A regression guard with teeth: if any read path reverts to the
        invented endpoint, `requests.post` is called and this fails."""
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap), \
             patch("app.node.reader.requests.post") as posted:
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p1"])
        posted.assert_not_called()

    def test_the_cohort_is_narrowed_SERVER_side_ONE_PATIENT_PER_REQUEST(self):
        """The ordering argument depends on the narrowing happening at the
        CDR: the node excludes spärr-blocked patients BEFORE the read, so the
        CDR must never be asked for rows it would then have to hand back.

        One patient per request, because a comma-separated list returns ZERO
        rows on the deployed cdr1 and cdr2 (measured 2026-10-14) — cdr.pdhc's
        tree accepts one, that code is deployed nowhere, and a client that
        assumed it would read nothing at all."""
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p2", "p1", "p1"])
        assert len(cap.calls) == 2, "deduplicated, one request each"
        assert sorted(c["params"]["patient"] for c in cap.calls) == ["p1", "p2"]
        assert not any("," in c["params"]["patient"] for c in cap.calls), (
            "a comma-separated patient list reads nothing on the deployed "
            "CDRs")

    def test_every_patient_in_a_large_cohort_is_actually_asked_about(self):
        guids = [f"p{i:05d}" for i in range(37)]
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            self._reader().read_observations(
                purpose="statistics", patient_guids=guids)
        assert sorted(c["params"]["patient"] for c in cap.calls) == sorted(guids)

    def test_the_fan_out_is_BOUNDED(self):
        """An unbounded pool against a sibling is a denial of service with
        extra steps — the same reason `excluded_by_spärr` bounds its own."""
        from app.node.reader import MAX_CONCURRENCY
        assert 1 <= MAX_CONCURRENCY <= 8

    def test_the_concept_is_sent_in_the_PIPED_form_not_the_stored_uri(self):
        """The asymmetry that cost a wrong conclusion in #823.
        `code=<system>|<guid>` is an indexed equality against the stored
        `urn:pdhc:concept/<guid>`. `code=urn:pdhc:concept/<guid>` — the
        obvious thing to send — matches NOTHING, because cdr reads a value
        with no pipe as a bare code and applies `LIKE '%/<value>'`."""
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p1"], concepts=["abc"])
        assert cap.params["code"] == "urn:pdhc:concept|abc"
        assert "urn:pdhc:concept/abc" not in str(cap.params.values())

    def test_each_concept_gets_its_OWN_request(self):
        """cdr's `code` is single-valued: a repeated `code=` is read as one of
        them, which would narrow to one concept while reporting on several."""
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p1"],
                concepts=["a", "b"])
        assert sorted(c["params"]["code"] for c in cap.calls) == [
            "urn:pdhc:concept|a", "urn:pdhc:concept|b"]

    def test_the_requests_are_the_cross_product_of_patients_and_concepts(self):
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p1", "p2"],
                concepts=["a", "b"])
        assert sorted((c["params"]["patient"], c["params"]["code"])
                      for c in cap.calls) == [
            ("p1", "urn:pdhc:concept|a"), ("p1", "urn:pdhc:concept|b"),
            ("p2", "urn:pdhc:concept|a"), ("p2", "urn:pdhc:concept|b")]

    def test_it_asks_for_cdrs_full_page_because_there_is_no_offset(self):
        from app.node.reader import MAX_COUNT
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            self._reader().read_observations(
                purpose="statistics", patient_guids=["p1"])
        assert cap.params["_count"] == MAX_COUNT


class TestTheReadRefusesToBeQuietlyWrong:

    def _reader(self):
        return NodeReader(base_url="http://cdr", service_key="k")

    def test_a_FULL_page_is_a_truncated_read_and_raises(self):
        """cdr has no offset parameter and `Bundle.total` reports the PAGE
        SIZE, not the match count — so a full page is indistinguishable from
        a complete answer. A figure over an unknown fraction of a cohort
        reads exactly like a real figure, which is why this refuses."""
        from app.node.reader import MAX_COUNT
        full = _bundle(*[_obs(patient="p1") for _ in range(MAX_COUNT)])
        cap = _capture(full)
        with patch("app.node.reader.requests.get", side_effect=cap):
            with pytest.raises(ReadError, match="truncated read"):
                self._reader().read_observations(
                    purpose="statistics", patient_guids=["p1"])

    def test_a_row_outside_the_allowlist_RAISES_rather_than_being_dropped(self):
        """cdr ignores an unknown query parameter SILENTLY, so "the server
        narrowed by patient" is an assumption until the rows are inspected.
        If it did not narrow, the node is holding rows for patients it
        excluded — possibly spärr-blocked — and that must not be
        discoverable only by its absence from the output."""
        cap = _capture(_bundle(_obs(patient="p1"), _obs(patient="INTRUDER")))
        with patch("app.node.reader.requests.get", side_effect=cap):
            with pytest.raises(ReadError, match="not asked about"):
                self._reader().read_observations(
                    purpose="statistics", patient_guids=["p1"])

    def test_a_400_is_reported_not_swallowed(self):
        cap = _capture(status=400, text="value-quantity: bad prefix")
        with patch("app.node.reader.requests.get", side_effect=cap):
            with pytest.raises(ReadError, match="refused this read"):
                self._reader().read_observations(
                    purpose="statistics", patient_guids=["p1"])

    def test_an_empty_cohort_makes_NO_request_at_all(self):
        cap = _capture()
        with patch("app.node.reader.requests.get", side_effect=cap):
            assert self._reader().read_observations(
                purpose="statistics", patient_guids=[]) == []
        assert cap.calls == []


class TestTheBundleMapsToTheRowShapeTheNodeProjectsFrom:

    def _rows(self, *resources, **kw):
        cap = _capture(_bundle(*resources))
        with patch("app.node.reader.requests.get", side_effect=cap):
            return NodeReader(base_url="http://cdr", service_key="k"
                              ).read_observations(
                purpose="statistics",
                patient_guids=kw.pop("patient_guids", ["p1"]), **kw)

    def test_the_five_fields_frame_and_projection_read(self):
        row = self._rows(_obs(value=7.5, unit="mmol/L", org="org-a"))[0]
        assert row["patient_guid"] == "p1"
        assert row["concept"] == "c1"
        assert row["value"] == 7.5
        assert row["unit"] == "mmol/L"
        assert row["effective_at"] == "2026-05-01T08:00:00Z"
        assert row["meta"]["author_org"] == "org-a"

    def test_the_concept_is_the_BARE_guid_not_the_canonical_uri(self):
        """`node/frame.py` matches `row["concept"] == var.from_`, and a spec's
        `from` names the concept, not the URI it is stored under."""
        assert self._rows(_obs(concept="abc"))[0]["concept"] == "abc"

    def test_the_legacy_concept_spelling_is_also_read(self):
        """gateway dual-emits both during the #294 alias window, so a row
        carrying only the URL form must not lose its concept."""
        row = self._rows(_obs(
            concept="abc",
            system="https://plan.pdhc.se/api/v1/concepts"))[0]
        assert row["concept"] == "abc"

    def test_a_PDHC_coding_wins_over_an_external_one_regardless_of_order(self):
        """Otherwise a row's concept depends on the producer's array order,
        which is the #787.2 defect one layer up."""
        res = _obs(concept="abc")
        res["code"]["coding"].insert(0, {"system": "http://loinc.org",
                                         "code": "4548-4"})
        assert self._rows(res)[0]["concept"] == "abc"

    def test_a_row_with_no_performer_stays_ABSENT_not_filled_in(self):
        """cdr's attested `author_org_guid` (#665) lives on clinical_context
        and is not in this resource, so `performer` is a declaration. Absent
        must read as "(not recorded)" in completeness, not as a guess."""
        assert "meta" not in self._rows(_obs(org=None))[0]

    def test_an_undated_observation_is_kept_with_no_date(self):
        """It still counts toward a mean; it just cannot be placed on a time
        axis. Dropping it would understate n without saying so."""
        rows = self._rows(_obs(when=None))
        assert len(rows) == 1 and rows[0]["effective_at"] is None

    def test_an_effective_PERIOD_is_placed_at_its_START(self):
        res = _obs(when=None)
        res["effectivePeriod"] = {"start": "2026-05-01", "end": "2026-05-09"}
        assert self._rows(res)[0]["effective_at"] == "2026-05-01"

    def test_a_non_quantity_value_still_arrives(self):
        res = _obs()
        del res["valueQuantity"]
        res["valueString"] = "positive"
        row = self._rows(res)[0]
        assert row["value"] == "positive" and row["unit"] is None
