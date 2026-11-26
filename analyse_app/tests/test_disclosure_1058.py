"""#1058 — every research CSV export reports one disclosure per patient to record.pdhc.

Record's HTTP boundary is mocked at ``requests.post`` inside app.disclosure,
not at our own client, so the real header/body/status handling runs.
Synthetic GUIDs only.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
import requests

from app import disclosure as D
from app.extensions import db
from app.models import AnalyseAudit, Cohort, DisclosureOutbox
from tests.conftest import make_app

P1 = "11111111-1111-4111-8111-111111111111"
P2 = "22222222-2222-4222-8222-222222222222"
P3 = "33333333-3333-4333-8333-333333333333"
VAR = "https://termbank.pdhc.se/CodeSystem/loinc/4548-4"
RECORD = "https://record.example.test"


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    @property
    def text(self):
        return ""


def _obs(pat, value, oid, vid="1"):
    return {"resourceType": "Observation", "id": oid,
            "meta": {"versionId": vid},
            "subject": {"reference": f"Patient/{pat}"},
            "code": {"coding": [{"system": "https://termbank.pdhc.se/CodeSystem/loinc",
                                 "code": "4548-4"}]},
            "effectiveDateTime": "2026-04-01T10:00:00+00:00",
            "valueQuantity": {"value": value, "unit": "%"}}


OBS = [_obs(P1, 6.4, "o-1", "2"), _obs(P1, 6.6, "o-2"), _obs(P2, 7.1, "o-3")]


def _cdr(method, url, params=None, json=None, headers=None, timeout=None):
    if "/api/v1/fhir/Observation" in url:
        return _Resp(200, {"entry": [{"resource": o} for o in OBS]})
    return _Resp(404)


def _app(**cfg):
    base = dict(CDR_ENDPOINTS=[{"cdr_id": "cdr1", "base_url": "http://cdr1",
                                "region_label": "R"}],
                RECORD_BASE_URL=RECORD, RECORD_DISCLOSURE_KEY="k-test",
                DISCLOSURE_SEND_MODE="inline")
    base.update(cfg)
    return make_app(**base)


@pytest.fixture
def members(ips_patients):
    ips_patients({P1, P2, P3})


def _cohort(app):
    with app.app_context():
        c = Cohort(guid="c0c0c0c0-0000-4000-8000-000000000001",
                   filter={"cdr_ids": ["cdr1"]}, members=[P1, P2, P3], n=3)
        db.session.add(c)
        db.session.commit()
        return c.guid


def _export(app, record_post):
    cid = _cohort(app)
    client = app.test_client()
    with patch("app.analyse.federation.requests.request", side_effect=_cdr), \
            patch("app.disclosure.requests.post", side_effect=record_post):
        r = client.get(f"/api/cohort/{cid}/export?format=csv&variables={VAR}")
        body = r.get_data(as_text=True)
        for t in list(D._threads):
            t.join(5)
    return r, body


def _rows(app):
    with app.app_context():
        return [(o.patient_guid, o.status, o.attempts, o.last_error, o.record_id,
                 o.payload) for o in
                db.session.query(DisclosureOutbox).order_by(DisclosureOutbox.patient_guid)]


# --- the AuditEvent itself ---------------------------------------------------

def _check_contract(ev, patient):
    """The parts the #1058 contract requires."""
    assert ev["resourceType"] == "AuditEvent"
    assert ev["meta"]["profile"] == [D.BALP["profile"]]
    assert ev["category"][0]["coding"][0] == D.BALP["category"]
    assert ev["code"]["coding"][0] == D.BALP["code"]
    assert ev["action"] == "R"
    assert ev["recorded"]
    assert ev["outcome"]["code"]["code"] == "success"
    assert ev["authorization"][0]["coding"][0]["code"] == "HRESCH"
    ident = {"system": "https://eps.pdhc.se/fhir/Patient", "value": patient}
    assert ev["patient"]["identifier"] == ident
    pat_ents = [e for e in ev["entity"]
                if e["role"]["coding"][0] == D.BALP["entity_patient_role"]]
    assert [e["what"]["identifier"] for e in pat_ents] == [ident]
    types = [a["type"]["coding"][0]["code"] for a in ev["agent"]]
    assert types == ["110153", "110152"]
    assert ev["agent"][0]["who"] == ev["source"]["observer"]
    assert ev["source"]["observer"]["identifier"]["value"] == "analyse.pdhc"
    sid = ev["identifier"][0]
    assert sid["system"] == "https://analyse.pdhc.se/disclosure"
    assert sid["value"].endswith(":" + patient)


def test_export_writes_one_disclosure_per_patient_in_the_file(members):
    app = _app()
    sent = []
    r, body = _export(app, lambda url, json=None, headers=None, timeout=None:
                      sent.append((url, headers, json)) or _Resp(201, {"id": "rec-" + json["patient"]["identifier"]["value"][:4]}))
    assert r.status_code == 200 and body.count("\n") == 4
    rows = _rows(app)
    # P3 is a member with no rows: nothing of theirs left, so no disclosure.
    assert [p for p, *_ in rows] == [P1, P2]
    assert all(s == "sent" and a == 1 for _, s, a, *_ in rows)
    assert rows[0][4] == "rec-1111"
    p1 = rows[0][5]
    _check_contract(p1, P1)
    _check_contract(rows[1][5], P2)
    refs = [e["what"].get("reference") for e in p1["entity"] if e["what"].get("reference")]
    assert refs == ["Observation/o-1/_history/2", "Observation/o-2/_history/1"]
    detail = {d["type"]["text"]: d for d in p1["entity"][1]["detail"]}
    assert detail["count"]["valueInteger"] == 2
    assert len(detail["sha256"]["valueString"]) == 64
    url, headers, _ = sent[0]
    assert url == RECORD + "/api/v1/disclosures"
    assert headers["X-Source-Service"] == "analyse.pdhc"
    assert headers["X-Service-Key"] == "k-test"
    assert headers["Content-Type"] == "application/fhir+json"
    with app.app_context():
        rec = db.session.query(AnalyseAudit).filter_by(route="research_export").one()
        assert rec.payload_snapshot["n_patients"] == 2
        assert rec.payload_snapshot["complete"] is True
        assert rec.payload_snapshot["sha256"] == detail["sha256"]["valueString"]


def test_duplicate_200_counts_as_sent(members):
    app = _app()
    _export(app, lambda *a, **k: _Resp(200, {"id": "existing"}))
    assert {s for _, s, *_ in _rows(app)} == {"sent"}


@pytest.mark.parametrize("status,name", [(400, "http_400: bad_profile"),
                                         (401, "http_401"), (403, "http_403")])
def test_refusal_keeps_rows_pending_with_the_name(members, status, name):
    app = _app()
    body = {"reason": "bad_profile"} if status == 400 else {}
    r, _ = _export(app, lambda *a, **k: _Resp(status, body))
    assert r.status_code == 200
    rows = _rows(app)
    assert {(s, a, e) for _, s, a, e, *_ in rows} == {("pending", 1, name)}


def test_not_configured_is_named_and_export_still_succeeds(members):
    app = _app(RECORD_BASE_URL="")
    called = []
    r, body = _export(app, lambda *a, **k: called.append(1))
    assert r.status_code == 200 and P1 in body
    assert called == []
    errs = {e for _, _, _, e, *_ in _rows(app)}
    assert errs == {"record_not_configured: RECORD_BASE_URL unset"}


def test_missing_key_is_named(members):
    app = _app(RECORD_DISCLOSURE_KEY="")
    _export(app, lambda *a, **k: _Resp(201))
    assert {e for _, _, _, e, *_ in _rows(app)} == {
        "record_not_configured: RECORD_DISCLOSURE_KEY unset"}


def test_transport_error_never_fails_the_export(members):
    app = _app()

    def boom(*a, **k):
        raise requests.ConnectionError("record down")
    r, body = _export(app, boom)
    assert r.status_code == 200 and body.count("\n") == 4
    rows = _rows(app)
    # Stops after the first transport error: one attempt, the rest untouched.
    assert [(s, a) for _, s, a, *_ in rows] == [("pending", 1), ("pending", 0)]
    assert rows[0][3] == "transport_error: ConnectionError"


def test_rolled_back_receipt_leaves_no_outbox_row(members, monkeypatch):
    """Same transaction: if the receipt's commit fails, no disclosure row
    survives either (and the export still completes)."""
    app = _app()
    real_add = db.session.add

    def add(obj):
        real_add(obj)
        if isinstance(obj, DisclosureOutbox):
            raise RuntimeError("simulated failure after the outbox row")
    with app.app_context():
        pass
    monkeypatch.setattr(db.session, "add", add, raising=False)
    r, body = _export(app, lambda *a, **k: _Resp(201))
    monkeypatch.undo()
    assert r.status_code == 200 and P1 in body
    with app.app_context():
        assert db.session.query(DisclosureOutbox).count() == 0
        assert db.session.query(AnalyseAudit).filter_by(
            route="research_export").count() == 0


def test_client_disconnect_still_reports_what_left(members):
    """A stream closed early still writes the receipt + disclosures."""
    app = _app()
    cid = _cohort(app)
    with patch("app.analyse.federation.requests.request", side_effect=_cdr), \
            patch("app.disclosure.requests.post", return_value=_Resp(201)):
        r = app.test_client().get(
            f"/api/cohort/{cid}/export?format=csv&variables={VAR}",
            buffered=False)
        it = iter(r.response)
        next(it)           # header chunk
        next(it)           # trailing rows chunk
        r.close()
    rows = _rows(app)
    assert [p for p, *_ in rows] == [P1, P2]


# --- never blocks the export ------------------------------------------------

def _timed_export(app, delay):
    def slow(*a, **k):
        time.sleep(delay)
        return _Resp(201)
    cid = _cohort(app)
    with patch("app.analyse.federation.requests.request", side_effect=_cdr), \
            patch("app.disclosure.requests.post", side_effect=slow):
        t0 = time.monotonic()
        r = app.test_client().get(f"/api/cohort/{cid}/export?format=csv&variables={VAR}")
        r.get_data()
        elapsed = time.monotonic() - t0
        for t in list(D._threads):
            t.join(10)
    return r, elapsed


def test_slow_record_does_not_hold_the_export(members):
    app = _app(DISCLOSURE_SEND_MODE="thread")
    r, elapsed = _timed_export(app, 0.6)
    assert r.status_code == 200
    assert elapsed < 0.5, elapsed
    assert {s for _, s, *_ in _rows(app)} == {"sent"}


def test_control_blocking_send_would_fail_the_check_above(members):
    """Control: the same export with the send made blocking (inline) takes
    two sends x 0.6 s, so `elapsed < 0.5` above discriminates."""
    app = _app(DISCLOSURE_SEND_MODE="inline")
    r, elapsed = _timed_export(app, 0.6)
    assert elapsed >= 1.0, elapsed


def test_control_send_without_the_guard_breaks_the_export(members, monkeypatch):
    """Control: if a send failure propagated (a blocking, unguarded send), the
    transport-error test above would fail — the export dies."""
    app = _app()

    def unguarded(app_, export_ref):
        with app_.app_context():
            row = db.session.query(DisclosureOutbox).first()
            D.post_event(app_.config, row.payload)
    monkeypatch.setattr(D, "send_after_commit", unguarded)

    def boom(*a, **k):
        raise requests.ConnectionError("record down")
    with pytest.raises(RuntimeError, match="transport_error"):
        _export(app, boom)


# --- CLI: send + backfill ----------------------------------------------------

def test_cli_sends_pending_and_is_idempotent(members):
    app = _app(DISCLOSURE_SEND_MODE="off")
    _export(app, lambda *a, **k: _Resp(201))
    assert {s for _, s, *_ in _rows(app)} == {"pending"}
    runner = app.test_cli_runner()
    with patch("app.disclosure.requests.post", return_value=_Resp(201)):
        dry = runner.invoke(args=["send-disclosures", "--dry-run"])
        assert "pending=2, would send=2, record: configured" in dry.output
        one = runner.invoke(args=["send-disclosures", "--limit", "1"])
        assert "sent=1" in one.output and "still_pending=1" in one.output
        rest = runner.invoke(args=["send-disclosures"])
        assert "sent=1" in rest.output and "still_pending=0" in rest.output
        again = runner.invoke(args=["send-disclosures"])
        assert "sent=0" in again.output


def test_cli_failure_exits_nonzero_and_names_the_error(members):
    app = _app(DISCLOSURE_SEND_MODE="off", RECORD_BASE_URL="")
    _export(app, None)
    res = app.test_cli_runner().invoke(args=["send-disclosures"])
    assert res.exit_code == 1
    assert "record_not_configured: RECORD_BASE_URL unset" in res.output


def _old_receipt(app, export_id, cohort_id, n_rows):
    with app.app_context():
        db.session.add(AnalyseAudit(
            user_guid="aaaaaaaa-0000-4000-8000-00000000000a", user_org_guids=[],
            route="research_export", n_rows_returned=n_rows, response_status=200,
            payload_snapshot={"export_id": export_id, "cohort_id": cohort_id,
                              "variables": [VAR]}))
        db.session.commit()


def test_backfill_counts_dry_run_then_apply(members):
    app = _app(DISCLOSURE_SEND_MODE="off")
    cid = _cohort(app)
    _old_receipt(app, "e-old-1", cid, 5)          # 3 members -> 3 events
    _old_receipt(app, "e-old-2", cid, 0)          # empty export
    _old_receipt(app, "e-old-3", "gone-cohort", 4)
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["send-disclosures", "--backfill"])
    assert "backfill DRY RUN" in dry.output
    assert "receipts=3" in dry.output and "events=3" in dry.output
    assert "empty_export=1" in dry.output and "cohort_missing=1" in dry.output
    assert "written=0" in dry.output
    with app.app_context():
        assert db.session.query(DisclosureOutbox).count() == 0
    applied = runner.invoke(args=["send-disclosures", "--backfill", "--apply"])
    assert "written=3" in applied.output
    again = runner.invoke(args=["send-disclosures", "--backfill", "--apply"])
    assert "already_reported=1" in again.output and "written=0" in again.output
    rows = _rows(app)
    assert [p for p, *_ in rows] == [P1, P2, P3]
    ev = rows[0][5]
    _check_contract(ev, P1)
    assert ev["identifier"][0]["value"] == f"e-old-1:{P1}"
    detail = {d["type"]["text"]: d for d in ev["entity"][1]["detail"]}
    assert detail["backfilled"]["valueBoolean"] is True
    assert "count" not in detail


def test_apply_without_backfill_is_refused(members):
    app = _app()
    res = app.test_cli_runner().invoke(args=["send-disclosures", "--apply"])
    assert res.exit_code != 0


def test_unset_record_base_url_has_no_fallback(monkeypatch):
    monkeypatch.delenv("RECORD_BASE_URL", raising=False)
    from app import create_app
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    assert app.config["RECORD_BASE_URL"] == ""
    with pytest.raises(D.NotConfigured, match="RECORD_BASE_URL unset"):
        D._endpoint(app.config)
