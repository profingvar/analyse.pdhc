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
from app.privacy.disclosure import SUPPRESSED
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


def _title_for(r: dict[str, Any], kind: str) -> str:
    """A chart titled "histogram" tells the reader nothing they cannot see.
    Prefer the variable the analysis was about."""
    by = r.get("by_source") or {}
    return by.get("var") or r.get("var") or kind


def _show(v: Any, k_min: int) -> Any:
    """Render a withheld count the way the sentence promises it.

    `SUPPRESSED` is the literal "<k" — a sentinel, compared by identity all
    over the engine, with the "k" never substituted for anything. The
    suppression sentence meanwhile tells the reader that small groups "are
    shown as <5". So the page explained a notation it did not then use, and
    "k" is jargon besides. Substituted here, at the edge, so the sentinel
    stays a sentinel and k_min stays out of a module-level constant.
    """
    return f"<{k_min}" if v == SUPPRESSED else v


def _block(kind: str) -> dict[str, Any]:
    return {"kind": kind, "svg": None, "table": None, "sentence": None,
            "notes": [], "exactness": None}


def _freq_block(block, pooled, k_min, lang):
    """Counts as a table, plus the sentence that explains a withheld one.

    #724: this rendered as a bare heading. The card carried an exactness
    line and no figures, so on the one card where a category had been
    withheld the reader was told the numbers were exact and shown none.
    """
    counts = pooled.get("counts") or {}
    if not counts:
        return
    cross = pooled.get("cross")
    head = ("Grupp", "Patienter") if lang == "sv" else ("Group", "Patients")
    block["table"] = charts.data_table(
        list(head), [[k, _show(v, k_min)] for k, v in counts.items()],
        caption="Frekvens" if lang == "sv" else "Frequency")
    if any(v == SUPPRESSED for v in counts.values()):
        block["sentence"] = sentences.suppression_sentence(k_min, lang)
    elif cross:
        block["sentence"] = None


def _corr_block(block, pooled, lang):
    lines = []
    for pair, stats in pooled.items():
        a, _, b = str(pair).partition("|")
        lines.append(sentences.correlation_sentence(a, b or pair, stats, lang))
    block["sentence"] = " ".join(lines) if lines else None


def _compare_block(block, pooled, k_min, lang):
    groups = pooled.get("groups") or {}
    comps = pooled.get("comparisons") or {}
    head = (("Grupp", "Patienter", "Medel", "95 % KI")
            if lang == "sv" else ("Group", "Patients", "Mean", "95% CI"))
    rows = []
    for name, g in groups.items():
        ci = g.get("ci95") or [None, None]
        rows.append([name, _show(g.get("n"), k_min),
                     None if g.get("mean") is None else round(g["mean"], 2),
                     "" if ci[0] is None
                     else f"{ci[0]:.2f}–{ci[1]:.2f}"])
    if rows:
        block["table"] = charts.data_table(
            list(head), rows,
            caption="Grupper" if lang == "sv" else "Groups")
    said = []
    for pair, comp in comps.items():
        a, _, b = str(pair).partition(" vs ")
        said.append(sentences.comparison_sentence(a, b or pair, comp, lang))
    if pooled.get("suppressed") or any(
            g.get("suppressed") for g in groups.values()):
        said.append(sentences.suppression_sentence(k_min, lang))
    block["sentence"] = " ".join(said) if said else None


def _completeness_block(block, pooled, lang):
    miss = pooled.get("missingness") or {}
    head = (("Variabel", "Saknas", "Av", "Andel saknad")
            if lang == "sv" else ("Variable", "Missing", "Of", "% missing"))
    rows = [[v, m.get("missing"), m.get("total"),
             f"{m.get('percent_missing', 0):.1f} %"] for v, m in miss.items()]
    if rows:
        block["table"] = charts.data_table(
            list(head), rows,
            caption="Täckning" if lang == "sv" else "Completeness")
    n_p, n_r = pooled.get("n_patients"), pooled.get("n_rows")
    if n_p is not None:
        block["sentence"] = (
            f"{n_p} patienter och {n_r} observationer ingår."
            if lang == "sv" else
            f"{n_p} patients and {n_r} observations are included.")


def _render_result(result, mode: str, lang: str,
                   spec: Any = None) -> dict[str, Any]:
    """Engine output → what a person reads. This is app/ui's whole purpose.

    #722 wired two of the seven analysis kinds. #724 wired the rest: every
    kind the spec language can express now renders something, and a card
    never carries a reassurance about figures it is not showing.
    """
    prov = dict(getattr(result, "provenance", {}) or {})
    prov["data_mode"] = mode
    k_min = prov.get("k_min_applied") or 5

    blocks: list[dict[str, Any]] = []
    for r in getattr(result, "results", []) or []:
        kind = r.get("kind")
        pooled = r.get("pooled") or {}
        block = _block(kind)
        try:
            if kind == "describe":
                var = (r.get("by_source") or {}).get("var") or "värdet"
                block["sentence"] = sentences.describe_sentence(
                    var, pooled, lang=lang)
            elif kind == "histogram" and pooled.get("bins"):
                # charts.histogram takes (bins, *, title, y_label) — there is
                # no lang argument, and passing one raised a TypeError that
                # the per-block catch turned into "(could not render)". The
                # chart silently did not appear, which is the fourth thing
                # #688 sets out to check.
                block["svg"] = charts.histogram(
                    pooled["bins"], title=_title_for(r, kind),
                    y_label="patienter" if lang == "sv" else "patients")
                # data_table's own docstring calls itself "always rendered
                # with the chart" — the accessibility requirement and the
                # "let me check the picture" requirement at once. It was not
                # being rendered with the chart.
                head = ("Intervall", "Patienter") if lang == "sv" else \
                       ("Range", "Patients")
                block["table"] = charts.data_table(
                    list(head),
                    [[b.get("label"), _show(b.get("count"), k_min)]
                     for b in pooled["bins"]],
                    caption=_title_for(r, kind))
                if any(b.get("count") == SUPPRESSED for b in pooled["bins"]):
                    block["sentence"] = sentences.suppression_sentence(
                        k_min, lang)
            elif kind == "frequency":
                _freq_block(block, pooled, k_min, lang)
            elif kind == "correlation":
                _corr_block(block, pooled, lang)
            elif kind == "compare_groups":
                _compare_block(block, pooled, k_min, lang)
            elif kind == "over_time" and pooled.get("series"):
                block["svg"] = charts.curve(
                    pooled["series"], title=_title_for(r, kind),
                    x_label=("dagar sedan indexhändelse" if lang == "sv"
                             else "days since index event"))
            elif kind == "completeness":
                _completeness_block(block, pooled, lang)
        except Exception as e:                       # noqa: BLE001
            # A renderer that raises must not take the whole page with it —
            # the numbers are still correct and the reader should see them.
            block["sentence"] = f"(could not render: {type(e).__name__})"

        # Exactness qualifies figures. On a card with no figures it is not a
        # qualification, it is a claim about nothing — and the frequency card
        # showed exactly that. Attach it only where something was rendered.
        if r.get("exactness") and (block["svg"] or block["table"]
                                   or block["sentence"]):
            block["exactness"] = sentences.exactness_sentence(
                r["exactness"], lang)
        if not (block["svg"] or block["table"] or block["sentence"]):
            block["sentence"] = (
                f"Ingen utdata att visa för '{kind}'." if lang == "sv"
                else f"No output to show for '{kind}'.")
        blocks.append(block)

    # #725: the node's own explanation of why it returned less than was asked.
    notes = list(getattr(result, "notes", []) or [])
    # The double-counting warning the brief requires. It was written, tested,
    # and never called, so a linkage: none run warned nobody.
    if spec is not None:
        warn = sentences.linkage_sentence(
            getattr(getattr(spec, "linkage", None), "value", "") or "",
            len(getattr(spec, "sources", []) or []), lang)
        if warn:
            notes.insert(0, warn)

    return {
        "blocks": blocks,
        "notes": notes,
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

    payload = _render_result(result, mode, lang, spec)
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
