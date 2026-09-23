"""#644 — the analysis spec: models, validation, canonicalisation, hashing.

The spec is the only thing a node executes, so its validation is the last
place a malformed question can be caught cheaply. After this it costs a read.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.spec import (
    AnalysisSpec, Linkage, Purpose, canonical_json, json_schema, provenance,
    spec_hash,
)


# The brief's worked example, with its `purpose` corrected to the platform's
# closed enum — the brief says `quality_followup`, which is not a real value
# anywhere in PDHC (DISCOVERY.md gap G6).
BRIEF_EXAMPLE = {
    "spec_version": 1,
    "title": "Home phase after burn injury, by injury size",
    "purpose": "quality_registry",
    "sources": ["cdr_uppsala", "cdr_ostergotland"],
    "linkage": "none",
    "index_event": {"observation": "injury_date"},
    "cohort": {"include": [
        {"observation": "tbsa_percent", "op": ">=", "value": 5},
        {"age_band": {"from": 18}},
    ]},
    "variables": [
        {"name": "tbsa", "from": "tbsa_percent", "agg": "first"},
        {"name": "pain_w1_4", "from": "pain_nrs", "agg": "mean",
         "window_days": [0, 28]},
        {"name": "n_reports", "from": "patient_reported", "agg": "count"},
        {"name": "sex", "from": "demographics.sex"},
        {"name": "site", "from": "meta.author_org"},
    ],
    "groups": [
        {"name": "TBSA < 20%", "where": {"tbsa": {"lt": 20}}},
        {"name": "TBSA >= 20%", "where": {"tbsa": {"gte": 20}}},
    ],
    "analyses": [
        {"type": "describe", "vars": ["tbsa", "pain_w1_4", "n_reports", "sex"]},
        {"type": "histogram", "var": "pain_w1_4",
         "bins": {"width": 1, "range": [0, 10]}},
        {"type": "frequency", "vars": ["sex", "site"]},
        {"type": "correlation", "vars": ["tbsa", "pain_w1_4", "n_reports"],
         "method": "pearson"},
        {"type": "compare_groups", "vars": ["pain_w1_4", "n_reports", "sex"]},
    ],
}


def _minimal(**over):
    base = {
        "title": "t", "purpose": "statistics", "sources": ["cdr1"],
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 1}]},
        "variables": [{"name": "v", "from": "x", "agg": "first"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    }
    base.update(over)
    return base


class TestTheBriefsExample:

    def test_it_parses(self):
        spec = AnalysisSpec.model_validate(BRIEF_EXAMPLE)
        assert spec.purpose is Purpose.quality_registry
        assert spec.linkage is Linkage.none
        assert len(spec.analyses) == 5

    def test_meta_author_org_is_a_usable_source(self):
        """`meta.author_org` is the field cdr #665 added. A variable reading
        it takes no agg — it is one value per patient, not a series."""
        spec = AnalysisSpec.model_validate(BRIEF_EXAMPLE)
        site = next(v for v in spec.variables if v.name == "site")
        assert site.from_ == "meta.author_org"
        assert site.agg is None

    def test_it_round_trips_through_canonical_json(self):
        spec = AnalysisSpec.model_validate(BRIEF_EXAMPLE)
        again = AnalysisSpec.model_validate(json.loads(canonical_json(spec)))
        assert spec_hash(again) == spec_hash(spec)


class TestPurposeIsThePlatformEnum:

    @pytest.mark.parametrize("p", ["research", "statistics", "quality_registry"])
    def test_secondary_purposes_accepted(self, p):
        assert AnalysisSpec.model_validate(_minimal(purpose=p)).purpose.value == p

    @pytest.mark.parametrize("p", ["care", "care_coordination",
                                   "patient_access", "administration"])
    def test_primary_use_purposes_rejected(self, p):
        """Analysis reads for secondary use. `administration` in particular is
        never blocked by ips, so accepting it would make `purpose` a way
        around consent rather than a way of declaring it."""
        with pytest.raises(ValidationError):
            AnalysisSpec.model_validate(_minimal(purpose=p))

    def test_the_briefs_own_value_is_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisSpec.model_validate(_minimal(purpose="quality_followup"))


class TestHashStability:

    def test_key_order_does_not_change_the_hash(self):
        a = AnalysisSpec.model_validate(BRIEF_EXAMPLE)
        shuffled = dict(reversed(list(BRIEF_EXAMPLE.items())))
        b = AnalysisSpec.model_validate(shuffled)
        assert spec_hash(a) == spec_hash(b)

    def test_stating_a_default_equals_relying_on_it(self):
        """Otherwise changing a default later would silently re-hash every
        stored spec, and old results could no longer be traced."""
        without = AnalysisSpec.model_validate(_minimal())
        with_it = AnalysisSpec.model_validate(
            _minimal(linkage="shared_guid", allow_overlapping_groups=False))
        assert spec_hash(without) == spec_hash(with_it)

    def test_a_real_change_changes_the_hash(self):
        a = AnalysisSpec.model_validate(_minimal())
        b = AnalysisSpec.model_validate(_minimal(title="different question"))
        assert spec_hash(a) != spec_hash(b)

    def test_hash_is_stable_across_processes(self):
        """PYTHONHASHSEED randomises dict/set iteration per process. A hash
        that depended on it would be reproducible only within one run —
        exactly when nobody needs it."""
        code = (
            "import json,sys;sys.path.insert(0,'.');"
            "from app.spec import AnalysisSpec, spec_hash;"
            "print(spec_hash(AnalysisSpec.model_validate(json.load(sys.stdin))))"
        )
        outs = set()
        for seed in ("0", "1", "random"):
            r = subprocess.run([sys.executable, "-c", code],
                               input=json.dumps(BRIEF_EXAMPLE), text=True,
                               capture_output=True,
                               env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"})
            assert r.returncode == 0, r.stderr
            outs.add(r.stdout.strip())
        assert len(outs) == 1, f"hash varied across processes: {outs}"

    def test_hash_is_prefixed_with_its_algorithm(self):
        assert spec_hash(AnalysisSpec.model_validate(_minimal())).startswith("sha256:")


class TestValidation:

    def test_unknown_key_is_an_error_not_ignored(self):
        """A silently dropped field is a figure computed from something other
        than what the analyst wrote."""
        with pytest.raises(ValidationError):
            AnalysisSpec.model_validate(_minimal(bins_widht=3))

    def test_analysis_referencing_an_unknown_variable(self):
        with pytest.raises(ValidationError, match="unknown variable"):
            AnalysisSpec.model_validate(_minimal(
                analyses=[{"type": "describe", "vars": ["nope"]}]))

    def test_group_referencing_an_unknown_variable(self):
        with pytest.raises(ValidationError, match="unknown variable"):
            AnalysisSpec.model_validate(_minimal(
                groups=[{"name": "g", "where": {"nope": {"lt": 1}}}]))

    def test_duplicate_variable_names(self):
        with pytest.raises(ValidationError, match="duplicate variable"):
            AnalysisSpec.model_validate(_minimal(variables=[
                {"name": "v", "from": "x", "agg": "first"},
                {"name": "v", "from": "y", "agg": "last"},
            ]))

    def test_compare_groups_needs_two_groups(self):
        with pytest.raises(ValidationError, match="at least two groups"):
            AnalysisSpec.model_validate(_minimal(
                groups=[{"name": "only", "where": {"v": {"lt": 1}}}],
                analyses=[{"type": "compare_groups", "vars": ["v"]}]))

    def test_window_days_requires_an_index_event(self):
        """Day offsets have no zero point without one."""
        with pytest.raises(ValidationError, match="index_event is required"):
            AnalysisSpec.model_validate(_minimal(variables=[
                {"name": "v", "from": "x", "agg": "mean",
                 "window_days": [0, 28]}]))

    def test_over_time_requires_an_index_event(self):
        with pytest.raises(ValidationError, match="index_event is required"):
            AnalysisSpec.model_validate(_minimal(analyses=[
                {"type": "over_time", "var": "v", "bin_days": 7,
                 "range_days": [0, 90]}]))

    def test_series_variable_must_say_how_it_collapses(self):
        with pytest.raises(ValidationError, match="agg is required"):
            AnalysisSpec.model_validate(_minimal(
                variables=[{"name": "v", "from": "x"}]))

    def test_flat_variable_rejects_an_agg(self):
        with pytest.raises(ValidationError, match="does not apply"):
            AnalysisSpec.model_validate(_minimal(variables=[
                {"name": "v", "from": "demographics.sex", "agg": "mean"}]))

    def test_reversed_window_is_rejected(self):
        with pytest.raises(ValidationError, match="start must not exceed end"):
            AnalysisSpec.model_validate(_minimal(
                index_event={"observation": "ix"},
                variables=[{"name": "v", "from": "x", "agg": "mean",
                            "window_days": [28, 0]}]))

    def test_duplicate_sources_rejected(self):
        with pytest.raises(ValidationError, match="duplicate source"):
            AnalysisSpec.model_validate(_minimal(sources=["cdr1", "cdr1"]))

    def test_overlapping_groups_are_opt_in(self):
        """The flag must be stated in the spec; the engine enforces it at run
        time. Defaulting to allowed would silently break every comparison."""
        assert AnalysisSpec.model_validate(_minimal()).allow_overlapping_groups is False


class TestSchemaAndProvenance:

    def test_schema_is_generated_and_identified(self):
        s = json_schema()
        assert s["$id"].endswith("analysis-spec-v1.json")
        assert "purpose" in s["properties"]

    def test_schema_serialises(self):
        json.dumps(json_schema())

    def test_provenance_carries_what_reproduction_needs(self):
        spec = AnalysisSpec.model_validate(BRIEF_EXAMPLE)
        p = provenance(spec, coordinator_version="1.2.3",
                       node_versions={"cdr_uppsala": "1.2.3"},
                       snapshots={"cdr_uppsala": "2026-09-23T10:00:00Z"})
        assert p["spec_hash"] == spec_hash(spec)
        assert p["purpose"] == "quality_registry"
        assert p["coordinator_version"] == "1.2.3"
        assert p["snapshots"]["cdr_uppsala"].startswith("2026")

    def test_snapshots_are_per_source_not_global(self):
        """Federated sources are read at different moments; one timestamp
        across all of them would be a fiction."""
        spec = AnalysisSpec.model_validate(BRIEF_EXAMPLE)
        p = provenance(spec, coordinator_version="1",
                       snapshots={"cdr_uppsala": "2026-09-23T10:00:00Z",
                                  "cdr_ostergotland": "2026-09-23T10:04:00Z"})
        assert len(set(p["snapshots"].values())) == 2
