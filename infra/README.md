# Infrastructure

| Path | What |
|---|---|
| `compose/` | Single-server stack (pilot, §27): PostgreSQL 16, Redis 7, Keycloak 26, CRM (migrate, api, bulk, dispatcher, scheduler), Agreements (api, worker), optional Caddy TLS edge (`--profile edge`, which routes pastes and imports to `crm-bulk`) |
| `keycloak/realm-tfsc.json` | Realm import: CRM + Agreements clients (PKCE S256), the CRM's provisioning service account (`tfsc-crm-provisioner`: create users, groups, disable), SMTP settings from `SMTP_*`, brute-force protection, password policy, TOTP required for the `mfa-required` role (group `supervisors-and-above`), `amr` claim, email subjects |
| `keycloak/dev_seed.py` | Local trials only: creates test users (optionally with a known TOTP secret for automated tests) |
| `keycloak/trial_seed.py` | Local trials only: bootstraps both Super Admins, links them, invites synthetic users (pairs with `apps/crm-web/e2e/sso-invites.mjs`) |
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
People get their SSO accounts from the CRM: when a Manager or above creates a user without a temporary password,
Keycloak emails an invitation (set a password; Supervisors and above also enrol TOTP). Role changes move them between
the `agents` and `supervisors-and-above` groups; suspension and offboarding disable the account and end its sessions.
This needs `CRM_KEYCLOAK_CLIENT_SECRET` (the realm's `tfsc-crm-provisioner` client) and working `SMTP_*` settings;
"Forgot password?" on the sign-in page uses the same mail settings. Federating a company directory instead is also
possible: emails must match the CRM accounts.

## Compose: local trial (verified 2026-10-20)
Use http origins (`CRM_MODE=local`, `TFSC_MODE=local`, `KC_COMMAND=start-dev`, `KC_REALM_SSL_REQUIRED=none`) and
ports 8866 (CRM), 8865 (Agreements), 8081 (Keycloak). What was checked on that trial stack:
- all services healthy; CRM migrated to head by the `crm-migrate` job;
- SSO in a real browser (`apps/crm-web/e2e/sso.mjs`): agent with password; supervisor with password + TOTP (token
  `amr: ["pwd","otp"]`); a Keycloak account without a CRM account is refused;
- CRM ↔ Agreements on internal URLs with public Host headers, including a signed bridge call accepted by TFSC.

## Compose: invitation, recovery and TFSC SSO trial (verified 2026-10-20, `--profile trial`)
`--profile trial` adds Mailpit (web UI on :8025), which catches every email Keycloak sends (`SMTP_HOST=mailpit`,
`SMTP_PORT=1025`). Use `*.localhost` origins so each app keeps its own cookies (for example
`CRM_ORIGIN=http://crm.localhost:8766`, `TFSC_ORIGIN=http://tfsc.localhost:8765`, `SSO_PUBLIC_URL=http://sso.localhost:8081`).
```
docker compose -p tfsc-trial --env-file trial.env --profile trial up -d
docker compose -p tfsc-trial exec agreements python manage.py setup-key
python infra/keycloak/trial_seed.py state.json setup && (cd apps/crm-web && node e2e/sso-invites.mjs invites out)
python infra/keycloak/trial_seed.py state.json grant && (cd apps/crm-web && node e2e/sso-invites.mjs tfsc out)
```
Checked in a real browser on a fresh stack: invitation emails ("Set up your 24-7 Consultancy sign-in") for an agent
and a supervisor; the link sets the password, and the supervisor enrols TOTP in the same flow; the first CRM SSO
sign-in activates the invited account; "Forgot password?" sends "Reset your 24-7 Consultancy password", and the new
password works; TFSC SSO works both for someone granted Agreements after their first sign-in and for someone granted
before it (the CRM re-sends the grant with the verified identity).

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
