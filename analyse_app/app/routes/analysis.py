"""The coordinator's web surface (#722).

`app/ui/` — charts, question cards, sentences, recipes, i18n — was built for
Phase 3 (#655–#658), tested, and imported by nothing. #714 found why: it
renders ENGINE output, and the coordinator had no route. Only
`cli_analyse.py` could reach it, so there was no page for a person to use and
#688's usability acceptance could not be performed at all.

This is that page.

## Where the numbers come from

Two paths, and the page always says which one it used:

* **Configured nodes** — when `ANALYSE_NODES` and `ANALYSE_TRANSPORT_SECRET`
  are both set, the spec is fanned out for real through the sealed
  coordinator↔node wire.
* **Synthetic** — otherwise. `app/testing/synth` drives the *real* node and
  coordinator code over generated data; it is not a mock of the engine, it is
  the engine over data nobody's health depends on.

Synthetic is the honest default rather than an error page, for two reasons.
The brief makes `data_mode: synthetic` the default everywhere and says plainly
that it must not be possible to point this tool at real patients by
forgetting a flag. And #688 asks whether a clinician can get an answer out of
this — a question about cards, sentences and suppression, none of which needs
real data to evaluate.

Every response is labelled, and a synthetic run says so on the page and in the
provenance. A number whose origin is ambiguous is worse than no number.
"""
from __future__ import annotations

import json
from typing import Any

from flask import (
    Blueprint, Response, current_app, jsonify, render_template, request,
)

from app.services.role_guards import researcher_required
from app.spec.models import AnalysisSpec
from app.ui import charts, recipes, sentences

bp = Blueprint("analysis", __name__, url_prefix="/analysis")

#: Kept small on purpose — a synthetic population is for judging the page, not
#: for benchmarking. 3 sources mirrors the federation property tests.
SYNTH_NODES, SYNTH_PATIENTS, SYNTH_SEED = 3, 400, 0

#: The concept `app/testing/synth` writes on every row. The example spec below
#: MUST use it: a default that matches nothing returns an empty result, and a
#: page whose own example produces no answer teaches the reader that the tool
#: does not work.
SYNTH_CONCEPT = "x"


def _live_sources() -> list[str]:
    from app.transport.client import endpoints_from_config
    try:
        return [e.node_id for e in endpoints_from_config(current_app.config)]
    except ValueError:
        return []


def _can_run_live() -> bool:
    """A real fan-out needs both the nodes and the secret that seals the wire.

    The secret has no default by design: one that fell back to a development
    constant would ship working and attest nothing.
    """
    return bool(_live_sources()
                and current_app.config.get("ANALYSE_TRANSPORT_SECRET"))


def _run(spec: AnalysisSpec) -> tuple[Any, str]:
    """(result, mode). `mode` is reported to the user, never inferred by them."""
    if _can_run_live():
        from app.coordinator import run_distributed
        from app.transport.client import endpoints_from_config
        from app.transport.envelope import secret_from_config
        endpoints = [e for e in endpoints_from_config(current_app.config)
                     if e.node_id in set(spec.sources)]
        result = run_distributed(
            spec, endpoints, secret_from_config(current_app.config),
            project=current_app.config.get("ANALYSE_PROJECT_ID") or "default")
        return result, "live"

    from app.testing import synth
    sources = synth.build(nodes=SYNTH_NODES, patients=SYNTH_PATIENTS,
                          seed=SYNTH_SEED)
    return synth.run(spec, sources), "synthetic"


def _render_result(result, mode: str, lang: str) -> dict[str, Any]:
    """Engine output → what a person reads. This is app/ui's whole purpose."""
    blocks: list[dict[str, Any]] = []
    for r in getattr(result, "results", []) or []:
        kind = r.get("kind")
        pooled = r.get("pooled") or {}
        block: dict[str, Any] = {"kind": kind, "svg": None, "sentence": None,
                                 "exactness": None}
        if r.get("exactness"):
            block["exactness"] = sentences.exactness_sentence(
                r["exactness"], lang)
        try:
            if kind == "describe":
                var = (r.get("by_source") or {}).get("var") or "värdet"
                block["sentence"] = sentences.describe_sentence(
                    var, pooled, lang=lang)
            elif kind == "histogram" and pooled.get("bins"):
                block["svg"] = charts.histogram(
                    pooled["bins"], title=kind, lang=lang)
        except Exception as e:                       # noqa: BLE001
            # A renderer that raises must not take the whole page with it —
            # the numbers are still correct and the reader should see them.
            block["sentence"] = f"(could not render: {type(e).__name__})"
        blocks.append(block)

    prov = dict(getattr(result, "provenance", {}) or {})
    prov["data_mode"] = mode
    return {
        "blocks": blocks,
        "sources": [{"source": s.source, "ok": s.ok,
                     "n_patients": s.n_patients}
                    for s in (getattr(result, "sources", []) or [])],
        "provenance": recipes.provenance_rows({"provenance": prov}),
        "mode": mode,
    }


@bp.get("")
@researcher_required
def workspace():
    live = _can_run_live()
    return render_template(
        "analysis_workspace.html",
        live=live,
        sources=_live_sources(),
        synth_nodes=SYNTH_NODES, synth_patients=SYNTH_PATIENTS,
        example=json.dumps(_EXAMPLE_SPEC, indent=2, ensure_ascii=False))


@bp.post("/run")
@researcher_required
def run():
    lang = (request.args.get("lang") or "sv")[:2]
    raw = request.get_json(silent=True)
    if raw is None:
        try:
            raw = json.loads(request.form.get("spec") or "")
        except ValueError:
            return jsonify(error="bad_spec",
                           message="That is not valid JSON."), 400
    try:
        spec = AnalysisSpec.model_validate(raw)
    except Exception as e:                           # pydantic ValidationError
        # The spec's validators carry the reasoning ("index_event is required
        # when window_days or over_time is used — day offsets have no zero
        # point without it"). Passing them through is more use than a generic
        # "invalid spec".
        return jsonify(error="invalid_spec", message=str(e)), 400

    try:
        result, mode = _run(spec)
    except Exception as e:                           # noqa: BLE001
        return jsonify(error="run_failed", message=str(e)), 502

    payload = _render_result(result, mode, lang)
    if request.accept_mimetypes.best == "application/json" or \
            request.args.get("format") == "json":
        return jsonify(payload)
    return render_template("analysis_result.html", spec=spec, **payload)


@bp.post("/export.csv")
@researcher_required
def export_csv():
    """Aggregates only. There is no per-patient export from this surface."""
    raw = request.get_json(silent=True) or {}
    try:
        spec = AnalysisSpec.model_validate(raw)
    except Exception as e:
        return jsonify(error="invalid_spec", message=str(e)), 400
    result, mode = _run(spec)
    body = recipes.aggregates_csv({"results": getattr(result, "results", []),
                                   "provenance": {"data_mode": mode}})
    return Response(body, mimetype="text/csv", headers={
        "Content-Disposition": 'attachment; filename="aggregates.csv"'})


#: Written against the synthetic vocabulary so the page's own example
#: actually answers. Against configured nodes it would name that CDR's real
#: concepts instead.
_EXAMPLE_SPEC = {
    "title": "Mätvärde per källa",
    "purpose": "statistics",
    "sources": ["cdr1", "cdr2", "cdr3"],
    "cohort": {"include": [
        {"observation": SYNTH_CONCEPT, "op": ">=", "value": 0}]},
    "variables": [{"name": "matvarde", "from": SYNTH_CONCEPT, "agg": "mean"}],
    "analyses": [{"type": "describe", "vars": ["matvarde"]}],
}
