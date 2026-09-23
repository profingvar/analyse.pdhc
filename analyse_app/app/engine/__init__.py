"""Analysis engine: one module per type, each local / merge / finalize."""
from . import correlation, describe, frequency, histogram
from .base import APPROXIMATE, EXACT, Partial, Result
from .sketch import TDigest

#: kind -> module, so a coordinator can dispatch without a match statement.
REGISTRY = {
    describe.KIND: describe,
    histogram.KIND: histogram,
    frequency.KIND: frequency,
    correlation.KIND: correlation,
}

__all__ = ["Partial", "Result", "EXACT", "APPROXIMATE", "TDigest",
           "REGISTRY", "describe", "histogram", "frequency", "correlation"]
