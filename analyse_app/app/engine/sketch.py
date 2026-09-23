"""A bounded, mergeable quantile sketch (#647).

Medians and IQRs need order statistics, and order statistics need all the
data — which is exactly what must not leave a node. A sketch is the way out:
it summarises the distribution in bounded space, merges across nodes, and
carries no individual value.

This is a t-digest in the usual form: centroids of (mean, weight), compressed
so that clusters near the tails stay small and clusters near the median may
grow. Tail accuracy is what matters for a quantile sketch, and it is also
what keeps a single outlying patient from sitting alone in a centroid where
their value would be recoverable.

Quantiles from it are APPROXIMATE, and every caller flags them so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Higher is more accurate and larger. 100 keeps a few hundred centroids at
#: most, which is small enough to send and accurate to well under a percent
#: for the medians and quartiles this tool reports.
DEFAULT_COMPRESSION = 100.0


@dataclass
class TDigest:
    compression: float = DEFAULT_COMPRESSION
    centroids: list[tuple[float, float]] = field(default_factory=list)  # (mean, weight)

    @property
    def total_weight(self) -> float:
        return sum(w for _, w in self.centroids)

    def add(self, value: float, weight: float = 1.0) -> None:
        self.centroids.append((float(value), float(weight)))
        if len(self.centroids) > self.compression * 10:
            self.compress()

    def compress(self) -> None:
        """Merge adjacent centroids while the result stays within the size
        the scale function allows at that quantile."""
        if not self.centroids:
            return
        pts = sorted(self.centroids)
        total = sum(w for _, w in pts)
        if total <= 0:
            self.centroids = []
            return

        out: list[tuple[float, float]] = []
        cum = 0.0
        cur_mean, cur_w = pts[0]
        for mean, w in pts[1:]:
            q = (cum + cur_w + w / 2.0) / total
            # k-size bound: centroids may be large near q=0.5, must stay
            # small near the tails.
            limit = 4.0 * total * q * (1.0 - q) / self.compression
            if cur_w + w <= max(limit, 1.0):
                cur_mean = (cur_mean * cur_w + mean * w) / (cur_w + w)
                cur_w += w
            else:
                out.append((cur_mean, cur_w))
                cum += cur_w
                cur_mean, cur_w = mean, w
        out.append((cur_mean, cur_w))
        self.centroids = out

    def quantile(self, q: float) -> float | None:
        if not self.centroids:
            return None
        if not 0.0 <= q <= 1.0:
            raise ValueError("quantile must be within [0, 1]")
        pts = sorted(self.centroids)
        total = sum(w for _, w in pts)
        target = q * total
        cum = 0.0
        for i, (mean, w) in enumerate(pts):
            if cum + w >= target:
                if i == 0 or w <= 1:
                    return mean
                prev_mean, prev_w = pts[i - 1]
                # linear interpolation between adjacent centroid means
                span = w
                frac = (target - cum) / span if span else 0.0
                return prev_mean + (mean - prev_mean) * min(max(frac, 0.0), 1.0)
            cum += w
        return pts[-1][0]

    def protect(self, k_min: int) -> "TDigest":
        """Merge centroids until none describes fewer than ``k_min`` patients.

        WITHOUT THIS THE SKETCH LEAKS. t-digest compression deliberately keeps
        tail centroids small — down to weight 1 — because that is what makes
        tail quantiles accurate. But a centroid of weight 1 IS one patient's
        exact value, and the sketch crosses the wire. The most extreme
        patients, the ones disclosure control works hardest to protect, are
        exactly the ones who end up alone in a centroid.

        So a node runs this before serialising. The cost is tail resolution,
        which is aligned with the rest of the design rather than a loss: AN-3
        already refuses to publish true minima and maxima and reports a
        p5–p95 range instead, so the tails were never going to be shown at
        full resolution anyway.

        Returns a new digest; the caller's own is untouched.
        """
        if k_min < 1:
            raise ValueError("k_min must be at least 1")
        pts = sorted(self.centroids)
        out: list[list[float]] = []
        for mean, w in pts:
            if out and out[-1][1] < k_min:
                total = out[-1][1] + w
                out[-1][0] = (out[-1][0] * out[-1][1] + mean * w) / total
                out[-1][1] = total
            else:
                out.append([mean, w])
        # A trailing group below the floor merges backwards, so no patient is
        # dropped at the top of the distribution.
        while len(out) > 1 and out[-1][1] < k_min:
            mean, w = out.pop()
            total = out[-1][1] + w
            out[-1][0] = (out[-1][0] * out[-1][1] + mean * w) / total
            out[-1][1] = total
        return TDigest(compression=self.compression,
                       centroids=[(m, w) for m, w in out])

    def to_json(self) -> dict:
        return {"compression": self.compression,
                "centroids": [[m, w] for m, w in self.centroids]}

    @classmethod
    def from_json(cls, blob: dict) -> "TDigest":
        return cls(compression=blob.get("compression", DEFAULT_COMPRESSION),
                   centroids=[(m, w) for m, w in blob.get("centroids", [])])

    @classmethod
    def merged(cls, digests: list["TDigest"]) -> "TDigest":
        """Merging is why this exists: a coordinator combines node sketches
        without ever seeing a value."""
        out = cls(compression=max((d.compression for d in digests),
                                  default=DEFAULT_COMPRESSION))
        for d in digests:
            out.centroids.extend(d.centroids)
        out.compress()
        return out
