"""Saved recipes and report export (#657).

A RECIPE is a named spec someone else with the right role can rerun on fresh
data. It is shared by ID and only by ID — never by a link carrying the
parameters. A cohort definition IS potentially identifying: "patients at this
clinic, over 85, with this rare diagnosis" can describe one person, and a URL
is pasted into chat, logged by proxies and kept in browser history.

A REPORT carries the figures AND the provenance: spec hash, sources, snapshot
times and the privacy settings in force. A report that cannot be traced back
to a spec hash is a screenshot with a letterhead.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.spec import AnalysisSpec, canonical_json, spec_hash


@dataclass
class Recipe:
    recipe_id: str
    name: str
    spec_json: dict[str, Any]
    owner_user_guid: str | None = None
    owner_org_guids: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def spec(self) -> AnalysisSpec:
        return AnalysisSpec.model_validate(self.spec_json)

    @property
    def spec_hash(self) -> str:
        return spec_hash(self.spec)

    def share_url(self, base: str) -> str:
        """Only the id. Never the parameters — see the module docstring."""
        return f"{base.rstrip('/')}/recipe/{self.recipe_id}"

    def to_json(self) -> dict[str, Any]:
        return {"recipe_id": self.recipe_id, "name": self.name,
                "spec": self.spec_json, "spec_hash": self.spec_hash,
                "owner_user_guid": self.owner_user_guid,
                "created_at": self.created_at}


def make_recipe(name: str, spec: AnalysisSpec, *, recipe_id: str,
                owner_user_guid: str | None = None,
                owner_org_guids: list[str] | None = None) -> Recipe:
    return Recipe(recipe_id=recipe_id, name=name,
                  spec_json=json.loads(canonical_json(spec)),
                  owner_user_guid=owner_user_guid,
                  owner_org_guids=list(owner_org_guids or []))


# ── export ────────────────────────────────────────────────────────────

def aggregates_csv(result: dict[str, Any]) -> str:
    """The aggregates as CSV — the machine-readable half of a report.

    Suppressed cells export as ``<k``, exactly as they display. Exporting the
    real number "because it is only a CSV" is how a suppressed figure gets
    into a spreadsheet and then into a slide.
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["analysis", "source", "key", "value"])
    for res in result.get("results", []):
        kind = res.get("kind")
        for key, val in _flatten(res.get("pooled", {})):
            w.writerow([kind, "(pooled)", key, val])
        for src, data in (res.get("by_source") or {}).items():
            for key, val in _flatten(data):
                w.writerow([kind, src, key, val])
    return buf.getvalue()


def _flatten(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, obj))
    return out


def provenance_rows(result: dict[str, Any]) -> list[tuple[str, str]]:
    """What a report must carry to be traceable, in display order."""
    p = result.get("provenance", {})
    rows = [
        ("Spec hash", p.get("spec_hash", "–")),
        ("Purpose", p.get("purpose", "–")),
        ("Linkage", p.get("linkage", "–")),
        ("Minimum group size applied", str(p.get("k_min_applied", "–"))),
        ("Coordinator version", p.get("coordinator_version", "–")),
    ]
    for src, when in sorted((p.get("snapshots") or {}).items()):
        rows.append((f"Data from {src}", when))
    for s in result.get("sources", []):
        rows.append((f"Source {s.get('source')}",
                     "answered" if s.get("ok") else
                     f"did not answer: {s.get('reason', 'unknown')}"))
    return rows
