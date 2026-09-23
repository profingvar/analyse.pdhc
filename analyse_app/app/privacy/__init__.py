"""Privacy layer: projection, pseudonymisation, coarsening.

Pseudonymised data are STILL PERSONAL DATA under GDPR, not anonymised data.
Nothing in this package makes a dataset anonymous, and no interface built on
it should say that it does.
"""
from .coarsen import (
    DEFAULT_AGE_BAND_YEARS, CoarsenError, TimeGrain, age_band, calendar,
    coarsen_record, day_offset,
)
from .projection import (
    NEVER_PROJECTABLE, STRUCTURAL_FIELDS, ProjectionError, build_allowlist,
    project, project_all,
)
from .pseudonym import (
    PSEUDONYM_HEX_LEN, ProjectKey, pseudonymise, pseudonymise_all,
)

__all__ = [
    "build_allowlist", "project", "project_all", "ProjectionError",
    "STRUCTURAL_FIELDS", "NEVER_PROJECTABLE",
    "ProjectKey", "pseudonymise", "pseudonymise_all", "PSEUDONYM_HEX_LEN",
    "day_offset", "calendar", "age_band", "coarsen_record", "TimeGrain",
    "CoarsenError", "DEFAULT_AGE_BAND_YEARS",
]
