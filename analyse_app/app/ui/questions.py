"""Question cards (#655).

The design goal, verbatim from the brief: a clinician or project coordinator
without statistical training gets from a question to a correct, readable
answer in under two minutes, WITHOUT CHOOSING A STATISTICAL TEST.

So the cards are phrased as questions, and the METHOD IS DERIVED from the
variable types. A user who has to pick between Pearson and Spearman has
already been asked a statistics question, whatever the button says.

Each card builds a spec fragment. The UI produces specs exactly as the CLI
does — there is no UI-only path into the engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

CONTINUOUS = "continuous"
CATEGORICAL = "categorical"


@dataclass(frozen=True)
class Question:
    key: str
    label_sv: str
    label_en: str
    #: How many variables, and of what kind. None = any.
    needs: tuple[str, ...]
    needs_groups: bool
    build: Callable[..., list[dict[str, Any]]]
    explain_sv: str
    explain_en: str


def _describe(vars_, **_):
    return [{"type": "describe", "vars": list(vars_)}]


def _distribution(vars_, **_):
    return [{"type": "histogram", "var": vars_[0]}]


def _frequency(vars_, **_):
    return [{"type": "frequency", "vars": list(vars_)}]


def _related_continuous(vars_, **_):
    # Pearson by default. Spearman is offered in expert mode only: choosing
    # between them IS the statistics question the brief says not to ask.
    return [{"type": "correlation", "vars": list(vars_), "method": "pearson"}]


def _related_categorical(vars_, **_):
    return [{"type": "frequency", "vars": list(vars_)}]


def _compare(vars_, **_):
    return [{"type": "compare_groups", "vars": list(vars_)}]


def _over_time(vars_, bin_days=7, range_days=(0, 90), **_):
    return [{"type": "over_time", "var": vars_[0], "bin_days": bin_days,
             "range_days": list(range_days)}]


def _completeness(vars_, **_):
    return [{"type": "completeness"}]


CARDS: tuple[Question, ...] = (
    Question("describe", "Beskriv mitt urval", "Describe my cohort",
             (), False, _describe,
             "En sammanfattande tabell över urvalet.",
             "A summary table of the cohort."),
    Question("distribution", "Hur fördelar sig X?", "How is X distributed?",
             (CONTINUOUS,), False, _distribution,
             "Ett stapeldiagram med median och kvartilavstånd markerade.",
             "A bar chart with the median and IQR marked."),
    Question("frequency", "Hur vanligt är Y?", "How common is Y?",
             (CATEGORICAL,), False, _frequency,
             "En frekvenstabell med stapeldiagram.",
             "A frequency table with a bar chart."),
    Question("related_continuous", "Hänger X och Y ihop?",
             "Are X and Y related?", (CONTINUOUS, CONTINUOUS), False,
             _related_continuous,
             "Ett rutnät som visar hur många patienter som hamnar i varje "
             "kombination. Aldrig en punktgraf — den skulle visa en prick "
             "per patient.",
             "A grid showing how many patients fall in each combination. "
             "Never a scatter plot, which would show one dot per patient."),
    Question("related_categorical", "Hänger X och Y ihop?",
             "Are X and Y related?", (CATEGORICAL, CATEGORICAL), False,
             _related_categorical,
             "En korstabell.", "A cross-tabulation."),
    Question("compare", "Skiljer sig grupperna åt?", "How do groups differ?",
             (), True, _compare,
             "Grupperna sida vid sida, med skillnader och osäkerhet.",
             "The groups side by side, with differences and their "
             "uncertainty."),
    Question("over_time", "Hur förändras X över tid?",
             "How does X change over time?", (CONTINUOUS,), False, _over_time,
             "En kurva per grupp, räknat i dagar från varje patients egen "
             "starthändelse.",
             "One curve per group, counted in days from each patient's own "
             "index event."),
    Question("completeness", "Hur kompletta är data?",
             "How complete are the data?", (), False, _completeness,
             "Hur mycket data som finns — inte vad den säger.",
             "How much data exists — not what it says."),
)

BY_KEY = {q.key: q for q in CARDS}


def applicable(var_kinds: dict[str, str], has_groups: bool) -> list[Question]:
    """The cards that can actually be answered with what the user has chosen.

    Offering a card that cannot run and failing afterwards is worse than not
    offering it: the user has already committed to a question.
    """
    kinds = list(var_kinds.values())
    out = []
    for q in CARDS:
        if q.needs_groups and not has_groups:
            continue
        if q.needs:
            need = list(q.needs)
            pool = list(kinds)
            ok = True
            for k in need:
                if k in pool:
                    pool.remove(k)
                else:
                    ok = False
                    break
            if not ok:
                continue
        out.append(q)
    return out


def build_analyses(question_key: str, variables: Sequence[str],
                   **options) -> list[dict[str, Any]]:
    q = BY_KEY.get(question_key)
    if q is None:
        raise KeyError(f"unknown question card: {question_key}")
    return q.build(list(variables), **options)
