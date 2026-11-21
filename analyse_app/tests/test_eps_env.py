"""#1022 (epic #1015): analyse reads EPS_* with IPS_* as the fallback.

ips.pdhc is being renamed eps.pdhc. Until phase 4 removes the old names the
shared .env (app + three nodes) may carry EPS_*, IPS_* or both: the new name
must win, the old one must still work alone, and with neither set analyse must
behave as before — unset, so the spärr gate and the cohort routes refuse by
name rather than calling an invented host (#833).
"""
from __future__ import annotations

import ast
import pathlib
from unittest.mock import patch

import pytest

from app.eps_env import eps_env
from app.services.ips_client import IpsClient
from tests.conftest import make_app

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
NAMES = ("EPS_BASE_URL", "EPS_API_KEY", "IPS_BASE_URL", "IPS_API_KEY")


@pytest.fixture
def clean_env(monkeypatch):
    for n in NAMES:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


# ── the helper ──────────────────────────────────────────────────────────────

def test_new_name_wins(clean_env):
    clean_env.setenv("EPS_BASE_URL", "http://eps.test")
    clean_env.setenv("IPS_BASE_URL", "http://ips.test")
    assert eps_env("BASE_URL") == "http://eps.test"


def test_old_name_still_works(clean_env):
    clean_env.setenv("IPS_API_KEY", "old-key")
    assert eps_env("API_KEY") == "old-key"


def test_empty_new_name_falls_back_to_old(clean_env):
    clean_env.setenv("EPS_API_KEY", "")
    clean_env.setenv("IPS_API_KEY", "old-key")
    assert eps_env("API_KEY") == "old-key"


def test_both_unset_is_unset(clean_env):
    assert eps_env("BASE_URL") == ""
    assert eps_env("API_KEY", None) is None


# ── wired into config (the app and all three nodes build config this way) ──

def test_config_prefers_eps_names(clean_env):
    clean_env.setenv("EPS_BASE_URL", "http://eps.test")
    clean_env.setenv("EPS_API_KEY", "new-key")
    clean_env.setenv("IPS_BASE_URL", "http://ips.test")
    clean_env.setenv("IPS_API_KEY", "old-key")
    cfg = make_app().config
    assert (cfg["IPS_BASE_URL"], cfg["IPS_API_KEY"]) == ("http://eps.test", "new-key")


def test_config_still_reads_ips_names(clean_env):
    clean_env.setenv("IPS_BASE_URL", "http://ips.test")
    clean_env.setenv("IPS_API_KEY", "old-key")
    cfg = make_app().config
    assert (cfg["IPS_BASE_URL"], cfg["IPS_API_KEY"]) == ("http://ips.test", "old-key")


def test_config_with_neither_set_is_empty(clean_env):
    cfg = make_app().config
    assert (cfg["IPS_BASE_URL"], cfg["IPS_API_KEY"]) == ("", "")


# ── IpsClient's own environment fallback ───────────────────────────────────

def test_client_fallback_prefers_eps(clean_env):
    clean_env.setenv("EPS_BASE_URL", "http://eps.test/")
    clean_env.setenv("IPS_BASE_URL", "http://ips.test/")
    assert IpsClient(token="t").base_url == "http://eps.test"


def test_client_fallback_still_reads_ips(clean_env):
    clean_env.setenv("IPS_BASE_URL", "http://ips.test/")
    assert IpsClient(token="t").base_url == "http://ips.test"


def test_client_with_neither_set_has_no_base_url(clean_env):
    assert IpsClient(token="t").base_url == ""


# ── no read of the old names bypasses the helper ───────────────────────────

def _env_reads_of_ips(path: pathlib.Path) -> list[str]:
    """os.environ.get("IPS_…") / os.environ["IPS_…"] / os.getenv("IPS_…"),
    found on the parse tree so a comment or docstring cannot match."""
    hits = []
    for node in ast.walk(ast.parse(path.read_text())):
        arg = None
        if isinstance(node, ast.Call) and node.args:
            f = ast.unparse(node.func)
            if f in ("os.environ.get", "os.getenv", "environ.get"):
                arg = node.args[0]
        elif isinstance(node, ast.Subscript) and \
                ast.unparse(node.value) in ("os.environ", "environ"):
            arg = node.slice
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                and arg.value.startswith("IPS_"):
            hits.append(f"{path.name}:{node.lineno}")
    return hits


def test_no_direct_environment_read_of_an_IPS_name():
    files = list((APP_ROOT / "app").rglob("*.py")) + \
        list((REPO_ROOT / "deploy").glob("*.py"))
    assert len(files) > 20, "sanity: the walk must actually find the source"
    hits = [h for f in files for h in _env_reads_of_ips(f)]
    assert not hits, f"read IPS_* through app.eps_env.eps_env instead: {hits}"


def test_the_guard_sees_a_direct_read(tmp_path):
    """Control: the guard above must be able to fail."""
    p = tmp_path / "probe.py"
    p.write_text('import os\nX = os.environ["IPS_API_KEY"]\n'
                 'Y = os.environ.get("IPS_BASE_URL", "")\n'
                 '# os.environ["IPS_API_KEY"] in a comment is not a read\n')
    assert _env_reads_of_ips(p) == ["probe.py:2", "probe.py:3"]


# ── the researcher's predicate label ───────────────────────────────────────

def test_the_registry_predicate_is_labelled_eps(client, ips_patients):
    ips_patients({"p1"})
    with patch("app.analyse.federation.requests.request",
               side_effect=AssertionError("no CDR call expected")):
        r = client.post("/api/cohort", json={"cdr_ids": [], "demographics": {}})
    assert r.status_code == 201
    sources = [p["source"] for p in r.get_json()["summary"]["predicates"]
               if p["resource_type"] == "Patient"]
    assert sources == ["eps.pdhc"]
