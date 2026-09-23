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
    check_data_mode, run_spec,
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

    def test_a_503_from_the_cdr_fails_closed(self):
        """cdr returns 503 when the consent filter cannot answer. A missing
        verdict is not a reason to proceed with unfiltered data."""
        r = self._reader()

        class _R:
            status_code = 503
            text = ""
        with patch("app.node.reader.requests.post", return_value=_R()):
            with pytest.raises(ConsentUnavailable, match="fails closed"):
                r.read_observations(purpose="statistics", patient_guids=["p1"])

    def test_the_purpose_header_is_actually_sent(self):
        """Without it the CDR passes machine callers through unfiltered —
        which for an analysis node means reading data a patient objected to."""
        r = self._reader()
        captured = {}

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"items": []}

        def _post(url, json=None, headers=None, timeout=None):
            captured.update(headers or {})
            return _R()

        with patch("app.node.reader.requests.post", side_effect=_post):
            r.read_observations(purpose="statistics", patient_guids=["p1"])
        assert captured["X-Access-Purpose"] == "statistics"

    def test_research_sends_its_project_guids(self):
        r = self._reader()
        captured = {}

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"items": []}

        def _post(url, json=None, headers=None, timeout=None):
            captured.update(headers or {})
            return _R()

        with patch("app.node.reader.requests.post", side_effect=_post):
            r.read_observations(purpose="research", patient_guids=["p1"],
                                research_projects=["proj-1", "proj-2"])
        assert captured["X-Research-Project-Guids"] == "proj-1,proj-2"


class TestRunOrdering:

    class FakeReader:
        def __init__(self, rows, blocked=()):
            self.rows = rows
            self.blocked = set(blocked)
            self.asked_for = None

        def excluded_by_spärr(self, guids, ips_base_url):
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
            def excluded_by_spärr(self, guids, ips_base_url):
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
