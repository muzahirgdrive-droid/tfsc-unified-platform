# P8 report: "complete everything" (2026-10-20 to 2026-10-21)

The owner asked to "complete everything. make the complete product." P8 worked through the items the P7 handoff left
open. Every item was implemented, tested, committed and pushed on `claude/vigilant-babbage-cg07la`, and CI is green on
every pushed commit. Numbers below are measured, not estimated. What still needs the owner is at the end.

## What changed, and how it was verified

| Item | What it delivers | Verified by |
|---|---|---|
| P8.1 quick fixes | TFSC `/reviews` restyled with an inline required decision reason (no browser prompt); ESLint (type-checked + react-hooks) for the web app, enforced in CI, 146 findings fixed; concurrent same-lead Agreements handoff test | axe clean on `/reviews`; the concurrency test found a real race (500s and timeouts with two TFSC workers), fixed with a per-client advisory lock |
| P8.2 assignment and protection | Round-robin auto-assignment per team, weighted by open-lead capacity, off by default; 90-day lead protection review: supervisor renews or releases, never automatic while a meeting is scheduled | v2 tests, E2E, axe; browser check of People → Teams |
| P8.3 invitations and recovery | The CRM creates Keycloak accounts: invitation email (set password, TOTP for Supervisor and above), group sync, disable on suspension/offboarding, password reset by email; TFSC sign-in through the same realm | Real browser on the compose trial stack with a Mailpit sink; four bugs found and fixed on the way (identity never linked, Strict cookie dropped after the IdP, reset flag kept after SSO, no back-channel URL) |
| P8.4 files | Photos, imports, exports in S3-compatible storage with SHA-256-checked reads and 120 s presigned downloads; every upload scanned by ClamAV (refused on detection, 503 if the scanner is down); daily audit anchors copied to an object-lock bucket (COMPLIANCE, 7 years) and verified version by version | Real clamd refused EICAR, also inside a ZIP; forged versions and delete markers detected; RustFS replaces MinIO (MinIO stopped publishing images) |
| P8.5 observability | JSON logs with request and trace ids (route templates only, no personal data); OpenTelemetry traces for requests, outgoing calls and SQL; Sentry for API, web and Agreements (scrubbed); Prometheus, Grafana dashboard, alert rules; Helm values + optional ServiceMonitor | Live traces in Jaeger for every service; checking them found query strings (search terms, sign-in codes) in span URLs and Grafana phoning home, both fixed and tested; dashboard reviewed in both themes. Sentry: unit tests only |
| P8.6 backups | pgBackRest: WAL archived at least every minute, AES-256 encrypted, weekly full + daily differential, 14 days of point-in-time recovery; after every backup an automated restore test rebuilds the server at a point in time in an isolated copy and checks markers, migrations, `pg_amcheck` and the audit chain; results in the Security Center, metrics and 3 alerts; optional off-site repository; disaster-recovery restore command | Drills: restore to a time (later changes gone); data volume deleted and restored to the latest WAL (database open in 4 s, stack serving in ~20 s); off-site copy restored and checked; wrong passphrase reported as a failure; CI runs a backup and restore test on every push |
| P8.7 performance | A 1,000-row paste writes one statement per table (19 instead of 1,516); each phone's validity check runs once | A/B on identical data (3.98 M activities, 550 agents, 6 s think, two passes each): paste p95 3.8–5.3 s → 1.9–2.1 s, interactive p95 2–5× lower; 800 agents at 20 s think meet every §24 p95 target (docs/PERFORMANCE.md, round 2) |
| P8.8 Kubernetes | The Helm chart installed on a kind cluster with ingress-nginx and the chart's ClamAV; 13 end-to-end checks through the ingress and inside the cluster, `helm upgrade`, the same checks again; CI job `k8s-smoke` | Clean run: install 50 s, upgrade 43 s, 13/13 twice. It found four chart bugs, all fixed: migrations never ran on `helm upgrade` (a YAML comma), named image users failed `runAsNonRoot`, the CRM volume required ReadWriteMany, ClamAV pods had to download signatures before starting |

Test suites at the end: v2 API 178 (coverage of domain + security 90 %), pilot 17, refine + bridge (Python) 312,
Agreements 44 (+42 subtests), web unit 22, browser E2E 49 (desktop + mobile, axe), kind smoke 13 + 13.

## Honest limits (details in the apps' 04-KNOWN-ISSUES.csv)

- Load was measured on one 4-vCPU VM where the database, the API and the load generator share the CPU. 800 agents at
  6 s think saturate it; the p99 tails (1–6 s) are that contention. 10k/50k-user profiles and 100 M rows were not run.
- The chart ran on a one-node kind cluster with test-only PostgreSQL/Redis/storage, not on a multi-node cluster with a
  managed database and real DNS.
- Sentry, real SMTP, Google Calendar, Vonage API logging and the off-site backup bucket were not tried against the
  real providers (no credentials); each is implemented and tested against a stand-in.

## What needs the owner

See `08-OWNER-CHECKLIST.txt` in either app archive: hosting layout and DNS, secrets (now including
`BACKUP_PASSPHRASE`, `CRM_KEYCLOAK_CLIENT_SECRET`, `GRAFANA_ADMIN_PASSWORD`), SMTP, an off-site backup bucket, Sentry and
alert recipients, Google and Vonage, the signature images, LibreOffice vs Word approval, legal review, the cutover date,
and which teams use round-robin.
