# Infrastructure

| Path | What |
|---|---|
| `compose/` | Single-server stack (pilot, §27): PostgreSQL 16, Redis 7, Keycloak 26, CRM (migrate, api, bulk, dispatcher, scheduler), Agreements (api, worker), optional Caddy TLS edge (`--profile edge`, which routes pastes and imports to `crm-bulk`) |
| `keycloak/realm-tfsc.json` | Realm import: CRM + Agreements clients (PKCE S256), brute-force protection, password policy, TOTP required for the `mfa-required` role (group `supervisors-and-above`), `amr` claim |
| `keycloak/dev_seed.py` | Local trials only: creates test users (optionally with a known TOTP secret for automated tests) |
| `helm/tfsc-platform/` | Kubernetes chart (PostgreSQL, Redis, Keycloak external): CRM api + bulk (pastes and imports, routed by the Ingress)/dispatcher/scheduler + migrate Job, Agreements api/worker, Ingress, HPA, PDB, NetworkPolicies |
| `load/` | k6 load profiles (`crm.js`, `run.sh`), SSE fan-out harness (`sse_fanout.py`), synthetic data generator (`gen_data.py`); results and method in docs/PERFORMANCE.md |

## Compose: production on one VPS
```
cd infra/compose && cp .env.example .env    # fill every secret; https origins; KC_COMMAND=start
docker compose --env-file .env --profile edge up -d --build
```
DNS: `CRM_DOMAIN`, `TFSC_DOMAIN`, `SSO_DOMAIN` point at the server; Caddy obtains certificates.
First Super Admin: set `CRM_ALLOW_LOCAL_LOGIN=1`, read the one-time key with
`docker compose exec crm-api python -c "from tfsc_crm.domain import bootstrap; print(bootstrap.read_setup_key())"`,
complete setup at `/login`, then set it back to `0` and restart `crm-api`. Everyone else signs in with SSO.
Create Keycloak users (or federate your directory) with the same emails as their CRM accounts; put Supervisors and
above in the `supervisors-and-above` group.

## Compose: local trial (verified 2026-10-20)
Use http origins (`CRM_MODE=local`, `TFSC_MODE=local`, `KC_COMMAND=start-dev`, `KC_REALM_SSL_REQUIRED=none`) and
ports 8866 (CRM), 8865 (Agreements), 8081 (Keycloak). What was checked on that trial stack:
- all services healthy; CRM migrated to head by the `crm-migrate` job;
- SSO in a real browser (`apps/crm-web/e2e/sso.mjs`): agent with password; supervisor with password + TOTP (token
  `amr: ["pwd","otp"]`); a Keycloak account without a CRM account is refused;
- CRM ↔ Agreements on internal URLs with public Host headers, including a signed bridge call accepted by TFSC.

## Building images
Both images build from the repository root:
```
docker build -f apps/crm-api/Dockerfile -t tfsc-crm:2 .
docker build -f apps/agreements/Dockerfile -t tfsc-agreements:4 .
```
Behind a TLS-inspecting proxy add `--secret id=build_ca,src=/path/to/proxy-ca.crt` (used only during the build, never
stored in the image). If Docker Hub rate-limits pulls, pull the base images from a mirror (for example
`mirror.gcr.io/library/python:3.12-slim-bookworm`) and tag them locally first.

## Kubernetes
```
kubectl create secret generic tfsc-crm ...        # see helm/tfsc-platform/templates/NOTES.txt
kubectl create secret generic tfsc-agreements ...
helm upgrade --install tfsc infra/helm/tfsc-platform -f my-values.yaml
```
Checked: `helm lint` clean; all 15 rendered objects validate against Kubernetes 1.30 schemas (`kubeconform -strict`).
Not yet run on a live cluster.
