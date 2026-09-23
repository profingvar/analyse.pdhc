"""Swedish and English (#658).

Swedish is the default. This is a Swedish healthcare platform and the people
the brief describes — a clinician, a project coordinator — work in Swedish;
English is the second language, not the source one.

Translations live in one dict per language rather than in .po files: the
string count is small, and a build step to compile catalogues would be its
own dependency for very little gain. Revisit if this grows past a few hundred
keys.
"""
from __future__ import annotations

DEFAULT_LANG = "sv"
LANGS = ("sv", "en")

STRINGS: dict[str, dict[str, str]] = {
    "sv": {
        "app.title": "analyse.pdhc",
        "step.data": "Data",
        "step.cohort": "Urval",
        "step.question": "Fråga",
        "step.result": "Resultat",
        "sources.heading": "Vilka datakällor ska ingå?",
        "sources.status.online": "tillgänglig",
        "sources.status.offline": "svarar inte",
        "sources.snapshot": "data hämtad",
        "sources.eligible": "patienter som kan ingå",
        "linkage.warning.none": (
            "Samma patient kan finnas i flera källor och räknas då mer än en "
            "gång. Siffrorna nedan är summor per källa, inte unika patienter."),
        "cohort.heading": "Patienter som…",
        "cohort.count": "Antal patienter just nu",
        "question.heading": "Vad vill du veta?",
        "result.how_to_read": "Så läser du det här",
        "result.exact": "exakt",
        "result.approximate": "ungefärlig",
        "result.suppressed": "dolt för att skydda små grupper",
        "result.per_source": "Visa per källa",
        "privacy.banner": (
            "Pseudonymiserade uppgifter är fortfarande personuppgifter. "
            "Grupper med färre än {k} patienter visas som <{k}."),
        "mode.live": "SKARPA DATA — denna körning använder riktiga patientuppgifter",
        "error.too_few": (
            "Den här gruppen har för få patienter för att kunna visas säkert. "
            "Pröva att vidga urvalet."),
        "error.no_sources": "Ingen datakälla svarade. Inga siffror kan visas.",
    },
    "en": {
        "app.title": "analyse.pdhc",
        "step.data": "Data",
        "step.cohort": "Cohort",
        "step.question": "Question",
        "step.result": "Result",
        "sources.heading": "Which data sources should be included?",
        "sources.status.online": "available",
        "sources.status.offline": "not responding",
        "sources.snapshot": "data as of",
        "sources.eligible": "patients who could be included",
        "linkage.warning.none": (
            "The same patient may appear in more than one source and would "
            "then be counted more than once. The figures below are totals per "
            "source, not unique patients."),
        "cohort.heading": "Patients who…",
        "cohort.count": "Patients matching right now",
        "question.heading": "What do you want to know?",
        "result.how_to_read": "How to read this",
        "result.exact": "exact",
        "result.approximate": "approximate",
        "result.suppressed": "hidden to protect small groups",
        "result.per_source": "Show per source",
        "privacy.banner": (
            "Pseudonymised data are still personal data. Groups of fewer "
            "than {k} patients are shown as <{k}."),
        "mode.live": "LIVE DATA — this run uses real patient records",
        "error.too_few": (
            "This group has too few patients to show safely. "
            "Try widening the criteria."),
        "error.no_sources": "No data source answered. No figures can be shown.",
    },
}


def t(key: str, lang: str = DEFAULT_LANG, **fmt) -> str:
    """Translate. A missing key returns the KEY, loudly, rather than an empty
    string — a blank label looks like a design choice; `cohort.heading` in the
    middle of a page does not."""
    table = STRINGS.get(lang) or STRINGS[DEFAULT_LANG]
    s = table.get(key) or STRINGS[DEFAULT_LANG].get(key) or key
    return s.format(**fmt) if fmt else s


def missing_keys() -> dict[str, list[str]]:
    """Keys present in one language and not another. Used by a test, so a
    half-translated release fails rather than shipping English into a Swedish
    page."""
    base = set(STRINGS["sv"])
    return {lang: sorted(base - set(keys))
            for lang, keys in STRINGS.items() if base - set(keys)}
