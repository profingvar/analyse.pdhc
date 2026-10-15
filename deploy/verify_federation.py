"""#806 — the real node path over REAL CDRs, merged by the REAL coordinator.

What this substitutes: only the coordinator<->node HTTP hop (that transport
is `ANALYSE_TRANSPORT_SECRET` + `ANALYSE_NODES`, and #806's own body records
it as working and NOT the blocker). Everything else is production: two live
CDRs, the real NodeReader over HTTP, the real spärr call to live ips, the
real consent join inside each CDR, the real projection, coarsening,
disclosure and merge.

The claim under test is #806's "done when": the merged result matches what
the same data produces POOLED, and the gates are observably applied.

HOW TO RUN IT (it must run INSIDE the container, which holds the keys):

    docker cp deploy/verify_federation.py analyse_pdhc_app:/tmp/
    docker exec analyse_pdhc_app python /tmp/verify_federation.py

WHAT IT DOES NOT PROVE: the coordinator<->node HTTP hop. `run_spec` is called
here in process. Standing up node processes behind `ANALYSE_NODES` is #807,
and it needs `.env` work only the operator can do (`ANALYSE_TRANSPORT_SECRET`,
the per-project key). Everything on the other side of that hop — the read, the
gates, the projection, the coarsening, the disclosure pass and the merge — is
what this exercises, against production.

A CAVEAT ON `source_clinic_id`: this script takes ONE value, from the first
observation's performer. That is correct for the currently seeded CDRs, where
every row shares one performer org, and WRONG in general — see progress.md,
and #782. cdr1 once held six distinct performer organisations across 7,415
rows, and no single per-node constant can stand in for all of them. Spärr is
per source, so the real answer is per row.
"""
import os, sys
sys.path.insert(0, "/app")
from app.coordinator import combine
from app.node import NodePolicy, NodeReader, run_spec
from app.privacy import ProjectKey
from app.spec import AnalysisSpec
import requests

KEY = os.environ["ANALYSE_PDHC_SERVICE_KEY"]
IPS = os.environ["IPS_BASE_URL"]
IPS_KEY = os.environ["IPS_API_KEY"]
SOURCES = [("cdr2", "http://host.docker.internal:9146"),
           ("cdr3", "http://host.docker.internal:9246")]
H = {"X-Service-Key": KEY, "X-Source-Service": "analyse.pdhc",
     "X-Access-Purpose": "statistics", "Accept": "application/json"}


def discover(base, n_patients=8):
    """Patients and the most common concept at this CDR, read from the CDR."""
    r = requests.get(f"{base}/api/v1/fhir/Observation",
                     params={"_count": 2000}, headers=H, timeout=60)
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
    return sorted(pats[concept])[:n_patients], concept


cohorts, concept = {}, None
for node_id, base in SOURCES:
    cohorts[node_id], c = discover(base)
    concept = concept or c
    print(f"{node_id}: {len(cohorts[node_id])} patients, concept {c[:8]}")
assert concept

# One source clinic for the spärr question. ips answers "is data from source
# X readable for patient P", so a node must name its org.
ORG = requests.get(
    f"{SOURCES[0][1]}/api/v1/fhir/Observation", params={"_count": 1},
    headers=H, timeout=30).json()["entry"][0]["resource"].get(
        "performer", [{}])[0].get("identifier", {}).get("value")
print("source_clinic_id", ORG)

spec = AnalysisSpec.model_validate({
    "title": "#806 federated describe over two real CDRs",
    "purpose": "statistics",
    "sources": [s for s, _ in SOURCES],
    "cohort": {"include": [{"observation": concept, "op": ">=", "value": 0}]},
    "index_event": {"observation": concept},
    "variables": [{"name": "v", "from": concept, "agg": "mean"}],
    "analyses": [{"type": "describe", "vars": ["v"]},
                 {"type": "completeness"}],
})

KEYMAT = ProjectKey("pdhc-806", b"8" * 32)
runs, policies, raw_rows = [], {}, []
for node_id, base in SOURCES:
    policy = NodePolicy.load(
        f"node_id: {node_id}\ncdr_base_url: {base}\n"
        f"source_clinic_id: {ORG}\n"
        f"permitted_purposes: [statistics]\n"
        f"permitted_analyses: [describe, completeness]\nk_min: 1\n",
        is_text=True)
    reader = NodeReader(base_url=base, service_key=KEY, timeout=60.0)
    run = run_spec(spec, policy, reader, project_key=KEYMAT,
                   ips_base_url=IPS, ips_api_key=IPS_KEY,
                   cohort=cohorts[node_id])
    policies[node_id] = policy.disclosure()
    runs.append(run.to_json())
    print(f"\n{node_id}: n_patients={run.n_patients} excluded={run.excluded} "
          f"partials={[p['kind'] for p in run.partials]}")
    for n in run.notes:
        print(f"  note: {n}")
    # The same rows, read again, for the pooled comparison.
    raw_rows.extend(reader.read_observations(
        purpose="statistics", patient_guids=cohorts[node_id],
        concepts=[concept]))

result = combine(spec, runs, coordinator_version="806-verify",
                 node_policies=policies)
print("\n=== merged ===")
import json
for r in result.results:
    print(" ", json.dumps(r)[:600])
print("sources", [(s.source, s.ok) for s in result.sources])
for n in result.notes:
    print("note:", n)

# ---- the pooled comparison -------------------------------------------------
# One value per patient, by the spec's own aggregation, over the union of both
# CDRs' rows — computed here with no node, no projection and no merge.
from app.privacy import coarsen_rows
rows, _ = coarsen_rows(raw_rows, index_concept=concept)
per_patient = {}
for r in rows:
    if r.get("value") is None:
        continue
    per_patient.setdefault(r["patient_guid"], []).append(float(r["value"]))
means = [sum(v) / len(v) for v in per_patient.values()]
print("\n=== pooled, computed with no node at all ===")
print("n", len(means))
print("mean %.6f" % (sum(means) / len(means)))
