"""Disclosures (utlämnanden) reported to record.pdhc as IHE BALP AuditEvents (#1058).

What counts as a disclosure in analyse.pdhc
===========================================
Only the researcher CSV export, ``GET /api/cohort/<id>/export``. It is the one
place where identifiable per-patient data LEAVES analyse as a file the
researcher keeps: one row per observation, carrying the eps patient GUID, the
value and the time. Its receipt is the ``analyse_audit`` row with
route='research_export', and every patient with at least one row in the file
gets one AuditEvent.

Not disclosures, and why (see progress.md 2026-11-26 for the full table):

- ``POST /analysis/export.csv`` — aggregates after disclosure control
  (k-suppressed cells, no patient key). Nothing in it names a patient.
- histogram / boxplot / trend / ``/api/v1/stats`` / node runs — aggregates
  or pseudonymous, disclosure-controlled partials.
- cohort preview and scatter — patient-level, but shown on screen inside the
  analysis tool, not handed over; each is already an ``analyse_audit`` READ.
- ``/api/v1/observations``, ``/api/v1/canonical``, ``/api/v1/openehr`` —
  per-patient rows, but to ``gateway.pdhc`` only (PATIENT_DATA_CALLERS), a
  PDHC service. If gateway hands them on, that hand-over is gateway's
  disclosure to report, not analyse's.

The wire contract (fixed with record.pdhc)
==========================================
``POST {RECORD_BASE_URL}/api/v1/disclosures`` with ``X-Source-Service``,
``X-Service-Key`` (``RECORD_DISCLOSURE_KEY``) and
``Content-Type: application/fhir+json``; one AuditEvent per patient. 201 is
new, 200 a duplicate of the same ``source_event_id`` — both mean "record has
it". A 400 means the event itself is invalid: the row becomes ``rejected``
(terminal) with ``http_400: <reason>``. 401/403/5xx/network leave it
``pending`` with the reason NAMED, for ``flask send-disclosures``.

``RECORD_BASE_URL`` has NO fallback. Unset reads as unset and is reported by
name (``record_not_configured: RECORD_BASE_URL unset``); it is never replaced
by a loopback or public default (CLAUDE.md §7, #833).
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

SERVICE_NAME = "analyse.pdhc"

#: Every code the record agent also pins. A mismatch with record.pdhc is a
#: one-line fix HERE. BALP is written for R4: its ``type`` is R5 ``category``,
#: ``subtype`` is R5 ``code`` and ``purposeOfEvent`` is R5 ``authorization``.
BALP = {
    "profile": "https://profiles.ihe.net/ITI/BALP/StructureDefinition/IHE.BasicAudit.PrivacyDisclosure.Source",
    "category": {"system": "http://dicom.nema.org/resources/ontology/DCM",
                 "code": "110106", "display": "Export"},
    "code": {"system": "http://terminology.hl7.org/CodeSystem/iso-21089-lifecycle",
             "code": "disclose", "display": "Disclose Record Lifecycle Event"},
    "action": "R",
    "agent_source": {"system": "http://dicom.nema.org/resources/ontology/DCM",
                     "code": "110153", "display": "Source Role ID"},
    "agent_recipient": {"system": "http://dicom.nema.org/resources/ontology/DCM",
                        "code": "110152", "display": "Destination Role ID"},
    "entity_patient_role": {"system": "http://terminology.hl7.org/CodeSystem/object-role",
                            "code": "1", "display": "Patient"},
    "entity_data_role": {"system": "http://terminology.hl7.org/CodeSystem/object-role",
                         "code": "4", "display": "Domain Resource"},
    "purpose_system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
    "patient_system": "https://eps.pdhc.se/fhir/Patient",
    "service_system": "https://pdhc.se/fhir/service",
}

#: analyse's exports are research (Lag 2022:913 secondary use).
PURPOSE = "HRESCH"
IDENTIFIER_SYSTEM = "https://analyse.pdhc.se/disclosure"
USER_SYSTEM = "https://sso.pdhc.se/fhir/user"
OUTCOME_SYSTEM = "http://terminology.hl7.org/CodeSystem/issue-severity"
SEND_TIMEOUT = 5.0
#: R5 has no AuditEvent.identifier; record.pdhc reads the source event id
#: from this extension AND the top-level identifier, and they must agree.
SOURCE_EVENT_ID_EXT = "https://record.pdhc.se/fhir/StructureDefinition/source-event-id"
#: record.pdhc's limit is 5000 data entities; one is the summary.
MAX_REFERENCES = 4999
#: record.pdhc accepts relative Type/id(/_history/v) only.
_REF_RE = re.compile(r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-.]{1,64}(/_history/[A-Za-z0-9\-.]{1,64})?$")


class NotConfigured(RuntimeError):
    """RECORD_BASE_URL or RECORD_DISCLOSURE_KEY is unset."""


def source_event_id(export_id: str, patient_guid: str) -> str:
    return f"{export_id}:{patient_guid}"


def _service_ref() -> dict:
    return {"identifier": {"system": BALP["service_system"], "value": SERVICE_NAME},
            "display": SERVICE_NAME}


def build_audit_event(*, export_id: str, patient_guid: str, recorded: datetime,
                      recipient_user: str | None, description: str,
                      count: int | None, sha256: str | None = None,
                      references: Iterable[str] = (),
                      extra_details: dict[str, Any] | None = None) -> dict:
    """One BALP Privacy Disclosure (Source side) for one patient.

    ``references`` are version-specific (``Observation/<id>/_history/<v>``)
    where the CDR gave a version, else plain ``Observation/<id>``.
    """
    patient_ident = {"system": BALP["patient_system"], "value": str(patient_guid)}
    details: list[dict] = [{"type": {"text": "description"},
                            "valueString": description}]
    if count is not None:
        details.append({"type": {"text": "count"}, "valueInteger": int(count)})
    if sha256:
        details.append({"type": {"text": "sha256"}, "valueString": sha256})
    for k, v in (extra_details or {}).items():
        if isinstance(v, bool):
            details.append({"type": {"text": k}, "valueBoolean": v})
        elif isinstance(v, int):
            details.append({"type": {"text": k}, "valueInteger": v})
        else:
            details.append({"type": {"text": k}, "valueString": str(v)})

    entities = [
        {"what": {"identifier": patient_ident},
         "role": {"coding": [BALP["entity_patient_role"]]}},
        {"what": {"display": description},
         "role": {"coding": [BALP["entity_data_role"]]},
         "detail": details},
    ]
    refs = [r for r in references if r and _REF_RE.match(r)]
    if len(refs) > MAX_REFERENCES:
        # Too many for one event: the summary's description + count stand in.
        details.append({"type": {"text": "references_omitted"},
                        "valueInteger": len(refs)})
        refs = []
    for ref in refs:
        entities.append({"what": {"reference": ref},
                         "role": {"coding": [BALP["entity_data_role"]]}})

    recipient_who = ({"identifier": {"system": USER_SYSTEM, "value": str(recipient_user)},
                      "display": "researcher (export requester)"}
                     if recipient_user else {"display": "unknown requester"})
    when = recorded if recorded.tzinfo else recorded.replace(tzinfo=timezone.utc)
    sid = {"system": IDENTIFIER_SYSTEM,
           "value": source_event_id(export_id, patient_guid)}
    return {
        "resourceType": "AuditEvent",
        "meta": {"profile": [BALP["profile"]]},
        "extension": [{"url": SOURCE_EVENT_ID_EXT, "valueIdentifier": dict(sid)}],
        "identifier": [dict(sid)],
        "category": [{"coding": [BALP["category"]]}],
        "code": {"coding": [BALP["code"]]},
        "action": BALP["action"],
        "recorded": when.isoformat(),
        "outcome": {"code": {"system": OUTCOME_SYSTEM, "code": "success"}},
        "authorization": [{"coding": [{"system": BALP["purpose_system"],
                                       "code": PURPOSE}]}],
        "patient": {"identifier": patient_ident},
        "source": {"observer": _service_ref()},
        "agent": [
            {"type": {"coding": [BALP["agent_source"]]}, "who": _service_ref(),
             "requestor": False},
            {"type": {"coding": [BALP["agent_recipient"]]}, "who": recipient_who,
             "requestor": True},
        ],
        "entity": entities,
    }


def enqueue(session, *, export_id: str, patient_guid: str, event: dict):
    """Add (NOT commit) an outbox row; the caller's commit is the export's."""
    from app.models import DisclosureOutbox
    row = DisclosureOutbox(
        source_event_id=source_event_id(export_id, patient_guid),
        patient_guid=str(patient_guid),
        export_ref=str(export_id),
        payload=event,
        status="pending",
        attempts=0,
    )
    session.add(row)
    return row


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _endpoint(config) -> tuple[str, str]:
    base = (config.get("RECORD_BASE_URL") or "").strip()
    key = (config.get("RECORD_DISCLOSURE_KEY") or "").strip()
    if not base:
        raise NotConfigured("record_not_configured: RECORD_BASE_URL unset")
    if not key:
        raise NotConfigured("record_not_configured: RECORD_DISCLOSURE_KEY unset")
    return base.rstrip("/") + "/api/v1/disclosures", key


def post_event(config, event: dict) -> str | None:
    """POST one AuditEvent. Returns record's id on 200/201; raises otherwise.

    The exception's str() is the NAME stored in last_error."""
    url, key = _endpoint(config)
    try:
        r = requests.post(url, json=event, timeout=SEND_TIMEOUT, headers={
            "X-Source-Service": SERVICE_NAME,
            "X-Service-Key": key,
            "Content-Type": "application/fhir+json",
        })
    except requests.RequestException as exc:
        raise RuntimeError(f"transport_error: {type(exc).__name__}") from exc
    if r.status_code in (200, 201):
        try:
            body = r.json() or {}
        except ValueError:
            body = {}
        rid = body.get("id") if isinstance(body, dict) else None
        return str(rid) if rid is not None else None
    reason = ""
    try:
        body = r.json()
        if isinstance(body, dict):
            reason = str(body.get("error") or body.get("reason") or "")[:120]
    except ValueError:
        pass
    raise RuntimeError(f"http_{r.status_code}" + (f": {reason}" if reason else ""))


def send_rows(session, config, rows) -> dict:
    """Send the given pending rows; commit their new state. Never raises for a
    send failure. Stops after the first transport error, so an unreachable
    record.pdhc costs one timeout, not one per patient."""
    out = {"sent": 0, "failed": 0, "skipped": 0}
    rows = [r for r in rows if r.status == "pending"]
    try:
        _endpoint(config)
    except NotConfigured as exc:
        # No network involved: name the reason on EVERY row, so the outbox
        # itself says why nothing left.
        for row in rows:
            if row.status != "sent":
                row.attempts = (row.attempts or 0) + 1
                row.last_error = str(exc)
                out["failed"] += 1
        session.commit()
        return out
    stop = False
    for row in rows:
        if row.status == "sent":
            continue
        if stop:
            out["skipped"] += 1
            continue
        row.attempts = (row.attempts or 0) + 1
        try:
            rid = post_event(config, row.payload)
        except Exception as exc:  # noqa: BLE001 — the name is the record
            row.last_error = str(exc)[:500]
            out["failed"] += 1
            if str(exc).startswith("http_400"):
                # record says the event itself is wrong: re-sending the same
                # payload can never succeed. Terminal, kept for a human.
                row.status = "rejected"
            if str(exc).startswith("transport_error"):
                stop = True
        else:
            row.status = "sent"
            row.record_id = rid
            row.last_error = None
            row.sent_at = datetime.now(timezone.utc)
            out["sent"] += 1
    session.commit()
    return out


def send_pending(session, config, *, limit: int | None = None,
                 export_ref: str | None = None) -> dict:
    from app.models import DisclosureOutbox
    q = session.query(DisclosureOutbox).filter(DisclosureOutbox.status == "pending")
    if export_ref:
        q = q.filter(DisclosureOutbox.export_ref == export_ref)
    q = q.order_by(DisclosureOutbox.id)
    if limit:
        q = q.limit(limit)
    return send_rows(session, config, q.all())


#: Background senders started by send_after_commit; tests join them.
_threads: list[threading.Thread] = []


def send_after_commit(app, export_ref: str) -> None:
    """Try to deliver one export's rows now. NEVER raises, never blocks.

    DISCLOSURE_SEND_MODE: 'thread' (default) sends in a daemon thread so the
    export's response is not held open; 'inline' sends in the caller (tests);
    'off' leaves everything to ``flask send-disclosures``.
    """
    mode = str(app.config.get("DISCLOSURE_SEND_MODE", "thread")).lower()
    if mode == "off":
        return

    def _run():
        try:
            with app.app_context():
                from app.extensions import db
                try:
                    res = send_pending(db.session, app.config, export_ref=export_ref)
                    if res["failed"] or res["skipped"]:
                        log.warning("disclosure send for export %s: %s "
                                    "(pending rows stay for send-disclosures)",
                                    export_ref, res)
                finally:
                    db.session.remove()
        except Exception as exc:  # noqa: BLE001
            log.warning("disclosure send for export %s failed: %s",
                        export_ref, type(exc).__name__)

    if mode == "inline":
        _run()
        return
    t = threading.Thread(target=_run, name=f"disclose-{export_ref[:8]}",
                         daemon=True)
    _threads[:] = [x for x in _threads if x.is_alive()]
    _threads.append(t)
    t.start()



# ---------------------------------------------------------------------------
# Backfill: exports that happened before #1058
# ---------------------------------------------------------------------------

BACKFILL_NOTE = ("backfilled from the export receipt: the receipt predates "
                 "#1058 and did not record which patients had rows, so the "
                 "patient set is the cohort's members (an upper bound) and "
                 "rows per patient are unknown")


def backfill(session, *, apply: bool = False) -> dict:
    """Outbox rows for every research_export receipt that has none.

    A receipt before #1058 names export_id, cohort_id, variables and the
    total row count, not the patients. Cohorts are immutable once defined, so
    the members are exactly who the export could have contained; a member
    with no rows is reported too (over-reporting a disclosure is the safe
    direction — under-reporting is the one the log exists to prevent).
    """
    from app.models import AnalyseAudit, Cohort, DisclosureOutbox

    counts = {"receipts": 0, "already_reported": 0, "empty_export": 0,
              "cohort_missing": 0, "exports_to_backfill": 0,
              "events": 0, "written": 0}
    receipts = (session.query(AnalyseAudit)
                .filter(AnalyseAudit.route == "research_export")
                .order_by(AnalyseAudit.timestamp).all())
    have = {r for (r,) in session.query(DisclosureOutbox.export_ref).distinct()}
    existing_ids = {s for (s,) in session.query(DisclosureOutbox.source_event_id)}
    for rec in receipts:
        counts["receipts"] += 1
        snap = rec.payload_snapshot if isinstance(rec.payload_snapshot, dict) else {}
        export_id = snap.get("export_id")
        cohort_id = snap.get("cohort_id")
        if not export_id or export_id in have:
            counts["already_reported"] += 1
            continue
        if not rec.n_rows_returned:
            counts["empty_export"] += 1
            continue
        cohort = session.get(Cohort, cohort_id) if cohort_id else None
        if cohort is None:
            counts["cohort_missing"] += 1
            continue
        counts["exports_to_backfill"] += 1
        variables = list(snap.get("variables") or [])
        user = rec.user_guid if rec.user_guid and rec.user_guid != "anonymous" else None
        for pat in sorted(set(cohort.members or [])):
            if source_event_id(export_id, pat) in existing_ids:
                continue
            counts["events"] += 1
            if not apply:
                continue
            event = build_audit_event(
                export_id=export_id, patient_guid=pat,
                recorded=rec.timestamp, recipient_user=user,
                description=(f"analyse.pdhc research CSV export {export_id} of "
                             f"cohort {cohort_id}, variables "
                             f"{', '.join(variables)}; {BACKFILL_NOTE}"),
                # record requires a count beside the description; the true
                # per-patient count is unknown, so 0 + count_known=false.
                count=0,
                extra_details={"count_known": False,
                               "export_total_rows": int(rec.n_rows_returned),
                               "backfilled": True},
            )
            enqueue(session, export_id=export_id, patient_guid=pat, event=event)
            counts["written"] += 1
    if apply:
        session.commit()
    return counts


def register_cli(app) -> None:
    import click

    @app.cli.command("send-disclosures")
    @click.option("--limit", type=int, default=None,
                  help="Send at most N pending rows.")
    @click.option("--dry-run", is_flag=True,
                  help="Count what would be sent; no HTTP, no writes.")
    @click.option("--backfill", "do_backfill", is_flag=True,
                  help="Create outbox rows for exports made before #1058 "
                       "(dry-run unless --apply). Does not send.")
    @click.option("--apply", is_flag=True,
                  help="With --backfill: write the rows.")
    def send_disclosures(limit, dry_run, do_backfill, apply):
        """Report pending disclosures to record.pdhc (#1058)."""
        from app.extensions import db
        from app.models import DisclosureOutbox

        if do_backfill:
            res = backfill(db.session, apply=bool(apply) and not dry_run)
            mode = "APPLIED" if (apply and not dry_run) else "DRY RUN"
            click.echo(f"backfill {mode}: " + ", ".join(
                f"{k}={v}" for k, v in res.items()))
            return
        if apply:
            raise click.UsageError("--apply only goes with --backfill")
        pending = (db.session.query(DisclosureOutbox)
                   .filter(DisclosureOutbox.status == "pending").count())
        if dry_run:
            try:
                _endpoint(app.config)
                cfg = "configured"
            except NotConfigured as exc:
                cfg = str(exc)
            n = min(pending, limit) if limit else pending
            click.echo(f"dry run: pending={pending}, would send={n}, record: {cfg}")
            return
        res = send_pending(db.session, app.config, limit=limit)
        left = (db.session.query(DisclosureOutbox)
                .filter(DisclosureOutbox.status == "pending").count())
        click.echo(f"sent={res['sent']} failed={res['failed']} "
                   f"skipped={res['skipped']} still_pending={left}")
        if res["failed"]:
            errs = (db.session.query(DisclosureOutbox.last_error)
                    .filter(DisclosureOutbox.status == "pending",
                            DisclosureOutbox.last_error.isnot(None))
                    .distinct().limit(5).all())
            for (e,) in errs:
                click.echo(f"  last_error: {e}")
            raise SystemExit(1)
