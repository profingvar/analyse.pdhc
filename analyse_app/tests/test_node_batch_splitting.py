"""#826 — batching moved the row budget from per-patient to per-batch.

`PATIENTS_PER_REQUEST` went from 1 to 100, which collapses a 60-patient,
3-concept cohort from 180 requests per CDR to 3. But cdr has no offset
parameter and `Bundle.total` reports the PAGE SIZE, so `_search` treats a full
page as a truncated read and refuses — a figure over an unknown fraction of a
cohort reads exactly like a real figure.

With one patient per request that ceiling was effectively per-patient. With a
batch it is per-batch, and the arithmetic is not comfortable: measured
2026-10-15, cdr2 returns ~650 rows per patient with no concept filter, so a
46-patient batch reaches MAX_COUNT = 30000. Shipping the batch size without
this would have turned a working cohort read into a hard failure at around 46
patients — and done it only on the larger cohorts, which are the ones that
matter.

The recovery is subdivision: half the cohort is half the rows, and the two
halves together are the same answer. Row density is a property of the DATA,
not of the client, so adapting beats guessing a smaller fixed batch.

A single patient that fills the page is NOT recoverable and must still raise.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.node.reader import (MAX_COUNT, NodeReader, ReadError, TruncatedRead)


def _obs(patient, n):
    return [{
        "resource": {
            "resourceType": "Observation",
            "id": f"{patient}-{i}",
            "subject": {"reference": f"Patient/{patient}"},
            "code": {"coding": [{"system": "urn:pdhc:concept", "code": "c1"}]},
            "valueQuantity": {"value": 1.0, "unit": "kg"},
            "effectiveDateTime": "2026-01-01T00:00:00Z",
        }} for i in range(n)]


class _Cdr:
    """A CDR whose page fills unless the batch is small enough.

    `rows_per_patient` x len(batch) >= MAX_COUNT is the truncation condition,
    which is exactly the real one.
    """

    def __init__(self, rows_per_patient):
        self.rpp = rows_per_patient
        self.batches: list[list[str]] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        batch = (params or {}).get("patient", "").split(",")
        batch = [b for b in batch if b]
        self.batches.append(batch)
        entries = []
        for p in batch:
            entries.extend(_obs(p, self.rpp))
        entries = entries[:MAX_COUNT]          # the CDR's own page cap

        class R:
            status_code = 200

            @staticmethod
            def json():
                return {"resourceType": "Bundle", "type": "searchset",
                        "total": len(entries), "entry": entries}
        return R()


def _reader():
    return NodeReader(base_url="http://cdr", service_key="k")


class TestTruncationIsRecoveredBySplitting:

    def test_a_batch_that_fills_the_page_is_SPLIT_not_refused(self):
        # 40 patients x 1000 rows = 40000 >= MAX_COUNT on the first attempt.
        cdr = _Cdr(rows_per_patient=1000)
        guids = [f"p{i:04d}" for i in range(40)]
        with patch("app.node.reader.requests.get", side_effect=cdr):
            rows = _reader().read_observations(
                purpose="statistics", patient_guids=guids)
        assert rows, "the read refused instead of splitting"
        assert {r["patient_guid"] for r in rows} == set(guids), (
            "splitting lost patients")

    def test_the_split_returns_EVERY_row_not_just_the_first_half(self):
        cdr = _Cdr(rows_per_patient=1000)
        guids = [f"p{i:04d}" for i in range(40)]
        with patch("app.node.reader.requests.get", side_effect=cdr):
            rows = _reader().read_observations(
                purpose="statistics", patient_guids=guids)
        assert len(rows) == 40 * 1000

    def test_it_actually_narrowed_the_batches_it_retried(self):
        cdr = _Cdr(rows_per_patient=1000)
        guids = [f"p{i:04d}" for i in range(40)]
        with patch("app.node.reader.requests.get", side_effect=cdr):
            _reader().read_observations(purpose="statistics",
                                        patient_guids=guids)
        sizes = [len(b) for b in cdr.batches]
        assert sizes[0] == 40, "the first attempt should be the full batch"
        assert min(sizes) < 40, "nothing was subdivided"
        # Halving, so every retry is at most half its parent.
        assert max(sizes[1:]) <= 20

    def test_a_batch_that_FITS_is_not_split_at_all(self):
        # The cost of this recovery must be zero when it is not needed.
        cdr = _Cdr(rows_per_patient=10)
        guids = [f"p{i:04d}" for i in range(40)]
        with patch("app.node.reader.requests.get", side_effect=cdr):
            rows = _reader().read_observations(
                purpose="statistics", patient_guids=guids)
        assert len(cdr.batches) == 1
        assert len(rows) == 400


class TestASinglePatientStillRefuses:

    def test_one_patient_filling_the_page_RAISES(self):
        # Nothing left to subdivide. Returning a partial answer here is the
        # failure the refusal exists to prevent.
        cdr = _Cdr(rows_per_patient=MAX_COUNT + 1)
        with patch("app.node.reader.requests.get", side_effect=cdr):
            with pytest.raises(TruncatedRead):
                _reader().read_observations(
                    purpose="statistics", patient_guids=["p0001"])

    def test_it_is_still_a_ReadError_so_existing_handlers_catch_it(self):
        # `app/node/service.py` and the runner catch ReadError; a new subclass
        # must not slip past them.
        assert issubclass(TruncatedRead, ReadError)
        cdr = _Cdr(rows_per_patient=MAX_COUNT + 1)
        with patch("app.node.reader.requests.get", side_effect=cdr):
            with pytest.raises(ReadError):
                _reader().read_observations(
                    purpose="statistics", patient_guids=["p0001"])

    def test_a_cohort_where_ONE_patient_is_too_large_refuses_the_WHOLE_read(self):
        # Deliberate: the alternative is silently dropping that patient, which
        # changes the cohort without saying so.
        class Mixed(_Cdr):
            def __call__(self, url, params=None, headers=None, timeout=None):
                batch = [b for b in (params or {}).get("patient", "").split(",") if b]
                self.batches.append(batch)
                entries = []
                for p in batch:
                    entries.extend(_obs(p, MAX_COUNT + 1 if p == "p0000" else 1))
                entries = entries[:MAX_COUNT]

                class R:
                    status_code = 200

                    @staticmethod
                    def json():
                        return {"total": len(entries), "entry": entries}
                return R()

        cdr = Mixed(rows_per_patient=0)
        with patch("app.node.reader.requests.get", side_effect=cdr):
            with pytest.raises(TruncatedRead):
                _reader().read_observations(
                    purpose="statistics",
                    patient_guids=[f"p{i:04d}" for i in range(8)])
