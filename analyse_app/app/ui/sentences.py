"""Plain-language summaries, from TEMPLATES (#655).

The brief says the summary sentence is "generated from a template (never
free-form text)", and that is not a style preference. A free-form sentence
about a clinical result is a claim nobody reviewed, produced by something
that does not know what it is allowed to say. A template can be read once,
argued about once, and then trusted.

Three rules these templates hold to:

- Never "effect", never "causes", never "significant" without its statistical
  sense being obvious. Differences are described, not explained.
- The EFFECT SIZE and its uncertainty come before any p-value, because "how
  big" is the question that was asked and "how surprising" is not.
- A suppressed figure is described as suppressed, in words, rather than
  silently omitted from the sentence.
"""
from __future__ import annotations

from typing import Any

from .i18n import DEFAULT_LANG


def _fmt(x: Any, dp: int = 1) -> str:
    if x is None:
        return "–"
    if isinstance(x, str):
        return x
    return f"{x:.{dp}f}"


def describe_sentence(var: str, pooled: dict[str, Any],
                      lang: str = DEFAULT_LANG) -> str:
    if pooled.get("suppressed"):
        return ("För få patienter för att visa ett medelvärde."
                if lang == "sv" else
                "Too few patients to show an average.")
    n, mean = pooled.get("n"), pooled.get("mean")
    ci = pooled.get("ci95") or [None, None]
    missing = pooled.get("missing") or 0
    if lang == "sv":
        s = (f"Bland {n} patienter var {var} i genomsnitt {_fmt(mean)} "
             f"(95 % konfidensintervall {_fmt(ci[0])}–{_fmt(ci[1])}).")
        if missing:
            s += (f" {missing} mätningar saknade värde och ingår inte.")
        return s
    s = (f"Among {n} patients, {var} averaged {_fmt(mean)} "
         f"(95% confidence interval {_fmt(ci[0])}–{_fmt(ci[1])}).")
    if missing:
        s += f" {missing} measurements had no value and are not included."
    return s


def comparison_sentence(a: str, b: str, comp: dict[str, Any],
                        lang: str = DEFAULT_LANG) -> str:
    """Effect size first, uncertainty second, p-value last if at all."""
    diff = comp.get("difference")
    ci = comp.get("ci95") or [None, None]
    if lang == "sv":
        return (f"I gruppen {b} var värdet i genomsnitt {_fmt(abs(diff))} "
                f"{'högre' if (diff or 0) >= 0 else 'lägre'} än i {a} "
                f"(95 % konfidensintervall {_fmt(ci[0])}–{_fmt(ci[1])}). "
                f"Det beskriver skillnaden mellan grupperna, inte en orsak.")
    return (f"In group {b} the value averaged {_fmt(abs(diff))} "
            f"{'higher' if (diff or 0) >= 0 else 'lower'} than in {a} "
            f"(95% confidence interval {_fmt(ci[0])}–{_fmt(ci[1])}). "
            f"This describes the difference between the groups, not a cause.")


def correlation_sentence(a: str, b: str, stats: dict[str, Any],
                         lang: str = DEFAULT_LANG) -> str:
    """Strength first, then the warning that a correlation is not a reason.

    The wording avoids every term in BANNED deliberately: a sentence that
    disclaims causation by NAMING it ("does not cause") still puts the word
    in front of a reader who will remember the word and not the negation.
    """
    if stats.get("suppressed"):
        return ("För få patienter för att visa ett samband."
                if lang == "sv" else
                "Too few patients to show an association.")
    r = stats.get("r")
    n = stats.get("n")
    if lang == "sv":
        return (f"Bland {n} patienter följdes {a} och {b} åt "
                f"({_strength(r, 'sv')}, r = {_fmt(r, 2)}). "
                f"Ett samband mellan två värden säger inget om varför.")
    return (f"Among {n} patients, {a} and {b} moved together "
            f"({_strength(r, 'en')}, r = {_fmt(r, 2)}). "
            f"An association between two values says nothing about why.")


def _strength(r: Any, lang: str) -> str:
    """Plain words for a number most readers cannot calibrate."""
    try:
        m = abs(float(r))
    except (TypeError, ValueError):
        return "okänd styrka" if lang == "sv" else "unknown strength"
    sv, en = ("svagt", "måttligt", "starkt"), ("weak", "moderate", "strong")
    word = (sv if lang == "sv" else en)[0 if m < 0.3 else 1 if m < 0.7 else 2]
    if lang == "sv":
        return f"{word} samband, {'samma' if (r or 0) >= 0 else 'motsatt'} riktning"
    return f"{word}, {'same' if (r or 0) >= 0 else 'opposite'} direction"


def exactness_sentence(exactness: str, lang: str = DEFAULT_LANG) -> str:
    if exactness == "exact":
        return ("Siffrorna är exakta även när flera källor kombineras."
                if lang == "sv" else
                "The figures are exact even when several sources are combined.")
    return ("Vissa siffror är ungefärliga när flera källor kombineras; "
            "de är markerade."
            if lang == "sv" else
            "Some figures are approximate when several sources are combined; "
            "they are marked.")


def suppression_sentence(k_min: int, lang: str = DEFAULT_LANG) -> str:
    if lang == "sv":
        return (f"Grupper med färre än {k_min} patienter visas som <{k_min}. "
                f"Ibland döljs även en grupp till, så att den första inte ska "
                f"gå att räkna ut från summorna.")
    return (f"Groups of fewer than {k_min} patients are shown as <{k_min}. "
            f"Sometimes a second group is hidden as well, so the first cannot "
            f"be worked out from the totals.")


def linkage_sentence(linkage: str, n_sources: int,
                     lang: str = DEFAULT_LANG) -> str:
    """The double-counting warning the brief asks for, in plain words."""
    if linkage != "none" or n_sources < 2:
        return ""
    if lang == "sv":
        return ("Samma patient kan finnas i flera källor och räknas då mer än "
                "en gång.")
    return ("The same patient may appear in more than one source and would "
            "then be counted more than once.")


#: Every sentence this module can produce, for the test that asserts none of
#: them contains causal language.
BANNED = ("effekt", "orsakar", "beror på", "effect", "causes", "caused by",
          "leads to")
