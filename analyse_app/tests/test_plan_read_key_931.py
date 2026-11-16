"""#931 — analyse reads plan.pdhc with its own read_only service key, from the
environment, because the lookups run on a thread pool with no app context."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.analyse import concept_display as cd


def test_unset_key_sends_no_credential(monkeypatch):
    monkeypatch.delenv("PLAN_SERVICE_KEY", raising=False)
    assert cd.plan_read_headers() == {}


def test_lookups_on_the_THREAD_POOL_carry_the_key(monkeypatch):
    monkeypatch.setenv("PLAN_SERVICE_KEY", "k931")
    monkeypatch.delenv("PLAN_SOURCE_SERVICE", raising=False)
    cd._cache.clear()
    r = MagicMock(status_code=404)
    with patch.object(cd.requests, "get", return_value=r) as g:
        cd.fill_displays([{"concept_guid": f"g931-{i}", "binding": "plan_concept", "display": None}
                          for i in range(4)], "http://plan-test")
    assert g.call_count == 4
    for c in g.call_args_list:
        assert c.kwargs["headers"] == {"X-Source-Service": "analyse.pdhc", "X-Service-Key": "k931"}
