"""Human names for plan.pdhc concepts. #758.

#733's merged inventory shows a concept's `display` so a researcher can tell
what they may ask about. cdr1 resolves its own, via
`clinical_read.resolve_display`; cdr_6 holds no plan client, and cdr1 is not
federated by this coordinator (#712) — so cdr_6's 21 concepts arrived as bare
GUIDs:

    urn:pdhc:concept/64928bff-9a46-472a-bbf1-dcfc694f945b   387,530 observations

A GUID is not an answer to "what can I ask about".

## Cosmetic, therefore fail-open

A display name is presentation. plan.pdhc being slow or down must degrade the
label, never the page — the inventory's whole point is that a researcher can see
what exists, and a missing name is a smaller loss than a missing source. Every
failure path here returns None and the caller keeps the GUID.

## Bounded, because a sibling is not a free resource

The lookup is one HTTP call per concept. 21 today; a reseed could make it
hundreds. Resolution is capped, cached for the process lifetime, and runs in a
small pool — an unbounded fan-out at a sibling is a denial of service with
extra steps, the same reasoning as `node/reader.py`'s spärr fan-out.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

#: plan.pdhc's local CodeSystem, as cdr1's PlanClient addresses it. The concept
#: GUID is the `code` within this system.
PLAN_LOCAL_CS_URL = "https://plan.pdhc.se/fhir/CodeSystem/plan-pdhc-local"

DEFAULT_TIMEOUT = 5.0
DEFAULT_TTL = 3600.0
#: Above this, stop resolving and leave the rest as GUIDs. A partly-named list
#: is more useful than a page that took a minute to build.
MAX_LOOKUPS = 200
MAX_WORKERS = 6

_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()


def _cached(guid: str, ttl: float):
    with _lock:
        hit = _cache.get(guid)
    if not hit:
        return None
    at, display = hit
    if time.monotonic() - at > ttl:
        return None
    return (display,)          # tuple so a cached None is distinguishable


def _store(guid: str, display: str | None) -> None:
    with _lock:
        _cache[guid] = (time.monotonic(), display)


def lookup_display(guid: str, base_url: str, *, timeout: float = DEFAULT_TIMEOUT,
                   ttl: float = DEFAULT_TTL) -> str | None:
    """One concept's display name, or None. Never raises."""
    if not guid or not base_url:
        return None
    hit = _cached(guid, ttl)
    if hit is not None:
        return hit[0]
    try:
        r = requests.get(
            f"{base_url.rstrip('/')}/api/v1/CodeSystem/$lookup",
            params={"system": PLAN_LOCAL_CS_URL, "code": guid},
            timeout=timeout)
    except requests.RequestException as e:
        # Transient — do NOT cache, so the next page retries. Caching a
        # reachability blip would blank the names for an hour.
        logger.info("plan lookup for %s failed (%s); keeping the GUID",
                    guid[:8], type(e).__name__)
        return None
    if r.status_code != 200:
        _store(guid, None)     # a clean "no display" IS cacheable
        return None
    try:
        body = r.json() or {}
    except ValueError:
        _store(guid, None)
        return None
    display = None
    for p in body.get("parameter") or []:
        if p.get("name") == "display" and "valueString" in p:
            display = p["valueString"]
            break
    _store(guid, display)
    return display


def fill_displays(concepts: list[dict], base_url: str, *,
                  timeout: float = DEFAULT_TIMEOUT,
                  ttl: float = DEFAULT_TTL) -> int:
    """Fill `display` on merged concepts that lack one. Returns how many it set.

    Only `plan_concept` entries with a `concept_guid` are looked up: an
    `external_code` already names itself (`loinc 41653-7`), and an `unbound`
    code resolves nowhere by definition.
    """
    if not base_url:
        return 0
    todo = [c for c in concepts
            if not c.get("display")
            and c.get("binding") == "plan_concept"
            and c.get("concept_guid")][:MAX_LOOKUPS]
    if not todo:
        return 0
    filled = 0
    try:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as pool:
            for c, display in zip(todo, pool.map(
                    lambda x: lookup_display(x["concept_guid"], base_url,
                                             timeout=timeout, ttl=ttl), todo)):
                if display:
                    c["display"] = display
                    filled += 1
    except Exception as e:                      # noqa: BLE001 — cosmetic
        logger.warning("concept display resolution failed (%s); the inventory "
                       "keeps its GUIDs", e)
    return filled
