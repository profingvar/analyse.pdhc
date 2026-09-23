"""#654 — audit for a group run, and the node policy file."""
from __future__ import annotations

import pytest

from app.models.audit import AnalyseAudit
from app.node import NodePolicy
from app.privacy.disclosure import SUPPRESSED, DisclosurePolicy
from app.services.run_audit import record_run, runs_for_spec
from app.spec import AnalysisSpec, spec_hash

P = DisclosurePolicy()


def _spec(**over):
    base = {
        "title": "Pain in the home phase", "purpose": "quality_registry",
        "sources": ["cdr1", "cdr2"],
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 1}]},
        "variables": [{"name": "v", "from": "x", "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


class TestRunAudit:

    def test_a_run_is_identified_by_its_spec_not_a_patient(self, app):
        with app.app_context():
            s = _spec()
            row = record_run(user_guid="u1", user_org_guids=["o1"], spec=s,
                             sources={"cdr1": 40, "cdr2": 30}, policy=P)
            assert row.patient_guid is None
            assert row.spec_hash == spec_hash(s)
            assert row.event_type == "analysis_run"

    def test_small_per_source_counts_are_suppressed_in_the_log(self, app):
        """An audit log is read by more people than a result is."""
        with app.app_context():
            row = record_run(user_guid="u1", user_org_guids=[], spec=_spec(),
                             sources={"cdr1": 40, "cdr2": 3}, policy=P)
            snap = row.payload_snapshot["patients_per_source"]
            assert snap["cdr1"] == 40
            assert snap["cdr2"] == SUPPRESSED

    def test_the_purpose_is_recorded(self, app):
        with app.app_context():
            row = record_run(user_guid="u1", user_org_guids=[], spec=_spec(),
                             sources={"cdr1": 40}, policy=P)
            assert row.payload_snapshot["purpose"] == "quality_registry"

    def test_the_applied_threshold_is_recorded(self, app):
        with app.app_context():
            row = record_run(user_guid="u1", user_org_guids=[], spec=_spec(),
                             sources={"cdr1": 40},
                             policy=DisclosurePolicy(k_min=25, floor=25))
            assert row.payload_snapshot["k_min_applied"] == 25

    def test_a_node_writes_its_own_row(self, app):
        """The point of the second log: an organisation whose rows sit in
        another organisation's CDR can still see its data being used."""
        with app.app_context():
            row = record_run(user_guid="u1", user_org_guids=[], spec=_spec(),
                             sources={"cdr1": 40}, policy=P,
                             node_id="cdr_uppsala")
            assert row.route == "analysis:node"
            assert row.payload_snapshot["node_id"] == "cdr_uppsala"

    def test_runs_can_be_found_by_spec(self, app):
        """The question an auditor actually asks."""
        with app.app_context():
            s = _spec(title="a distinctive title for this test")
            record_run(user_guid="u1", user_org_guids=[], spec=s,
                       sources={"cdr1": 40}, policy=P)
            found = runs_for_spec(spec_hash(s))
            assert len(found) == 1
            assert found[0]["payload_snapshot"]["title"] == s.title

    def test_a_refused_run_is_still_logged(self, app):
        """A refusal is a fact about who tried to read what."""
        with app.app_context():
            row = record_run(user_guid="u1", user_org_guids=[], spec=_spec(),
                             sources={}, policy=P, response_status=403)
            assert row.response_status == 403

    def test_the_audit_row_carries_no_identifier(self, app):
        from app.testing import assert_clean
        with app.app_context():
            row = record_run(user_guid="u1", user_org_guids=[], spec=_spec(),
                             sources={"cdr1": 40}, policy=P)
            assert_clean(row.payload_snapshot, where="audit payload")


class TestPolicyFileFormat:

    def test_the_documented_example_loads(self):
        """The file in docs/analyse/node-policy.md must actually parse, or
        the documentation is fiction."""
        p = NodePolicy.load("""
node_id: cdr_uppsala
cdr_base_url: http://127.0.0.1:9046
permitted_purposes: [statistics, quality_registry]
permitted_analyses: [describe, frequency, completeness]
k_min: 5
may_pool: true
may_use_other_orgs_rows: false
data_mode: synthetic
""", is_text=True)
        assert p.node_id == "cdr_uppsala"
        assert p.permitted_analyses == frozenset(
            {"describe", "frequency", "completeness"})

    def test_absent_permitted_analyses_means_all_of_them(self):
        p = NodePolicy.load("node_id: a\ncdr_base_url: u\n"
                            "permitted_purposes: [statistics]\n", is_text=True)
        assert "correlation" in p.permitted_analyses
