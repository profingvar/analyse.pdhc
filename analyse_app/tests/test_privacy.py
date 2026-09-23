"""#645 — the privacy layer.

The boundary that decides what can physically leave a node. The acceptance
test the ticket names is TestNothingForbiddenSurvives: a record carrying every
forbidden field type, asserted absent from the projection.

No fixture here contains a real-looking personnummer, name or address. The
forbidden values are obvious sentinels, so that if one ever DOES leak into an
output the scanner (AN-7) sees something unmistakable rather than something
that merely looks like test data.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.privacy import (
    NEVER_PROJECTABLE, CoarsenError, ProjectKey, ProjectionError, TimeGrain,
    age_band, build_allowlist, calendar, coarsen_record, day_offset, project,
    pseudonymise, pseudonymise_all,
)
from app.privacy.coarsen import birth_date
from app.spec import AnalysisSpec

KEY_A = ProjectKey("proj-a", b"k" * 32)
KEY_B = ProjectKey("proj-b", b"j" * 32)


def _spec(**over):
    base = {
        "title": "t", "purpose": "statistics", "sources": ["cdr1"],
        "cohort": {"include": [{"observation": "x", "op": ">=", "value": 1}]},
        "variables": [
            {"name": "v", "from": "x", "agg": "mean"},
            {"name": "sex", "from": "demographics.sex"},
            {"name": "site", "from": "meta.author_org"},
        ],
        "analyses": [{"type": "describe", "vars": ["v", "sex", "site"]}],
    }
    base.update(over)
    return AnalysisSpec.model_validate(base)


# ── the acceptance test ───────────────────────────────────────────────

class TestNothingForbiddenSurvives:

    #: One record carrying every category the brief forbids.
    DIRTY = {
        "patient_guid": "GUID-SHOULD-NOT-LEAK",
        "name": "FORBIDDEN-NAME",
        "given_name": "FORBIDDEN-GIVEN",
        "family_name": "FORBIDDEN-FAMILY",
        "personnummer": "FORBIDDEN-PNR",
        "address": "FORBIDDEN-ADDRESS",
        "postal_code": "FORBIDDEN-POSTCODE",
        "phone": "FORBIDDEN-PHONE",
        "email": "FORBIDDEN-EMAIL",
        "free_text": "FORBIDDEN-FREETEXT",
        "notes": "FORBIDDEN-NOTES",
        "birth_date": "FORBIDDEN-DOB",
        # legitimate fields, which must survive
        "pid": "abc123",
        "source": "cdr1",
        "value": 42.0,
        "unit": "L",
        "concept": "urn:pdhc:concept/x",
        "day_offset": 14,
        "demographics": {"sex": "female"},
        "meta": {"author_org": "org-1"},
    }

    def test_no_forbidden_value_appears_anywhere_in_the_output(self):
        out = project(self.DIRTY, build_allowlist(_spec()))
        blob = repr(out)
        for key, val in self.DIRTY.items():
            if isinstance(val, str) and val.startswith(("FORBIDDEN", "GUID-")):
                assert val not in blob, f"{key} leaked through projection"

    def test_the_legitimate_fields_do_survive(self):
        out = project(self.DIRTY, build_allowlist(_spec()))
        assert out["value"] == 42.0
        assert out["unit"] == "L"
        assert out["demographics.sex"] == "female"
        assert out["meta.author_org"] == "org-1"

    def test_a_field_added_later_is_not_carried_through(self):
        """Projection is CONSTRUCTIVE. A blocklist would pass through a column
        the CDR grows tomorrow; an allowlist cannot."""
        record = dict(self.DIRTY, newly_added_column="SURPRISE-PII")
        out = project(record, build_allowlist(_spec()))
        assert "newly_added_column" not in out
        assert "SURPRISE-PII" not in repr(out)

    def test_the_raw_patient_guid_is_never_projectable(self):
        assert "patient_guid" in NEVER_PROJECTABLE
        with pytest.raises(ProjectionError):
            project(self.DIRTY, {"patient_guid"})

    @pytest.mark.parametrize("field", sorted(NEVER_PROJECTABLE))
    def test_every_forbidden_field_is_refused_explicitly(self, field):
        """Naming one in an allowlist fails loudly here rather than
        succeeding quietly somewhere downstream."""
        with pytest.raises(ProjectionError):
            project({}, {"pid", field})


# ── pseudonyms ────────────────────────────────────────────────────────

class TestPseudonyms:

    def test_stable_within_a_project(self):
        assert pseudonymise("pat-1", KEY_A) == pseudonymise("pat-1", KEY_A)

    def test_different_across_projects(self):
        """The point of a per-project key: two projects' pseudonyms cannot be
        joined, so a pseudonym is not a lifelong identifier."""
        assert pseudonymise("pat-1", KEY_A) != pseudonymise("pat-1", KEY_B)

    def test_different_patients_differ(self):
        assert pseudonymise("pat-1", KEY_A) != pseudonymise("pat-2", KEY_A)

    def test_does_not_contain_the_input(self):
        assert "pat-1" not in pseudonymise("pat-1", KEY_A)

    def test_batch_mapping_is_consistent(self):
        m = pseudonymise_all(["pat-1", "pat-2"], KEY_A)
        assert m["pat-1"] == pseudonymise("pat-1", KEY_A)
        assert len(set(m.values())) == 2

    def test_a_short_key_is_refused(self):
        """A 64-bit HMAC key would make brute force cheap against a GUID
        space that is already enumerable."""
        with pytest.raises(ValueError):
            ProjectKey("p", b"tooshort")

    def test_the_key_refuses_to_render_itself(self):
        """Keys reach logs by the dullest route: formatting a config object,
        or an exception carrying locals."""
        k = ProjectKey("proj-a", b"SUPERSECRETKEYMATERIAL-0123456789")
        for rendered in (repr(k), str(k), f"{k}", "{}".format(k)):
            assert "SUPERSECRET" not in rendered
            assert "redacted" in rendered

    def test_the_key_is_not_in_the_hash(self):
        hash(ProjectKey("p", b"x" * 32))    # must not raise

    def test_loaded_from_the_secret_store_not_the_spec(self):
        env = {"ANALYSE_PROJECT_KEY_PROJ_A": "z" * 40}
        k = ProjectKey.from_env("proj-a", env=env)
        assert pseudonymise("pat-1", k)

    def test_missing_key_is_a_loud_failure(self):
        with pytest.raises(KeyError, match="no project key"):
            ProjectKey.from_env("absent", env={})


# ── coarsening ────────────────────────────────────────────────────────

class TestCoarsening:

    def test_day_offset_carries_no_calendar_information(self):
        assert day_offset(date(2026, 1, 15), date(2026, 1, 1)) == 14

    def test_calendar_has_no_day_granularity(self):
        assert calendar(date(2026, 3, 17)) == "2026-03"
        assert calendar(date(2026, 3, 17), TimeGrain.year) == "2026"
        assert not hasattr(TimeGrain, "day")

    @pytest.mark.parametrize("age,band", [
        (0, "0-4"), (37, "35-39"), (64, "60-64"), (89, "85-89"),
        (90, "90+"), (103, "90+"),
    ])
    def test_age_becomes_a_band_with_an_open_top(self, age, band):
        """90+ is open-ended deliberately: the bands thin out fast up there,
        and an exact age of 97 in one region is close to an identifier."""
        assert age_band(age) == band

    def test_birth_date_is_never_exposed(self):
        with pytest.raises(CoarsenError, match="never exposed"):
            birth_date(date(1980, 1, 1))

    def test_record_coarsening_replaces_age_and_date(self):
        out = coarsen_record(
            {"age": 37, "effective_at": date(2026, 1, 15), "value": 1.0},
            index_event=date(2026, 1, 1))
        assert out["age_band"] == "35-39"
        assert out["day_offset"] == 14
        assert "age" not in out and "effective_at" not in out

    def test_record_coarsening_drops_birth_dates_outright(self):
        out = coarsen_record({"birth_date": "1980-01-01", "dob": "x",
                              "value": 1.0})
        assert "birth_date" not in out and "dob" not in out

    def test_without_an_index_event_only_coarse_calendar_time_survives(self):
        out = coarsen_record({"effective_at": datetime(2026, 3, 17, 14, 30)})
        assert out["calendar"] == "2026-03"
        assert "day_offset" not in out


class TestAllowlistDerivation:

    def test_it_comes_from_the_spec_not_from_configuration(self):
        allow = build_allowlist(_spec())
        assert "demographics.sex" in allow
        assert "meta.author_org" in allow

    def test_a_field_no_variable_reads_is_not_allowed(self):
        allow = build_allowlist(_spec(variables=[
            {"name": "v", "from": "x", "agg": "mean"}],
            analyses=[{"type": "describe", "vars": ["v"]}]))
        assert "demographics.sex" not in allow

    def test_structural_fields_are_always_present(self):
        allow = build_allowlist(_spec())
        for f in ("pid", "source", "value", "unit", "concept", "day_offset"):
            assert f in allow

    def test_the_allowlist_never_contains_a_forbidden_field(self):
        assert not (build_allowlist(_spec()) & NEVER_PROJECTABLE)
