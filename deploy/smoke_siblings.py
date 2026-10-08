#!/usr/bin/env python3
"""analyse.pdhc — does every call to a SIBLING service actually work? (#708)

Run it INSIDE the container, because that is the only place the real service
keys and the real network names exist:

    docker exec analyse_pdhc_app python deploy/smoke_siblings.py
    docker exec analyse_pdhc_app python deploy/smoke_siblings.py --json

READ-ONLY. It creates nothing and writes nothing, so it is safe to run against
production at any time, including immediately after a deploy.

## Why it drives the app's own clients

Every cross-service defect found in the 2026-09-29/30 sweep was a mismatch
between what a client sent and what the sibling accepted — #710 an admin
bearer the caller could not hold, #712 a response shape that crashed the
parser, #713 a key named `organisation_guid` where the client read `guid`,
#711 a header request.pdhc does not accept. A smoke built from hand-written
`curl` calls would have passed through all of them, because it would have been
testing the author's idea of the contract rather than the code's.

So this imports `CdrRegistry`, `fanout` and the node reader and calls them.
If a header, a key name or a response shape is wrong, it is wrong here too.

Exit code is 0 only if every check passed.
"""
from __future__ import annotations

import json as _json
import pathlib
import re
import sys
import time

# Running a script puts ITS directory on sys.path, not the app root, so
# `import app` fails from deploy/. Derived from this file rather than assumed
# from the working directory — the same trap as the hash-stability test fixed
# earlier today, which passed from analyse_app/ and failed from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

#: #804: every path this smoke probes -> the client module that builds it.
#:
#: analyse's smoke was the one whose INVENTED path motivated gateway's guard
#: (`fhir/Observation` for `/api/v1/fhir/Observation`), and it was the only one
#: of the three smokes with no guard of its own. The path is correct today --
#: observations_search.py:120 really does say `/api/v1/fhir/Observation` -- so
#: this is not fixing a live bug; it is keeping a fixed one fixed.
#:
#: `%s` stands for a path parameter. Templates, not bare strings, because the
#: spärr path has a guid in the middle and checking only its tail would accept
#: a wrong prefix -- which is the very mistake being guarded against.
PROBED_PATHS = {
    "/api/v1/fhir/Observation": "analyse/observations_search.py",
    "/api/v1/patients/%s/blocks/check": "node/reader.py",
}

#: Probed but built INLINE in this script rather than by a client module, so
#: there is nothing to check them against. Listed so the set of unguarded
#: probes is visible instead of merely absent:
#:   /healthz      per-CDR liveness, composed in the check below
#:   /api/health   sso liveness, composed in the check below
#: Both are bare liveness pings with no contract to drift from. A path that
#: carries data belongs in PROBED_PATHS, not here.
INLINE_PATHS = ("/healthz", "/api/health")


def _client_root() -> pathlib.Path:
    """Where the client modules live, in the image AND in a checkout.

    The image is /app/app/... while a checkout is <repo>/analyse_app/app/...,
    so one hardcoded relative path resolves in only one of them. gateway's
    guard had exactly this problem and therefore could not be run before a
    deploy, which is when it would have been worth running (#804).
    """
    here = pathlib.Path(__file__).resolve().parent.parent
    for cand in (here / "app", here / "analyse_app" / "app"):
        if cand.is_dir():
            return cand
    return here / "app"


def _path_appears_in_client(template: str, src: str) -> bool:
    """Is `template` a path this client actually builds?

    ANCHORED on the first literal fragment. A bare `in` check is not enough and
    cannot catch the bug this exists for: `fhir/Observation` IS a substring of
    `/api/v1/fhir/Observation`, so a truncated prefix passes. In every client
    the path starts right after the base-URL placeholder or an opening quote,
    so requiring one of } " ' before it rejects the truncation while still
    accepting the real thing.
    """
    frags = [f for f in template.split("%s") if f]
    if not frags:
        return False
    if not re.search(r"""[}"']""" + re.escape(frags[0]), src):
        return False
    return all(f in src for f in frags[1:])


RESET, RED, GRN, YEL, BOLD = "\033[0m", "\033[31m", "\033[32m", "\033[33m", "\033[1m"

results: list[dict] = []


def check(name: str, detail: str = ""):
    def wrap(fn):
        started = time.time()
        try:
            ok, note = fn()
        except Exception as e:                      # noqa: BLE001
            ok, note = False, f"{type(e).__name__}: {e}"
        results.append({"check": name, "ok": bool(ok), "note": note,
                        "ms": int((time.time() - started) * 1000)})
        return ok
    return wrap


def main(as_json: bool = False) -> int:
    from app import create_app
    app = create_app()

    with app.app_context():
        from flask import current_app
        from app.analyse.federation import CdrRegistry, fanout

        cfg = current_app.config
        endpoints = cfg.get("CDR_ENDPOINTS") or []

        # ── #804: do the probed paths exist in the clients? ──
        # First, deliberately. A smoke that calls a path production never uses
        # is a green light wired to nothing, and finding that out before the
        # network calls makes the report readable.
        @check("paths: every probed path appears in its client module")
        def _():
            root = _client_root()
            missing = []
            for template, mod in PROBED_PATHS.items():
                f = root / mod
                if not f.exists():
                    missing.append(f"{mod} is missing (looked in {root})")
                    continue
                if not _path_appears_in_client(template,
                                               f.read_text(encoding="utf-8")):
                    missing.append(f"{template} does not START a URL in {mod}")
            return (not missing), (
                f"all {len(PROBED_PATHS)} anchored to a URL boundary in their "
                f"clients; {len(INLINE_PATHS)} inline liveness paths unguarded "
                f"by design" if not missing else "; ".join(missing))

        # ── config that the calls depend on ──
        @check("config: CDR_ENDPOINTS is populated")
        def _():
            return bool(endpoints), f"{len(endpoints)} endpoint(s)"

        @check("config: ANALYSE_PDHC_SERVICE_KEY is set")
        def _():
            # The key every outbound CDR call authenticates with. An empty one
            # does not fail loudly — the CDR simply refuses, per-endpoint.
            return bool(cfg.get("ANALYSE_PDHC_SERVICE_KEY")), "present"

        @check("config: IPS_BASE_URL is set")
        def _():
            return bool(cfg.get("IPS_BASE_URL")), cfg.get("IPS_BASE_URL") or "MISSING"

        # ── every CDR, through the app's own registry ──
        reg = CdrRegistry.from_config(cfg)

        @check("cdr: every endpoint answers /healthz")
        def _():
            seen = reg.discover()
            down = [k for k, up in seen.items() if not up]
            return (not down), ("all reachable: " + ", ".join(sorted(seen))
                                if not down else "UNREACHABLE: " + ", ".join(down))

        # This is the call the live analysis path makes. It proves the service
        # key is accepted and the FHIR surface answers — the two things that
        # silently differ between "the container is up" and "analysis works".
        @check("cdr: an authenticated FHIR read succeeds on every endpoint")
        def _():
            # The exact path observations_search.py uses. Writing a plausible
            # one instead is how a smoke ends up testing the author's idea of
            # the contract rather than the code's.
            # PRODUCTION's own timeout, not one chosen to make this pass.
            # If a fanout cannot complete inside what the live path allows,
            # that is the finding, not a smoke-test artefact.
            t = float(cfg.get("CDR_FANOUT_TIMEOUT", 15) or 15)
            resp = fanout(reg, method="GET", path="/api/v1/fhir/Observation",
                          params={"_count": "1"}, timeout=t)
            failed = list(resp.failed or [])
            n = len(resp.succeeded or [])
            if failed:
                return False, (f"mode={resp.mode}; {n} ok, "
                               f"FAILED: {', '.join(failed)}")
            return n > 0, (f"mode={resp.mode}; {n} endpoint(s) returned data "
                           f"within the live {t:g}s timeout")

        # ── ips: the spärr gate ──
        @check("ips: the spärr predicate answers, and the gate fails CLOSED")
        def _():
            from app.node.reader import NodeReader
            ips = cfg.get("IPS_BASE_URL")
            if not ips:
                return False, "IPS_BASE_URL not set"
            # base_url is irrelevant to the spärr call — it talks to ips, not
            # to a CDR — but the dataclass requires it.
            # #717: per patient, per SOURCE, Authorization: ApiKey.
            # A missing key or source now refuses by name instead of 404ing,
            # so this check distinguishes "not configured" from "ips is down".
            r = NodeReader(base_url="unused",
                           service_key=cfg.get("ANALYSE_PDHC_SERVICE_KEY", ""))
            blocked = r.excluded_by_spärr(
                ["smoke-not-a-real-patient"], ips,
                source_clinic_id=cfg.get("ANALYSE_SOURCE_CLINIC_ID")
                or "smoke-probe-org",
                ips_api_key=cfg.get("IPS_API_KEY"))
            # A reachable ips returns a set (usually empty for a fake guid).
            # If it were unreachable the reader raises, and that raise IS the
            # fail-closed behaviour — reported as a failure here on purpose,
            # because a node that cannot reach ips must not run.
            return isinstance(blocked, set), f"returned {len(blocked)} blocked"

        # ── sso: the identity every request is validated against ──
        @check("sso: SSO_BASE_URL reachable")
        def _():
            import requests
            base = (cfg.get("SSO_BASE_URL") or "").rstrip("/")
            if not base:
                return False, "SSO_BASE_URL not set"
            resp = requests.get(f"{base}/api/health", timeout=10)
            return resp.status_code == 200, f"/api/health {resp.status_code}"

        @check("sso: the service credential pair is configured")
        def _():
            # Not exercised here: /me/service needs a real user token, which a
            # read-only smoke has no business minting. Presence is what can be
            # checked without one, and an ABSENT pair is the failure that has
            # actually happened before (memory: PDHC SSO client creds audit).
            cid, sec = cfg.get("SSO_CLIENT_ID"), cfg.get("SSO_CLIENT_SECRET")
            return bool(cid and sec), "client id + secret present"

    failed = [r for r in results if not r["ok"]]
    if as_json:
        print(_json.dumps({"service": "analyse.pdhc", "checks": results,
                           "failed": len(failed)}, indent=2))
    else:
        print(f"\n{BOLD}analyse.pdhc — sibling smoke{RESET}\n")
        for r in results:
            mark = f"{GRN}✓{RESET}" if r["ok"] else f"{RED}✗{RESET}"
            print(f"  {mark} {r['check']:<52} {r['note']}  ({r['ms']}ms)")
        if failed:
            print(f"\n{RED}{BOLD}{len(failed)} check(s) failed.{RESET} "
                  f"analyse cannot do its job until these pass.\n")
        else:
            print(f"\n{GRN}{BOLD}All {len(results)} checks passed.{RESET}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(as_json="--json" in sys.argv))
