"""Every env var the operator is told to set must actually reach config.

#719 asked the operator to put an ips ApiKey in analyse's `.env` as
`IPS_API_KEY`. The code read it — `node/service.py` and `node/reader.py` both do
`config.get("IPS_API_KEY")` — but nothing ever put it there from the
environment. So the operator edited the right file, the variable reached the
container, and the smoke still said "IPS_API_KEY is not configured": true of the
config, false of the environment. The instruction was a correct specification
that the code had never implemented.

This is the shape to guard, not the one instance. A variable that is documented
and read but never wired fails silently in the one direction nobody tests,
because a unit test supplies config directly and never goes through os.environ.
"""
from __future__ import annotations

import os
import pathlib
import re

from tests.conftest import make_app

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _env_example_keys() -> set[str]:
    out = set()
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        out.add(line.split("=", 1)[0].strip())
    return out


def _keys_read_from_config() -> set[str]:
    """Config keys the app actually consults, by source inspection."""
    pat = re.compile(r"""config(?:\.get\(|\[)\s*["']([A-Z][A-Z0-9_]+)["']""")
    found = set()
    for py in (ROOT / "app").rglob("*.py"):
        found |= set(pat.findall(py.read_text()))
    return found


def test_ips_api_key_reaches_config_from_the_environment(monkeypatch):
    """The specific regression. Fails without the __init__.py wiring."""
    monkeypatch.setenv("IPS_API_KEY", "from-the-environment")
    app = make_app()
    assert app.config.get("IPS_API_KEY") == "from-the-environment"


def test_documented_and_read_env_vars_are_all_wired():
    """Any key that .env.example tells the operator to set AND that the code
    reads from config must be present in a freshly built config."""
    documented = _env_example_keys()
    read = _keys_read_from_config()
    both = documented & read
    assert both, "sanity: expected some overlap between .env.example and config reads"

    cfg = make_app().config
    missing = sorted(k for k in both if k not in cfg)
    assert not missing, (
        "these are documented in .env.example and read from config, but nothing "
        "puts them there from the environment — an operator who sets them is "
        f"ignored silently: {missing}"
    )
