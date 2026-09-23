"""Allowlist projection (#645).

The analysis layer never receives names, personnummer, addresses, phone
numbers, e-mail or free text. This module is where that stops being a policy
and becomes a property of the code.

The distinction that matters: this is CONSTRUCTIVE, not a filter. The output
is built field by field from the allowlist; the input record is never copied
and then cleaned. A blocklist fails open — the day the CDR grows a column
nobody thought of, a filter passes it through and a projection does not. That
is the whole difference, and it is why the brief says allowlist and never
blocklist.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.spec import AnalysisSpec

#: Fields the engine needs structurally, whatever the spec asks for.
#: Kept minimal: every entry here is a field that leaves the node.
STRUCTURAL_FIELDS = frozenset({
    "pid",             # the per-project pseudonym — never the raw guid
    "source",          # which CDR answered, for per-source results
    "day_offset",      # days from the index event; never a calendar date
    "concept",         # what was measured
    "value",
    "unit",
})

#: Prefixes a spec variable may read from, beyond a bare observation name.
_FLAT_PREFIXES = ("demographics.", "meta.")

#: Never projectable, under any spec, by any route. Listed not because the
#: allowlist needs them — it does not, it admits only what it names — but so
#: that an attempt to allow one fails loudly here instead of silently
#: succeeding somewhere downstream.
NEVER_PROJECTABLE = frozenset({
    "name", "given_name", "family_name", "full_name",
    "personnummer", "pnr", "ssn", "national_id",
    "address", "street", "postal_code", "city",
    "phone", "telephone", "mobile", "email", "e_mail",
    "free_text", "note", "notes", "comment", "narrative", "text",
    "patient_guid",    # the raw key: pseudonymised before anything else
    "birth_date", "date_of_birth", "dob",
})


class ProjectionError(ValueError):
    """A field was requested that must never leave a node."""


def build_allowlist(spec: AnalysisSpec) -> frozenset[str]:
    """The complete set of fields this spec's analysis may see.

    Derived from the spec, not configured: a field is projectable because a
    declared variable reads it, or because the engine structurally needs it.
    Nothing else is.
    """
    allowed = set(STRUCTURAL_FIELDS)
    for var in spec.variables:
        src = var.from_
        if src.startswith(_FLAT_PREFIXES):
            allowed.add(src)
        else:
            # An observation-sourced variable reads the shared structural
            # observation fields; the concept selects which rows, it is not
            # a field of its own.
            continue
    forbidden = allowed & NEVER_PROJECTABLE
    if forbidden:
        raise ProjectionError(
            "spec would require never-projectable fields: "
            + ", ".join(sorted(forbidden)))
    return frozenset(allowed)


def _get_path(record: Mapping[str, Any], path: str) -> Any:
    """Read a dotted path. Missing is None, never an error — absent data is
    a fact about the patient, not a fault, and AN-4 reports missingness."""
    cur: Any = record
    for part in path.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


def project(record: Mapping[str, Any], allowlist: Iterable[str]) -> dict[str, Any]:
    """Build a new record containing ONLY the allowlisted fields.

    Note what is absent: there is no branch that copies ``record`` and deletes
    from it. A field the allowlist does not name cannot appear in the output,
    including one added to the source after this code was written.
    """
    allowed = frozenset(allowlist)
    forbidden = allowed & NEVER_PROJECTABLE
    if forbidden:
        raise ProjectionError(
            "allowlist names never-projectable fields: "
            + ", ".join(sorted(forbidden)))
    return {field: _get_path(record, field) for field in sorted(allowed)}


def project_all(records: Iterable[Mapping[str, Any]],
                allowlist: Iterable[str]) -> list[dict[str, Any]]:
    allowed = frozenset(allowlist)
    return [project(r, allowed) for r in records]
