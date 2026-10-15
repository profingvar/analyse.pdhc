"""Quasi-identifier coarsening (#645).

Pseudonymisation removes the name. It does not stop a person being singled
out by the combination of what is left: an exact date of injury, an exact
age, a rare unit — a handful of ordinary fields can identify one patient in a
region. Coarsening is what keeps the rest of the record from doing the work
the name used to do.

ON BY DEFAULT. Every function here narrows; a caller has to pass an explicit
granularity to widen one, and none of them can be turned off entirely.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

#: Age bands. Five years by default, per the brief.
DEFAULT_AGE_BAND_YEARS = 5


class TimeGrain(str, Enum):
    """Calendar granularity. There is deliberately no DAY."""
    month = "month"
    year = "year"


class CoarsenError(ValueError):
    pass


def day_offset(when: date | datetime, index_event: date | datetime) -> int:
    """Days from the index event — the only time representation that leaves
    a node by default.

    A calendar date plus a diagnosis is often enough to identify someone. An
    offset is what the analysis actually needs (day 14 after injury), and it
    carries no calendar information at all.
    """
    a = when.date() if isinstance(when, datetime) else when
    b = index_event.date() if isinstance(index_event, datetime) else index_event
    return (a - b).days


def calendar(when: date | datetime, grain: TimeGrain = TimeGrain.month) -> str:
    """Calendar time at month or year granularity only.

    Used when an analysis genuinely needs wall-clock time — a seasonal
    pattern, a before/after around a policy change. Day granularity is not
    offered, because it is the granularity that identifies.
    """
    d = when.date() if isinstance(when, datetime) else when
    if grain is TimeGrain.year:
        return f"{d.year:04d}"
    return f"{d.year:04d}-{d.month:02d}"


def age_band(age_years: int | float,
             band: int = DEFAULT_AGE_BAND_YEARS) -> str:
    """Age as a band, never a number.

    The open-ended top band matters: 90-94, 95-99 and so on thin out fast,
    and an exact age of 97 in one region is close to an identifier on its
    own.
    """
    if band < 1:
        raise CoarsenError("age band must be at least 1 year")
    if age_years < 0:
        raise CoarsenError("age must not be negative")
    if age_years >= 90:
        return "90+"
    low = int(age_years // band) * band
    return f"{low}-{low + band - 1}"


def birth_date(*_args, **_kwargs):
    """Birth dates are never exposed, at any granularity.

    This exists as a function so that calling it fails loudly and names the
    reason, rather than someone reaching for ``calendar(dob, year)`` and
    getting a plausible-looking answer. A birth year plus a rare condition
    plus a region is a person.
    """
    raise CoarsenError(
        "birth dates are never exposed — derive an age_band() instead")


def coarsen_record(record: dict, *, index_event=None,
                   band: int = DEFAULT_AGE_BAND_YEARS,
                   grain: TimeGrain = TimeGrain.month) -> dict:
    """Apply the default coarsening to a projected record.

    Runs AFTER projection: there is no point coarsening a field that should
    not have been read at all.
    """
    out = dict(record)

    if "age" in out and out["age"] is not None:
        out["age_band"] = age_band(out.pop("age"), band)

    when = out.pop("effective_at", None)
    if when is not None:
        if index_event is not None:
            out["day_offset"] = day_offset(when, index_event)
        else:
            # No index event: the spec cannot use windows (AN-1 enforces
            # that), so only coarse calendar time is meaningful.
            out["calendar"] = calendar(when, grain)

    for never in ("birth_date", "date_of_birth", "dob"):
        out.pop(never, None)
    return out


# ---------------------------------------------------------------------------
# #824 — the node pipeline's coarsen step, which was documented and absent
# ---------------------------------------------------------------------------
#
# `node/runner.py`'s own docstring listed "6. coarsen — quasi-identifiers
# narrowed" and NOTHING on the node path called anything in this module.
# Meanwhile `privacy/projection.py` allowlists `day_offset` and comments it
# "days from the index event; never a calendar date", and `node/frame.py`
# reads `row["day_offset"]` for every longitudinal series. So a row's time
# axis was specified to arrive already coarsened, and nothing produced it.
#
# It went unnoticed because no node is deployed and `tests/test_frame.py`
# hands the pipeline rows that already carry `day_offset` — the fixture
# supplying exactly the field production does not. The moment #823 repointed
# the reader at `GET /api/v1/fhir/Observation`, rows began arriving with an
# `effectiveDateTime` and no offset, which fails in one of two ways:
#
#   * every longitudinal analysis silently degrades to "all rows undated",
#     because `_in_window` excludes a row with no `day_offset` and `series()`
#     drops it — a trend over 90 days of real data computes over nothing; or
#   * someone maps `effectiveDateTime` into the row to fix that, and a
#     calendar date is now inside a record the allowlist was written to keep
#     dates out of. That is the dangerous one, and it is the obvious thing to
#     try when the charts come back empty.
#
# ## The index event: THE SPEC ALREADY DECLARES IT
#
# #824 listed three candidate zero points and recorded the third — a
# spec-declared index event — as needing "a spec field that does not exist".
# **It exists.** `spec.IndexEvent` is `{observation: <concept>}`, its
# docstring is "the zero point for every day offset in the spec", and
# `AnalysisSpec`'s own validator already REFUSES a spec that uses
# `window_days` or `over_time` without one: "day offsets have no zero point
# without it". So the decision was taken when the spec was written; what was
# missing was only the code that honours it.
#
# Day 0 is therefore the patient's FIRST observation of the declared index
# concept. That is per-patient, which is what makes it safe: the analyst holds
# the concept but not the date, so there is no inverse. The alternative that
# looked natural — the spec's cohort window start — is re-identifiable BY
# CONSTRUCTION, because the researcher wrote the window and therefore holds
# the zero point, making `day_offset + window_start` the exact calendar date
# of every observation. An offset measured from a date the analyst knows is a
# date, spelled differently.
#
# Three consequences, all load-bearing:
#
#   * **Offsets can be NEGATIVE** — an observation before the index event, a
#     baseline measurement being the ordinary case. `window_days` only
#     requires start <= end, so a window like [-30, 0] is expressible and
#     meaningful, and clamping at zero would silently discard it.
#   * **A patient with no observation of the index concept has NO zero
#     point**, and their rows stay undated rather than falling back to their
#     own earliest row. A fallback would be worse than missing: their
#     "day 14" would mean fourteen days after something else, pooled into a
#     figure labelled as fourteen days after the index event.
#   * **"Day 14" is not a shared calendar day.** Two patients' day 0 are
#     different dates, so a pooled figure at day 14 averages patients aligned
#     to their own index events. That is what a longitudinal analysis usually
#     wants (14 days after presentation) and is wrong to read as
#     simultaneity. A spec needing wall-clock alignment uses `calendar()`
#     instead, at month or year grain.
#
# When a spec declares no index event it cannot use windows or `over_time`,
# and the only remaining reader of `day_offset` is `frame._ordered`, which
# needs nothing but the ORDER (for `first`, `last`, `slope` and
# `time_to_first_event`). There, the patient's own earliest row is a correct
# and complete zero point, and that is the documented fallback.

def _parse_when(value):
    """A date, a datetime, or an ISO-8601 string. None for anything else.

    Deliberately forgiving about the trailing `Z` that cdr emits and about a
    date-only string; deliberately NOT forgiving about an unparseable value,
    which stays undated rather than becoming a guess.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def index_events(rows, *, concept: str | None = None,
                 patient_key: str = "patient_guid",
                 concept_key: str = "concept",
                 when_key: str = "effective_at") -> dict:
    """Each patient's zero point: their earliest dated observation of
    ``concept``, or — when no concept is declared — their earliest dated
    observation of anything.

    A patient absent from the result has no zero point, which is a different
    thing from having one at day 0 and must stay distinguishable.
    """
    out: dict = {}
    for row in rows:
        pid = row.get(patient_key)
        if not pid:
            continue
        if concept is not None and row.get(concept_key) != concept:
            continue
        when = _parse_when(row.get(when_key))
        if when is None:
            continue
        if pid not in out or when < out[pid]:
            out[pid] = when
    return out


def coarsen_rows(rows, *, index_concept: str | None = None,
                 patient_key: str = "patient_guid",
                 concept_key: str = "concept",
                 when_key: str = "effective_at") -> tuple[list[dict], int]:
    """Replace every row's calendar date with a ``day_offset``.

    ``index_concept`` is ``spec.index_event.observation``; see the block
    above for why it is per patient and what happens to a patient who has no
    observation of it.

    Returns ``(rows, n_undated)``. Runs BEFORE projection, which is the
    opposite of what ``coarsen_record``'s docstring says about itself and is
    not an inconsistency: the allowlist does not name ``effective_at``, so a
    projected record no longer contains the field this step reads.
    Coarsening after projection would therefore silently compute nothing —
    the pipeline order "5 project, 6 coarsen" is only satisfiable if the
    coarsening that PRODUCES an allowlisted field happens first. Projection
    remains the last gate: it still admits only what it names,
    ``day_offset`` included.

    The calendar date is REMOVED, not merely accompanied. Leaving it beside
    the offset would make the projection allowlist the only thing standing
    between a date and the outside, and one future addition to
    ``STRUCTURAL_FIELDS`` would be enough.

    An undated row keeps ``day_offset: None`` and is counted. ``_ordered``
    sorts those last so they never masquerade as the index, ``_in_window``
    excludes them from any window, and ``series()`` drops them — all three
    already true, so the only thing needed here is not to invent a date.
    """
    index = index_events(rows, concept=index_concept, patient_key=patient_key,
                         concept_key=concept_key, when_key=when_key)
    out: list[dict] = []
    undated = 0
    for row in rows:
        new = dict(row)
        when = _parse_when(new.pop(when_key, None))
        start = index.get(new.get(patient_key))
        if when is None or start is None:
            new["day_offset"] = None
            undated += 1
        else:
            new["day_offset"] = day_offset(when, start)
        out.append(new)
    return out, undated
