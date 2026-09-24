"""The analysis spec — the only thing a node will execute (#644).

Everything is driven by this declarative, versioned document. The UI and the
CLI both PRODUCE specs; neither sends a free-form query to a node. A node
receives a spec, signed by the coordinator, and nothing else.

Two choices here differ from the reconstruction brief, both deliberate:

1. ``purpose`` uses the PLATFORM's closed enum, not the brief's vocabulary.
   The brief's example says ``quality_followup``, which is not a real value
   anywhere in PDHC. ips.pdhc owns the enum (consent_policy.py) and cdr
   enforces it (#664); inventing a parallel vocabulary would mean translating
   at the boundary and getting it wrong once. See DISCOVERY.md gap G6.

2. Only the SECONDARY-use purposes are accepted. An analysis reads for
   secondary use by definition. The primary-use values (care,
   care_coordination, patient_access, administration) belong to a
   care-delivery basis this tool has no claim to, and ``administration`` is
   never blocked by ips — accepting it would make the purpose field a way
   around consent rather than a way of declaring it.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel, ConfigDict, Field, field_validator, model_validator,
)

SPEC_VERSION = 1


class Purpose(str, Enum):
    """Secondary-use purposes, as ips.pdhc defines them.

    Sent to cdr as ``X-Access-Purpose`` (#664) and written to the audit log.
    """
    research = "research"
    statistics = "statistics"
    quality_registry = "quality_registry"


class Linkage(str, Enum):
    """How the same patient appearing in several CDRs is treated."""

    #: the platform issues one GUID across CDRs, so patients deduplicate
    #: directly. The normal case on PDHC, which is GUID-keyed throughout.
    shared_guid = "shared_guid"

    #: patients in different CDRs are treated as distinct. The UI must warn
    #: that one patient may be counted more than once.
    none = "none"

    #: RETIRED 2026-09-24 (#687, operator decision). `keyed_hash` was shipped
    #: as a tested mechanism that could not be used: the CDR holds no
    #: personnummer, the ips token path was never specified, and the legal
    #: basis was never established. A flag that looks like a feature is an
    #: invitation, so the mode is gone rather than dormant. ADR-0010 keeps the
    #: design and the reasoning — in particular why the key must never reach
    #: the coordinator — if it is ever wanted again.


class Agg(str, Enum):
    """How repeated observations collapse to one value per patient."""
    first = "first"
    last = "last"
    mean = "mean"
    min = "min"
    max = "max"
    count = "count"
    slope = "slope"
    time_to_first_event = "time_to_first_event"


class Op(str, Enum):
    gte = ">="
    gt = ">"
    lte = "<="
    lt = "<"
    eq = "=="
    ne = "!="


class _Strict(BaseModel):
    """Unknown keys are an error, not something to ignore.

    A silently-dropped field in an analysis spec is a figure computed from
    something other than what the analyst wrote.
    """
    model_config = ConfigDict(extra="forbid", frozen=True,
                              populate_by_name=True)


# ── cohort ────────────────────────────────────────────────────────────

class AgeBand(_Strict):
    from_: int | None = Field(default=None, alias="from", ge=0, le=120)
    to: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def _ordered(self):
        if self.from_ is not None and self.to is not None and self.from_ > self.to:
            raise ValueError("age_band: 'from' must not exceed 'to'")
        if self.from_ is None and self.to is None:
            raise ValueError("age_band: give at least one of 'from' / 'to'")
        return self


class ObservationCriterion(_Strict):
    observation: str = Field(min_length=1)
    op: Op
    value: float


class AgeCriterion(_Strict):
    age_band: AgeBand


Criterion = Union[ObservationCriterion, AgeCriterion]


class Cohort(_Strict):
    include: list[Criterion] = Field(min_length=1)


class IndexEvent(_Strict):
    """The zero point for every day offset in the spec.

    Quasi-identifier coarsening (AN-2) expresses dates as days relative to
    this, so a spec without one cannot use windows.
    """
    observation: str = Field(min_length=1)


# ── variables ─────────────────────────────────────────────────────────

class Variable(_Strict):
    name: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1)
    agg: Agg | None = None
    window_days: tuple[int, int] | None = None

    @field_validator("window_days")
    @classmethod
    def _window_ordered(cls, v):
        if v is not None and v[0] > v[1]:
            raise ValueError("window_days: start must not exceed end")
        return v

    @model_validator(mode="after")
    def _agg_required_for_repeated(self):
        # demographics.* and meta.* are one value per patient by nature and
        # need no aggregation. Anything else is an observation series, and
        # collapsing a series without saying how is ambiguous.
        flat = self.from_.startswith(("demographics.", "meta."))
        if not flat and self.agg is None:
            raise ValueError(
                f"variable '{self.name}': agg is required for observation "
                f"'{self.from_}' (it is a series, not a single value)")
        if flat and self.agg is not None:
            raise ValueError(
                f"variable '{self.name}': '{self.from_}' is a single value "
                f"per patient; agg does not apply")
        if flat and self.window_days is not None:
            raise ValueError(
                f"variable '{self.name}': window_days does not apply to "
                f"'{self.from_}'")
        return self


# ── groups ────────────────────────────────────────────────────────────

class Group(_Strict):
    name: str = Field(min_length=1)
    where: dict[str, dict[str, float]] = Field(min_length=1)


# ── analyses ──────────────────────────────────────────────────────────

class Bins(_Strict):
    width: float = Field(gt=0)
    range: tuple[float, float]

    @model_validator(mode="after")
    def _ordered(self):
        if self.range[0] >= self.range[1]:
            raise ValueError("bins.range: low must be below high")
        return self


class Describe(_Strict):
    type: Literal["describe"]
    vars: list[str] = Field(min_length=1)


class Histogram(_Strict):
    type: Literal["histogram"]
    var: str
    bins: Bins | None = None


class Frequency(_Strict):
    type: Literal["frequency"]
    vars: list[str] = Field(min_length=1)


class Correlation(_Strict):
    type: Literal["correlation"]
    vars: list[str] = Field(min_length=2)
    method: Literal["pearson", "spearman"] = "pearson"


class CompareGroups(_Strict):
    type: Literal["compare_groups"]
    vars: list[str] = Field(min_length=1)


class OverTime(_Strict):
    type: Literal["over_time"]
    var: str
    bin_days: int = Field(gt=0)
    range_days: tuple[int, int]

    @model_validator(mode="after")
    def _ordered(self):
        if self.range_days[0] >= self.range_days[1]:
            raise ValueError("over_time.range_days: start must precede end")
        return self


class Completeness(_Strict):
    type: Literal["completeness"]


Analysis = Annotated[
    Union[Describe, Histogram, Frequency, Correlation, CompareGroups,
          OverTime, Completeness],
    Field(discriminator="type"),
]


# ── the spec ──────────────────────────────────────────────────────────

class AnalysisSpec(_Strict):
    spec_version: Literal[1] = SPEC_VERSION
    title: str = Field(min_length=1)
    purpose: Purpose
    sources: list[str] = Field(min_length=1)
    linkage: Linkage = Linkage.shared_guid
    index_event: IndexEvent | None = None
    cohort: Cohort
    variables: list[Variable] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    analyses: list[Analysis] = Field(min_length=1)

    #: Groups may overlap ONLY when the spec says so. Overlapping membership
    #: is otherwise an error, not a warning: a patient counted in two groups
    #: silently breaks every between-group comparison.
    allow_overlapping_groups: bool = False

    @field_validator("sources")
    @classmethod
    def _sources_unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("sources: duplicate source id")
        return v

    @model_validator(mode="after")
    def _cross_references_resolve(self):
        names = [v.name for v in self.variables]
        if len(set(names)) != len(names):
            raise ValueError("variables: duplicate variable name")
        known = set(names)

        gnames = [g.name for g in self.groups]
        if len(set(gnames)) != len(gnames):
            raise ValueError("groups: duplicate group name")

        # A group predicate can only reference a declared variable.
        for g in self.groups:
            for ref in g.where:
                if ref not in known:
                    raise ValueError(
                        f"group '{g.name}': unknown variable '{ref}'")

        # Every analysis must name variables that exist, or the run fails at
        # the node after the read has already happened.
        for i, a in enumerate(self.analyses):
            refs = list(getattr(a, "vars", []) or [])
            single = getattr(a, "var", None)
            if single:
                refs.append(single)
            for ref in refs:
                if ref not in known:
                    raise ValueError(
                        f"analyses[{i}] ({a.type}): unknown variable '{ref}'")
            if a.type == "compare_groups" and len(self.groups) < 2:
                raise ValueError(
                    "analyses: compare_groups needs at least two groups")

        # Windows and over_time are expressed relative to the index event.
        needs_index = any(v.window_days is not None for v in self.variables) \
            or any(a.type == "over_time" for a in self.analyses)
        if needs_index and self.index_event is None:
            raise ValueError(
                "index_event is required when window_days or over_time is "
                "used — day offsets have no zero point without it")
        return self
