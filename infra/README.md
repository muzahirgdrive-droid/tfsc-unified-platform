# Infrastructure

| Path | What |
|---|---|
| `compose/` | Single-server stack (pilot, §27): PostgreSQL 16, Redis 7, Keycloak 26, CRM (migrate, api, bulk, dispatcher, scheduler), Agreements (api, worker), S3-compatible object storage (`objects`, RustFS) + bucket setup (`objects-init`), ClamAV (`clamav`), optional Caddy TLS edge (`--profile edge`, which routes pastes and imports to `crm-bulk` and serves presigned downloads on `FILES_DOMAIN`), Mailpit for trials (`--profile trial`), Prometheus + Grafana + Jaeger (`--profile observability`), pgBackRest backups with a daily automated point-in-time restore test (`backup`) |
| `keycloak/realm-tfsc.json` | Realm import: CRM + Agreements clients (PKCE S256), the CRM's provisioning service account (`tfsc-crm-provisioner`: create users, groups, disable), SMTP settings from `SMTP_*`, brute-force protection, password policy, TOTP required for the `mfa-required` role (group `supervisors-and-above`), `amr` claim, email subjects |
| `keycloak/dev_seed.py` | Local trials only: creates test users (optionally with a known TOTP secret for automated tests) |
| `keycloak/trial_seed.py` | Local trials only: bootstraps both Super Admins, links them, invites synthetic users (pairs with `apps/crm-web/e2e/sso-invites.mjs`) |
| `helm/tfsc-platform/` | Kubernetes chart (PostgreSQL, Redis, Keycloak external): CRM api + bulk (pastes and imports, routed by the Ingress)/dispatcher/scheduler + migrate Job, Agreements api/worker, Ingress, HPA, PDB, NetworkPolicies |
| `observability/` | Prometheus scrape config and alert rules, Grafana datasource + "TFSC CRM" dashboard (provisioned in compose) |
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

## Files, virus scan and write-once audit anchors (verified on the compose stack 2026-10-20)
- Photos, uploaded import files, legacy workbooks and export ZIPs go to the private bucket `crm-files`
  (`CRM_STORAGE=s3`); PostgreSQL keeps type, size and SHA-256, and every read is checked against that hash.
  Export downloads are presigned URLs valid for `CRM_S3_URL_TTL` seconds (default 120), handed out only after the
  approval and requester checks; the edge serves them on `FILES_DOMAIN`. Anonymous bucket access is refused.
- Every upload is scanned by clamd before it is stored (archives included); a detection returns 422 and is audited
  as `file.rejected`; if the scanner is unreachable the upload is refused (503). Hosted mode will not start until
  `CRM_CLAMAV` is set (`host:port`, or `off` as an explicit choice).
- The scheduler copies each daily audit anchor to `crm-audit-anchors`, created with object lock; every copy has
  COMPLIANCE retention for `CRM_ANCHOR_RETENTION_DAYS` (default 2557, seven years): nobody, including the CRM's own
  keys, can delete it or shorten that. "Verify audit chain" in the Security Center reads every version of every
  copy, so an edited database, a forged newer version or a delete marker shows as a failure.
- RustFS replaces MinIO in the bundled stack: MinIO stopped publishing community images. Any S3-compatible service
  with object lock works (`CRM_S3_ENDPOINT`, `CRM_S3_PUBLIC_ENDPOINT`). For production, give the CRM keys that
  cannot delete objects, and back up the `crm-files` bucket (versioning or replication) with the database.

## Backups (verified on the compose stack 2026-10-21)
`postgres` is PostgreSQL 16 with pgBackRest (`compose/postgres/`): WAL archived at least every minute, encrypted with
`BACKUP_PASSPHRASE` (keep a copy off the server). The `backup` service takes a weekly full and a daily differential
backup and after each one restores the server to a point in time in an isolated scratch copy and checks it (markers,
migrations, `pg_amcheck`, audit chain). Results in Security Center → Backups, metrics and alerts. Off-site copy:
`compose/backup-offsite.env` (template next to it). Disaster recovery and the measured drills:
[docs/runbooks/backups-and-restore.md](../docs/runbooks/backups-and-restore.md).

## Logs, metrics, traces and error reports (verified on the compose stack 2026-10-20, `--profile observability`)
- **Logs**: one JSON object per line on stdout from every CRM and Agreements process (`CRM_LOG_FORMAT`/`TFSC_LOG_FORMAT`
  default to `json` in compose), with `request_id` (echoed as `X-Request-Id`; a caller's id is kept only if it is
  short and plain) and, when tracing is on, `trace_id`/`span_id`. Each request writes one access line: method,
  route template (`/api/leads/{lead_id}`, never the raw path), status and duration. No bodies, query strings, names,
  emails, phone numbers or client IPs are logged; uvicorn's own access log is switched off for that reason. Health
  checks and metric scrapes are not logged.
- **Metrics**: Prometheus scrapes `/metrics` on `crm-api` (pool `interactive`) and `crm-bulk` (pool `bulk`) with
  `CRM_METRICS_TOKEN`. The uvicorn workers of one container share their counters through `CRM_METRICS_DIR`, so every
  scrape reports the whole container. Grafana (`127.0.0.1:3000`, user `admin`, `GRAFANA_ADMIN_PASSWORD`) opens on the
  "TFSC CRM" dashboard: traffic, error rate, latency against the §24 targets, slowest and busiest routes, outbox
  backlog and dead letters, live connections, database and process status. Health checks and scrapes are left out
  of traffic and latency. Six alert rules (`observability/alerts.yml`) load into Prometheus; who receives them is an
  owner decision (add an Alertmanager with email or chat routes).
- **Traces**: set `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` and open Jaeger on `127.0.0.1:16686`. Services
  `tfsc-crm` and `tfsc-agreements` (plus `tfsc-crm-dispatcher`/`-scheduler`) send a span per request, per outgoing
  HTTP call (CRM ↔ Agreements, Keycloak, Google) and per SQL statement (statement text with placeholders, never the
  values); span URLs keep the path but never the query string (search terms, sign-in codes). The bundled Jaeger keeps
  traces in memory only; for production send OTLP to a managed or persistent backend.
- **Error reports**: Sentry when `CRM_SENTRY_DSN`, `CRM_SENTRY_DSN_WEB` (the browser app) or `TFSC_SENTRY_DSN` is
  set. Reports drop cookies, headers, request bodies, query strings and user details except the user id; the browser
  SDK loads only when a DSN is configured and collects no user info, cookies, headers, bodies or URL query strings.
  Checked with unit tests only: no live Sentry project was used.

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
Checked: `helm lint` clean; all 21 rendered objects validate against Kubernetes 1.30 schemas (`kubeconform -strict`),
23 with `observability.serviceMonitor.enabled` (ServiceMonitor checked against the Prometheus Operator CRD schema).
Not yet run on a live cluster.
Observability values: `observability.otlpEndpoint` (traces), `observability.sentry.*` (error reports),
`observability.serviceMonitor` (Prometheus Operator scrapes every CRM API pod with `CRM_METRICS_TOKEN` from the CRM
secret; the network policy admits `observability.prometheusNamespace`). Logs are JSON on stdout for the cluster's log
agent. Import `observability/grafana/dashboards/tfsc-crm.json` and `observability/alerts.yml` into your Grafana and
Prometheus.
