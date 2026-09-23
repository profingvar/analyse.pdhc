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
