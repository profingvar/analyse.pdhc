"""Who may call the federated analyse endpoints — #727.

This is deliberately a separate module from ``app/analyse/purpose.py``, because
the two answer different questions. ``purpose.py`` constrains **what a caller
may declare it is reading for**; this module constrains **who may call at all,
and for which class of endpoint**. #700 does not resolve #727 and #727 does not
resolve #700 — a caller that passes the purpose gate still has to be on the
list here, and a caller on the list here still has to declare.

Until #727 this policy was one literal, ``{"gateway.pdhc", "monitor.pdhc"}``,
spelled out four times — in ``canonical.py``, ``openehr.py``,
``observations_search.py`` and ``stats.py``. Four copies of one rule do not
drift on the day they are written; they drift on the day someone adds a caller
to three of them. That is the same shape #726 found in the operator vocabulary,
and it is worse here because the thing that drifts is an authorisation list.

The split is by **what the endpoint returns**, not by how much the caller is
trusted:

``PATIENT_DATA_CALLERS``
    Endpoints that fan a *named patient's* rows out of every CDR:
    ``/api/v1/canonical/<table>``, ``/api/v1/openehr/*``, and the
    ``/api/v1/observations`` search.

``AGGREGATE_CALLERS``
    Endpoints that return per-CDR row counts and nothing patient-identifying:
    ``/api/v1/stats``.

``monitor.pdhc`` is in the second set only. It is not a service: it is a
synthetic service-key identity created 2026-04-28 so the Playwright, perf and
chaos suites could bypass SSO (``plans/*_2026-04-28.md``). A monitoring
identity needs to know that the CDRs are answering and roughly how much they
hold; it has never needed a named patient's observations. It was allowlisted on
all four endpoints because the list was written once and copied, not because
anything asked for it.

The rest of #727 — dropping ``monitor.pdhc`` from ``KNOWN_SERVICES``, unsetting
``MONITOR_PDHC_SERVICE_KEY`` on the hosts, rotating it — waits on whether
anything outside these repos still calls with that key, because that part can
break an unknown caller. Narrowing cannot: it is correct under either answer,
so it does not wait for one.
"""
from __future__ import annotations

from typing import Any, Iterable

PATIENT_DATA_CALLERS = frozenset({"gateway.pdhc"})
AGGREGATE_CALLERS = frozenset({"gateway.pdhc", "monitor.pdhc"})


def caller_check(blob: Any, allowed: Iterable[str]) -> tuple[dict, int] | None:
    """``None`` if this caller may proceed, else the ``(payload, status)`` to return.

    The 403 body is deliberately the same whichever list the caller missed, so
    a rejected caller learns that it is not allowed here and nothing about who
    is.
    """
    source = (blob or {}).get("service_source")
    if not source:
        return {"error": "service-key auth required"}, 401
    if source not in allowed:
        return {"error": "source service not allowed for this endpoint"}, 403
    return None
