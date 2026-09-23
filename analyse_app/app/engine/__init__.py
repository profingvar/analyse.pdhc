"""Analysis engine: one module per type, each local / merge / finalize."""
from . import (compare_groups, completeness, correlation, describe,
               frequency, histogram, over_time)
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
}

__all__ = ["Partial", "Result", "EXACT", "APPROXIMATE", "TDigest",
           "REGISTRY", "describe", "histogram", "frequency", "correlation",
           "compare_groups", "over_time", "completeness"]
