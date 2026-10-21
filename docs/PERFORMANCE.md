# Performance (§24) — measured results

Everything below was measured on 2026-10-20 on the test bed described next, at commit `d5fb29d` (branch
`claude/vigilant-babbage-cg07la`). Numbers are what k6 and the SSE harness reported, not estimates. Where a §24 target
was **not** measured, the table says so. The "Sizing" section is arithmetic from measured per-row sizes and is labelled as such.

## Test bed

| | |
|---|---|
| Machine | **one** VM: 4 vCPU (Intel Xeon @ 2.10 GHz), 16 GB RAM, local disk |
| Co-located on it | PostgreSQL 16.15 (stock config: `shared_buffers` 128 MB, `work_mem` 4 MB), Redis 7.0.15, the CRM API, the k6 0.54.0 load generator (Docker, host network), and the SSE client processes |
| CRM API | Python 3.13, uvicorn. Interactive pool: 3 worker processes. Bulk pool (when used): 2 worker processes on a second port, standing in for the `crm-bulk` deployment |
| Realtime | Redis broker (`CRM_REDIS_URL`), outbox dispatcher inside the interactive API |
| Data (synthetic, `infra/load/gen_data.py`) | 606 users (550 agents, 50 supervisors, 5 managers, 1 director); **2.68 M activities** over 60 workdays in monthly partitions; 971 k phone-registry rows; database 1.85 GB |

Every tier shares the same 4 vCPUs, so the tail latencies below include CPU contention from the load generator and the
database. A production layout (database, API and bulk on separate hosts) has more headroom than this, but that was not measured.

## Results against the §24 targets

| §24 target | Measured | Verdict |
|---|---|---|
| API p95: reads < 200 ms, writes < 300 ms, pre-dial < 100 ms | 550 agents at 20 s think, bulk pool: **88 / 77 / 30 ms**. At 6 s think (~23× go-live save rate): **160 / 154 / 99 ms** | ✓ both profiles |
| Paste: 1,000 rows refined + saved < 3 s p95 | Alone: **1.29 s**. 550 agents at 20 s think, bulk pool: **2.91 s**. At 6 s think, bulk pool: **3.78 s** ✗. At 6 s think, no bulk pool: 10.4 s ✗ | ✓ up to the 20 s-think profile with the bulk pool; ✗ at 6 s think |
| Celebration fan-out < 3 s to all clients | 15,000 SSE clients: all received, **p95 2.80 s**, max 2.84 s. 10,000: p95 1.53 s. 2,000 during the 6 s-think mixed load: p95 0.87 s | ✓ |
| 15,000 concurrent sessions / SSE connections | **15,000 SSE streams** held open from distinct sessions on this one VM, 0 errors | ✓ for SSE. Not run: 15,000 sessions also sending requests |
| 550 / 10k / 50k user profiles | 550-agent profile run (below). **10k and 50k profiles not run** (see "Not measured") | partial |
| 100 M activities for the 50k profile test | **Not generated.** Measured at 2.68 M rows | ✗ not measured |
| 99.9 % availability, RPO ≤ 5 min, RTO ≤ 1 h | Not a load-test result. RPO: WAL archived at least every 60 s (`archive_timeout`), so about a minute of writes is at risk; restore to the latest WAL kept the last write in the drill. RTO on the compose trial stack (57 MB database, 2026-10-21): data volume deleted → database open in 4 s, CRM healthy at 16 s, whole stack about 20 s. Not measured on a production-size database. Availability: not measured. `docs/runbooks/backups-and-restore.md` | RPO/RTO ✓ at trial size; availability not measured |
| 2 B activity rows/year capacity | Sizing arithmetic below, from measured bytes/row | arithmetic only |

## Mixed-load runs (k6, `infra/load/crm.js`)

The profile ramps agents over 2 min, holds for 5 min, then ramps down for 20 s. Agents each run this loop: pre-dial
check, save an entry, every 5th loop list their day, every 10th open their dashboard, every 20th open the leaderboard,
then wait THINK × U(0.5, 1.5) seconds. 50 supervisors open 4 team dashboards every ~15 s. 3 pasters each send a
1,000-row Refine & Save every ~20 s. Each VU signs in once and keeps its session. A real agent makes ~170 calls a day,
about 3.2 saves/s across 550 agents over 8 h. The 20 s profile ran at ~23.5 saves/s (~7×). The 6 s profile ran at
~75 saves/s (~23×).

| Run | Topology | req/s | pre-dial p95 / p99 | read p95 / p99 | write p95 / p99 | paste p50 / p95 | errors |
|---|---|---|---|---|---|---|---|
| final-split-think20 | interactive 3 + bulk 2 | 64.4 | 30 ms / 352 ms | 88 ms / 517 ms | 77 ms / 426 ms | 1.41 s / **2.91 s** | 0 / 29,602 |
| final-split-think6 | interactive 3 + bulk 2 | 184.4 | 99 ms / 1.74 s | 160 ms / 1.54 s | 154 ms / 1.82 s | 1.60 s / 3.78 s | 0 / 82,477 |
| final-shared-think6 | one pool of 3 (pastes too) | 183.6 | 134 ms / 2.81 s | 185 ms / 1.22 s | 193 ms / 1.82 s | 3.50 s / 10.39 s | 0 / 82,346 |
| final-paste-alone | bulk 2, 1 paster, 1 agent | — | 13 ms | 69 ms | 58 ms | 1.23 s / 1.29 s | 0 |

CPU (vmstat, 10 s samples) during the 6 s-think runs averaged 46–49 % user + 11 % system, with an average run queue of
4.6–5.3 on 4 vCPUs, so the box was saturated in bursts. The 20 s-think run averaged 25 % + 6 %, run queue 1.6.

In one 6 s-think run the sign-in limit (left over from earlier runs) admitted only 397 of the 603 users: about 397
agents, no supervisors, no pasters. That run made 161 req/s with **no pastes**, and its pre-dial / read / write p99 were
60 / 118 / 112 ms, with nothing slower than 250 ms. The load was also lower, so it is not a valid §24 run. Still, it
points to 1,000-row pastes competing for CPU as the cause of the ~1–3 s p99 tails. Moving pastes to their own processes
fixed the p95s but not all of the CPU contention on a single 4-vCPU box.

## SSE fan-out (`infra/load/sse_fanout.py`)

N sessions are minted directly in the database. N SSE streams are opened from raw-socket clients spread over 2–4 processes,
at most 100 connection attempts in flight per process. Then one `sale.celebrated` event goes through the real path
(outbox row → dispatcher → Redis → each API worker → each stream). The latency is measured from the outbox commit to
the moment each client sees the event.

| Streams | Server | Open time | Received | p50 | p95 | max | Errors |
|---|---|---|---|---|---|---|---|
| 1,000 | 3 workers, idle | 3.0 s | 1,000 | 0.074 s | 0.087 s | 0.089 s | 0 |
| 3,000 | 3 workers, idle | 7.7 s | 3,000 | 0.284 s | 0.324 s | 0.332 s | 0 |
| 5,000 | 3 workers, idle | 14.4 s | 5,000 | 0.330 s | 0.422 s | 0.586 s | 0 |
| 10,000 | 3 workers, idle | 29.0 s | 10,000 | 0.665 s | 1.532 s | 1.575 s | 0 |
| 15,000 | 3 workers, idle | 50.1 s | 15,000 | 2.407 s | 2.796 s | 2.842 s | 0 |
| 2,000 | 3 workers during the 6 s-think mixed load | 13.7 s | 2,000 | 0.761 s | 0.865 s | 1.179 s | 0 |

With 3 workers, ~3,300 streams per worker (10,000 total) gave p95 1.5 s and ~5,000 per worker (15,000 total) gave
2.8 s, close to the 3 s target. More API processes or pods spread the per-event write loop and lower that.

## What the measurements changed

| Finding | Fix | Before → after |
|---|---|---|
| 1,000-row paste took 12.6 s (per-row savepoints, rollup and ledger writes per row) | Staged rollups/ledger flushed once per batch; fast path without savepoints, with fallback to per-row savepoints; prebuilt statements | 12.6 s → 1.6 s alone |
| Paste issued two round trips per row (registry lock + insert) | Lock every registry row of the batch up front (one statement) and read them from a per-batch cache | 1.7 s → 1.17 s (save function); HTTP p95 alone 1.66 s → 1.29 s |
| Concurrent pastes sharing numbers **deadlocked** (PostgreSQL killed one, which fell back to slow per-row saves) | Ordered up-front locks (sorted phone keys) | Deadlocks in the mixed run → 0 in all later runs; regression test with opposite-order batches |
| Pastes in the same processes as interactive requests (GIL + CPU) | Bulk pool: `/api/activities/batch`, `/api/imports*`, `/api/admin/legacy-imports*` routed to `crm-bulk` (compose edge, Helm Ingress) | At 6 s think: paste p95 10.4 s → 3.78 s; pre-dial p95 134 → 99 ms |
| Each request authenticated with 3–4 queries, settings read per key | One auth query (session + user + teams + Agreements grant); org settings loaded once per transaction | Single save 18 ms / 17 statements → 11.7 ms / 11 (in-process) |
| SSE broke above ~1,000 clients: `aclose(): asynchronous generator is already running` on every disconnect, one Redis connection per browser, blocking DB calls on the event loop | Cancel the waiting task instead of closing a running generator; one Redis subscription per worker fanned out in memory; auth and backlog in the threadpool | 3,000 streams: 68 opened, none delivered → 15,000 delivered, p95 2.80 s |
| k6 sign-ins hit the per-IP limit (1,000 per 15 min) after repeated runs | Kept (it protects sign-in). Load runs clear the limiter first | — |

## Bottlenecks now, in order

1. **CPU on a single box.** All p99 tails above ~0.5 s coincide with pastes. Production should put PostgreSQL on its
   own host and run the interactive and bulk pools as separate pods (the Helm chart does this by default), each with
   `workers` ≈ its vCPUs. The API is stateless (Redis broker, DB sessions), so it scales out by adding pods.
2. **Paste cost per row**, about 1.2 ms per row when alone: one INSERT round trip plus refine and encryption in Python.
   If the 6 s-think paste p95 matters, the next step is one multi-row INSERT for the batch's activities.
3. **PostgreSQL is untuned** (128 MB `shared_buffers`). Size it to the host (for example 25 % of RAM) before go-live.
4. **SSE writes per event** grow linearly with the streams in each worker. Plan ≤ ~3,000 concurrent browsers per API
   worker (p95 ≈ 1.5 s measured at ~3,300 per worker).

## Not measured (and why)

- **10k and 50k user profiles.** That many concurrent k6 VUs needs a separate load-generator fleet and RAM this VM does
  not have. Arithmetic only: the 6 s-think run's ~75 saves/s equals the save rate of ~12,700 agents at 170 calls per
  8-hour day, but with 600 sessions, not 12,700.
- **100 M-activity dataset** (§24 asks for it for the 50k profile). It would need roughly 50+ GB of database and hours
  to generate, beyond this environment's disk allowance. Hot paths read the current-month partition and indexed keys,
  but that was not verified at 100 M rows.
- Multi-host layouts, long soaks (hours), failover, and the Agreements app under load.

## Sizing (arithmetic from measured sizes, not a load test)

Measured on the 2.68 M-row dataset: an activity uses **~320 B of heap + ~194 B of index ≈ 514 B**. A phone-registry
row uses ~420 B including indexes.

- 2 B activities/year ≈ **1.0 TB/year** for activities (heap + indexes), or ~167 M rows / ~86 GB per monthly partition.
- 550 agents × 170 calls × ~250 workdays ≈ 23 M rows/year ≈ 12 GB/year.
- Monthly partitions are created ahead by `crm_ensure_partition_for`. Old partitions can be detached or archived
  without rewriting the table.

## How to reproduce

```bash
tools/dev_services.sh start                                   # PostgreSQL 16 + Redis 7 (dev)
createdb -h 127.0.0.1 -p 54329 -U postgres crm_load
cd apps/crm-api && CRM_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:54329/crm_load alembic upgrade head
export CRM_FIELD_KEY=... CRM_PHONE_KEY_SECRET=...              # any test values; the same for generator and server
CRM_DATABASE_URL=... python ../../infra/load/gen_data.py --agents 550 --activities 2500000 --days 60
# interactive pool and bulk pool (local mode allows password sign-in for the synthetic users)
CRM_DATABASE_URL=... CRM_REDIS_URL=redis://127.0.0.1:63799/1 CRM_MODE=local CRM_ORIGIN=http://127.0.0.1:8766 \
  CRM_INLINE_SCHEDULER=0 uvicorn tfsc_crm.main:app --port 8766 --workers 3 --no-access-log &
CRM_DATABASE_URL=... CRM_REDIS_URL=redis://127.0.0.1:63799/1 CRM_MODE=local CRM_ORIGIN=http://127.0.0.1:8766 \
  CRM_INLINE_SCHEDULER=0 CRM_INLINE_DISPATCHER=0 uvicorn tfsc_crm.main:app --port 8767 --workers 2 --no-access-log &
cd ../..
redis-cli -p 63799 -n 1 FLUSHDB                               # reset sign-in limits between runs
infra/load/run.sh split-think20 http://127.0.0.1:8766 AGENTS=550 SUPS=50 PASTERS=3 THINK=20 HOLD=5m RAMP=2m \
  BULK=http://127.0.0.1:8767                                  # k6 summary + vmstat in infra/load/results/
CRM_DATABASE_URL=... python infra/load/sse_fanout.py http://127.0.0.1:8766 15000 4   # raise `ulimit -n` first
```

`infra/load/results/` and `infra/load/users.json` are git-ignored, so rerun to get fresh files.
