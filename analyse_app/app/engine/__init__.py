"""Analysis engine: one module per type, each local / merge / finalize."""
from . import (compare_groups, completeness, correlation, describe,
               frequency, histogram, over_time, regression)
from .base import APPROXIMATE, EXACT, Partial, Result
from .sketch import TDigest

#: kind -> module, so a coordinator can dispatch without a match statement.
REGISTRY = {
    describe.KIND: describe,
    histogram.KIND: histogram,
    frequency.KIND: frequency,
    correlation.KIND: correlation,
    compare_groups.KIND: compare_groups,
    over_time.KIND: over_time,
    completeness.KIND: completeness,
    # #659: registered by kind so the coordinator dispatches uniformly; the
    # local/merge/finalize triple is named per model rather than generic.
    #
    # ── NOT FULLY DEPLOYED ──────────────────────────────────────────────
    # These two are implemented and tested here, but they are NOT in the
    # spec's `Analysis` union (app/spec/models.py), so **no spec can request
    # them** — a node will never be asked for either. They are ahead of the
    # spec rather than broken.
    #
    # Exposing them means adding LinearRegression and KaplanMeier models to
    # that union and a dispatch branch in node/runner.py::_dispatch, which
    # needs decisions nobody has taken: what a linear regression declares as
    # predictors vs outcome, and how a survival spec expresses its event and
    # censoring. Do not improvise those.
    #
    # Tracked with #712 (the per-CDR node topology); this comment exists so
    # the next person reading the REGISTRY does not have to discover the gap
    # by finding `linear_local` in a dead-code sweep, which is how it was
    # found the first time (#709 → #715).
    regression.LINEAR: regression,
    regression.KM: regression,
}

__all__ = ["Partial", "Result", "EXACT", "APPROXIMATE", "TDigest",
           "REGISTRY", "describe", "histogram", "frequency", "correlation",
           "compare_groups", "over_time", "completeness", "regression"]
