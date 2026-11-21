"""#1022 (epic #1015): ips.pdhc is becoming eps.pdhc.

The ONE place analyse reads the patient-registry environment variables. The
app, the three nodes (same image, same .env), ``IpsClient``'s own fallback and
the ``deploy/verify_*`` scripts all go through ``eps_env``.

``EPS_<name>`` is read first; ``IPS_<name>`` is the deprecated fallback until
phase 4 of #1015 removes it. An EMPTY ``EPS_<name>`` counts as unset and falls
through. The ``IPS_`` branch is ``os.environ.get`` unchanged, so a container
that carries only the old names behaves exactly as before — including both
absent giving ``default``, which is ``""`` at every call site: unset reads as
unset and the callers refuse by name. No default URL is invented here (#833).

Kept free of Flask so the deploy scripts can import it without an app.
Config KEYS stay ``IPS_BASE_URL`` / ``IPS_API_KEY``; only env names change.
"""
import os


def eps_env(name: str, default=""):
    new = os.environ.get(f"EPS_{name}")
    if new:
        return new
    return os.environ.get(f"IPS_{name}", default)
