# analyse.pdhc deploy + two-service cutover runbook

Ties together the cross-service tail of the analyse-engine split (#533 / pivot
#545). **Build is done + committed** (analyse `0f47896`; cd-assist nurse-fold
dashboard `a24715f`). What remains is live infra + cross-service, and is
**operator-coordinated** (reverse proxy, DNS, SSO client registration, and
high-blast-radius changes to gateway + all CDRs). Do the steps in order; verify
each before the next.

## Operator inputs required before any deploy
1. **SSO client registration** for `analyse.pdhc` in sso.pdhc → produces
   `SSO_CLIENT_ID` / `SSO_CLIENT_SECRET` (see memory: PDHC SSO client creds
   audit — each service's .env needs the matching `_<SERVICE>` pair or
   `/me/service` 401s). Callback path is `/auth/callback`.
2. **Three service keys** for analyse's `.env`:
   `ANALYSE_PDHC_SERVICE_KEY` (analyse's OWN outbound identity to CDR2–6),
   `GATEWAY_PDHC_SERVICE_KEY`, `MONITOR_PDHC_SERVICE_KEY` (inbound callers).
3. **CDR2–6 URLs** for `CDR_ENDPOINTS` (+ whatever per-CDR outbound key
   `federation.CdrRegistry.from_config` reads — see `.env.example`).
4. **Reverse-proxy vhost** `analyse.pdhc.se → 127.0.0.1:9110` + **DNS** +
   **TLS** (operator-owned; do NOT edit the proxy config from a service repo).

## Step 0 — deploy analyse.pdhc (own new service, containerised)
- scp release to `/usr/local/www/analyse.pdhc/`, operator fills `.env` from
  `.env.example` (§ inputs above). `AUTH_MODE=sso`. Bind 127.0.0.1.
- `docker-compose up -d --build` (COMPOSE_PROJECT_NAME=analyse_pdhc pinned).
- `docker exec analyse_pdhc_app flask db upgrade` → head `0001_initial`.
- Verify `curl 127.0.0.1:9110/healthz` = 200 `database:connected`; then via
  vhost `https://analyse.pdhc.se/healthz`. SU bootstrap: `flask create-su`.

## Step 1 — #540 gateway ANALYSE_BASE_URL repoint  (HIGH BLAST — gateway)
Gateway's `/api/v1/observations` proxy currently lands on **dashboard**
(#291). Repoint it to analyse:
- Edit gateway.pdhc `.env`: `ANALYSE_BASE_URL=https://analyse.pdhc.se` (or the
  loopback form to avoid hairpin NAT). Ensure gateway's `X-Service-Key` matches
  analyse's `GATEWAY_PDHC_SERVICE_KEY`.
- `docker-compose up -d` (NOT `docker restart` — must re-read env_file; see
  memory: docker restart skips env_file).
- Verify: gateway → analyse observations_search returns rows; gateway health green.

## Step 2 — #541 CDR2–6 read-identity flip  (HIGH BLAST — all analysis CDRs)
CDR2–6 currently trust `X-Source-Service: dashboard.pdhc` for analyse reads.
Add/rotate to accept `analyse.pdhc` presenting `ANALYSE_PDHC_SERVICE_KEY`.
Coordinate with the #293 read-lockdown. Verify a federated fanout from analyse
returns rows from every CDR before removing dashboard's read identity.

## Step 3 — #542 cutover verify
Researcher/cohort + all 4 federated endpoints work end-to-end **from
analyse.pdhc**, gateway routed to analyse, CDRs answering analyse's identity.

## Step 4 — #543 delete group half from cd-assist (dashboard)
ONLY after Steps 1–3 are green (analyse serves these live). Remove from
dashboard: `app/analyse/{observations_search,stats,canonical,openehr,cohort,
federation,aggregations}.py`, `app/routes/researcher.py`, their blueprint
registrations in `app/__init__.py`, their tests, and the `KNOWN_SERVICES`
gateway entry. Keep the nurse + charts (care-delivery) surface. Run the suite.

## Step 5 — #547 rebrand cd-assist (LOW-FRICTION: keep host)
- Keep dashboard.pdhc.se host, COMPOSE_PROJECT_NAME, container, volume, repo dir.
- Flip service identity: `/healthz` `service` string, page titles/templates,
  `docs/technical.md` + `docs/user_manual.md` → "cd-assist".
- www.pdhc.se cards: rebrand card 13 "Dashboard" → "cd-assist" (individual
  point-of-care; keep dashboard.pdhc.se + ports 9026–9029); **remove the
  phantom card 13b** (`cd-assist.pdhc.se`/9100–9109 — wrong under low-friction);
  **add an `analyse` card** (analyse.pdhc.se / 9110–9111). Update
  documentation.html rows. Keep favicon/monitoring keys consistent with the
  health `service` string chosen.

## Step 6 — #544 cleanup
Prune old dashboard group-half artefacts, retire superseded greenfield tickets
(#535/#536/#537), final bookkeeping. Prune the scratchpad greenfield backup.
