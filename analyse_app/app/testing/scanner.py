"""The identifier scanner (#650) — a build gate, not a report.

Everything else in this codebase argues that identifiers cannot leave a node.
This checks. It reads whatever the analysis actually produced — results, log
lines, error messages, exports — and fails if it finds something that looks
like a person.

It is deliberately CRUDE and deliberately NOISY. A scanner tuned to avoid
false positives is a scanner that misses the one real leak, and a false
positive costs a minute while a missed identifier costs a patient.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: A bare UUID. Every internal reference on this platform is one, so a UUID
#: in an output is either a patient, an org, or a request — none of which an
#: aggregate needs. Pseudonyms are 16 hex chars with no dashes and do not
#: match.
GUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE)

#: Swedish personnummer, in the forms people actually write them. Matched on
#: SHAPE, not on checksum: a value shaped like a personnummer is a finding
#: whether or not it would validate, because a near-miss in an output is
#: still someone trying to put one there.
PERSONNUMMER = re.compile(r"\b(?:19|20)?\d{6}[-+]?\d{4}\b")

#: The sentinels the test fixtures use. If one of these reaches an output the
#: leak is unambiguous.
SENTINEL = re.compile(r"\bFORBIDDEN-[A-Z]+\b|\bSURPRISE-PII\b|\bTEST-[A-Z0-9]+\b")

#: Raw key material names. A key in an output is a different incident from a
#: patient in one, and worth its own pattern.
KEY_MATERIAL = re.compile(r"ANALYSE_PROJECT_KEY_[A-Z0-9_]+|BEGIN [A-Z ]*PRIVATE KEY")

PATTERNS = {
    "guid": GUID,
    "personnummer": PERSONNUMMER,
    "sentinel": SENTINEL,
    "key_material": KEY_MATERIAL,
}


@dataclass
class Finding:
    pattern: str
    match: str
    where: str

    def __str__(self) -> str:
        return f"{self.pattern} in {self.where}: {self.match!r}"


def scan_text(text: str, *, where: str = "<text>",
              patterns: dict[str, re.Pattern] | None = None) -> list[Finding]:
    out: list[Finding] = []
    for name, pat in (patterns or PATTERNS).items():
        for m in pat.findall(text):
            out.append(Finding(pattern=name, match=m if isinstance(m, str)
                               else str(m), where=where))
    return out


def scan_object(obj: Any, *, where: str = "<object>") -> list[Finding]:
    """Walk a structure and scan every string in it, keys included.

    Keys as well as values: a dict keyed by patient guid discloses exactly as
    much as one that stores it.
    """
    findings: list[Finding] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            findings.extend(scan_text(node, where=path))
        elif isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str):
                    findings.extend(scan_text(k, where=f"{path}.<key>"))
                walk(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple, set)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(obj, where)
    return findings


def assert_clean(obj: Any, *, where: str = "<output>") -> None:
    """Raise with every finding listed. Used as a gate, so it names them all
    rather than stopping at the first — a leak is rarely alone."""
    found = scan_object(obj, where=where)
    if found:
        raise AssertionError(
            f"{len(found)} identifier-like value(s) in {where}:\n  "
            + "\n  ".join(str(f) for f in found))


def scan_paths(paths: Iterable[str]) -> list[Finding]:
    """Scan files — test output, exports, logs."""
    out: list[Finding] = []
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                out.extend(scan_text(fh.read(), where=p))
        except OSError:
            continue
    return out
