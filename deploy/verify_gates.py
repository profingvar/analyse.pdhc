"""#806's "observably applied" half: show each gate BITING, not merely absent.

A gate that excludes nobody is indistinguishable from a gate that is not
wired up — which is the #729 failure that hid for five months. So each of
these changes one input and shows the exclusion appear.

HOW TO RUN IT:

    docker cp deploy/verify_gates.py analyse_pdhc_app:/tmp/
    docker exec analyse_pdhc_app python /tmp/verify_gates.py

Read-only throughout. It demonstrates refusal by changing an INPUT (an
unreachable ips, a missing source_clinic_id, a purpose the policy forbids, a
research project the patients have not consented to) rather than by writing a
spärr into production ips. The one thing it therefore does NOT show is a
REGISTERED block being honoured; that needs a real block, which is an operator
decision and a production write.
"""
import os, sys
sys.path.insert(0, "/app")
from app.node import NodePolicy, NodeReader, run_spec
from app.node.reader import ConsentUnavailable, ReadError
from app.node.policy import PolicyError
from app.privacy import ProjectKey
from app.spec import AnalysisSpec
import requests

KEY = os.environ["ANALYSE_PDHC_SERVICE_KEY"]
IPS = os.environ["IPS_BASE_URL"]
IPS_KEY = os.environ["IPS_API_KEY"]
BASE = "http://host.docker.internal:9146"
H = {"X-Service-Key": KEY, "X-Source-Service": "analyse.pdhc",
     "X-Access-Purpose": "statistics", "Accept": "application/json"}

r = requests.get(f"{BASE}/api/v1/fhir/Observation", params={"_count": 2000},
                 headers=H, timeout=60)
pats, codes = {}, {}
for e in (r.json() or {}).get("entry") or []:
    res = e.get("resource") or {}
    p = ((res.get("subject") or {}).get("reference") or "").rsplit("/", 1)[-1]
    for c in (res.get("code") or {}).get("coding") or []:
        if (c.get("system") or "").startswith("urn:pdhc:concept"):
            codes[c["code"]] = codes.get(c["code"], 0) + 1
            if p:
                pats.setdefault(c["code"], set()).add(p)
concept = max(codes, key=codes.get)
cohort = sorted(pats[concept])[:8]
ORG = r.json()["entry"][0]["resource"].get(
    "performer", [{}])[0].get("identifier", {}).get("value")

def spec(purpose="statistics"):
    return AnalysisSpec.model_validate({
        "title": "gate probe", "purpose": purpose, "sources": ["cdr2"],
        "cohort": {"include": [{"observation": concept, "op": ">=",
                                "value": 0}]},
        "index_event": {"observation": concept},
        "variables": [{"name": "v", "from": concept, "agg": "mean"}],
        "analyses": [{"type": "describe", "vars": ["v"]}],
    })

def policy(*, purposes="[statistics, research]", clinic=ORG):
    return NodePolicy.load(
        f"node_id: cdr2\ncdr_base_url: {BASE}\n"
        + (f"source_clinic_id: {clinic}\n" if clinic else "")
        + f"permitted_purposes: {purposes}\n"
        f"permitted_analyses: [describe]\nk_min: 1\n", is_text=True)

def run(sp, pol, *, ips=IPS, ips_key=IPS_KEY, projects=()):
    return run_spec(sp, pol, NodeReader(base_url=BASE, service_key=KEY,
                                        timeout=60.0),
                    project_key=ProjectKey("p", b"8" * 32),
                    ips_base_url=ips, ips_api_key=ips_key, cohort=cohort,
                    research_projects=tuple(projects))

print("cohort", len(cohort), "patients at cdr2; concept", concept[:8])

print("\n1. BASELINE statistics")
b = run(spec(), policy())
print("   n_patients", b.n_patients, "excluded", b.excluded,
      "partials", [p["kind"] for p in b.partials])

print("\n2. CONSENT GATE — same cohort, purpose=research")
print("   sso's research_projects table holds 0 rows, so the per-project")
print("   intersection cannot be non-empty for anyone. If the gate is wired")
print("   up this must exclude everyone; if it is not, the numbers are")
print("   identical to the baseline and nothing says so.")
try:
    g = run(spec("research"), policy())
    print("   n_patients", g.n_patients, "excluded", g.excluded)
except ReadError as e:
    print("   the CDR REFUSED the read:", str(e).splitlines()[-1][:120])
print("   2b. research WITH a project guid the patients have not consented to")
try:
    g = run(spec("research"), policy(),
            projects=["00000000-0000-4000-8000-000000000000"])
    print("   n_patients", g.n_patients, "excluded", g.excluded,
          "partials", [p["kind"] for p in g.partials])
    for n in g.notes:
        print("   note:", n)
except ReadError as e:
    print("   ReadError:", str(e).splitlines()[-1][:120])

print("\n3. SPÄRR GATE fails CLOSED when ips cannot answer")
try:
    run(spec(), policy(), ips="http://127.0.0.1:1")
    print("   !! it proceeded — the gate failed OPEN")
except ConsentUnavailable as e:
    print("   ConsentUnavailable:", str(e)[:120])

print("\n4. SPÄRR cannot be asked without a source_clinic_id, so the node")
print("   refuses rather than computing over unknown block status")
try:
    run(spec(), policy(clinic=None))
    print("   !! it proceeded")
except ConsentUnavailable as e:
    print("   ConsentUnavailable:", str(e)[:120])

print("\n5. POLICY refuses a purpose the organisation did not permit")
try:
    run(spec("research"), policy(purposes="[statistics]"))
    print("   !! it proceeded")
except PolicyError as e:
    print("   PolicyError:", str(e)[:120])

print("\n6. ips ANSWERED for real in the baseline (not a silent pass):")
for g_ in cohort[:3]:
    rr = requests.get(f"{IPS.rstrip('/')}/api/v1/patients/{g_}/blocks/check",
                      params={"source_clinic_id": ORG},
                      headers={"Authorization": f"ApiKey {IPS_KEY}",
                               "Accept": "application/json"}, timeout=30)
    print("   ", g_[:8], rr.status_code, (rr.json() or {}).get("is_blocked"))
