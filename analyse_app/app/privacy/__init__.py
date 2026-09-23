"""Privacy layer: projection, pseudonymisation, coarsening.

Pseudonymised data are STILL PERSONAL DATA under GDPR, not anonymised data.
Nothing in this package makes a dataset anonymous, and no interface built on
it should say that it does.
"""
from .disclosure import (
    DEFAULT_CORRELATION_MIN_N, DEFAULT_K_MIN, DEFAULT_PERCENTILE_RANGE,
    SUPPRESSED, DifferencingGuard, DisclosureError, DisclosurePolicy,
    SuppressedTable, merge_small_bins, round_for_public, safe_correlation,
    safe_group_comparison, safe_range, safe_summary, suppress_counts,
    suppress_table,
)
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
    "DisclosurePolicy", "DisclosureError", "SuppressedTable", "SUPPRESSED",
    "suppress_counts", "suppress_table", "safe_summary", "safe_correlation",
    "safe_group_comparison", "safe_range", "merge_small_bins",
    "round_for_public", "DifferencingGuard",
    "DEFAULT_K_MIN", "DEFAULT_CORRELATION_MIN_N", "DEFAULT_PERCENTILE_RANGE",
]
