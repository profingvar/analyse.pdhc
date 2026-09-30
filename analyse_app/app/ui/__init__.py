"""Server-rendered UI: charts, question cards, sentences, recipes, i18n.

See ADR-0009 for why this is server-rendered and has no JavaScript build
chain.

## #714 — this package has NO ROUTE, deliberately, and that blocks #688

Nothing outside this package imports it. A dead-code sweep will keep
reporting all fifteen functions, so here is the reasoning once.

**It is not orphaned and not superseded.** It renders ENGINE output: the
sentence builders take `Result.pooled`, `exactness` and the disclosure
`k_min`; `make_recipe` takes an `AnalysisSpec`; `aggregates_csv` and
`provenance_rows` take a result dict. Those shapes are produced by the
coordinator (`app/coordinator/`), and **the coordinator has no web route** —
it is reached only from `app/cli_analyse.py`.

`app/routes/researcher.py` is a different surface with different inputs: its
`cohort_histogram` / `boxplot` / `scatter` / `trend` endpoints read rows
fanned out through `CdrRegistry`, not engine results. The two cannot be
swapped; wiring this package into that route would mean feeding it a shape it
does not accept.

So the work is not "import this somewhere". It is giving the coordinator a
web surface, at which point this package is what renders it.

**The consequence nobody had noticed:** #688 is the Phase 3 usability
acceptance for these exact modules (#655–#658). It says it follows the deploy
ticket, and that deploy (#685) is now done — so the next person to pick up
#688 will find there is nothing to walk a user through. #688 cannot be
performed until this package has a route.
"""
from . import charts, i18n, palette, questions, recipes, sentences

__all__ = ["charts", "i18n", "palette", "questions", "recipes", "sentences"]
