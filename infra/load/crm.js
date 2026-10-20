// k6 load test for the CRM API (§24). Profiles: AGENTS / SUPS / PASTERS / HOLD set the load; THINK sets agent pacing.
//   docker run --rm --network host -v $PWD/infra/load:/load grafana/k6 run -e BASE=http://127.0.0.1:8766 \
//     -e AGENTS=550 -e SUPS=50 -e PASTERS=5 -e HOLD=5m /load/crm.js
// Agents: pre-dial check -> save an entry -> sometimes list their day / open their dashboard; then think THINK seconds.
// Supervisors: team dashboards every ~15 s. Pasters: Refine & Save of 1,000 rows every ~20 s each.
// Thresholds are the §24 targets; k6 exits non-zero when one is missed.
import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';

const BASE = __ENV.BASE || 'http://127.0.0.1:8766';
const USERS = JSON.parse(open('./users.json'));
const N_AGENTS = Number(__ENV.AGENTS || 550);
const N_SUPS = Number(__ENV.SUPS || 50);
const N_PASTERS = Number(__ENV.PASTERS || 5);
const HOLD = __ENV.HOLD || '5m';
const RAMP = __ENV.RAMP || '2m';
const THINK = Number(__ENV.THINK || 6); // seconds between calls per agent (real agents: ~170 s at 170 calls/day)

export const options = {
  scenarios: {
    agents: { executor: 'ramping-vus', exec: 'agent', startVUs: 0, gracefulRampDown: '20s',
      stages: [{ duration: RAMP, target: N_AGENTS }, { duration: HOLD, target: N_AGENTS }, { duration: '20s', target: 0 }] },
    supervisors: { executor: 'constant-vus', exec: 'supervisor', vus: N_SUPS, duration: HOLD, startTime: RAMP },
    pasters: { executor: 'constant-vus', exec: 'paste', vus: N_PASTERS, duration: HOLD, startTime: RAMP },
  },
  thresholds: {
    'http_req_duration{kind:read}': ['p(95)<200'],
    'http_req_duration{kind:write}': ['p(95)<300'],
    'http_req_duration{kind:precheck}': ['p(95)<100'],
    'http_req_duration{kind:paste}': ['p(95)<3000'],
    'http_req_failed{kind:read}': ['rate<0.01'],
    'http_req_failed{kind:write}': ['rate<0.01'],
    'http_req_failed{kind:precheck}': ['rate<0.01'],
    'http_req_failed{kind:paste}': ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
  noCookiesReset: true, // each VU is one signed-in person for the whole test
};

let csrf = null;
function login(email) {
  const r = http.post(`${BASE}/api/auth/login`, JSON.stringify({ email, password: USERS.password }),
    { headers: { 'Content-Type': 'application/json' }, tags: { kind: 'login', name: 'login' } });
  check(r, { 'login 200': (x) => x.status === 200 });
  csrf = r.status === 200 ? r.json('csrf') : null;
}
const json = () => ({ 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, Origin: BASE });
const phone = () => `(415) 5${String(Math.floor(Math.random() * 100)).padStart(2, '0')}-${String(Math.floor(Math.random() * 10000)).padStart(4, '0')}`;

export function agent() {
  // VU ids are 1-based and unique across scenarios: agents use the first N_AGENTS accounts.
  if (!csrf) { login(USERS.agents[(exec.vu.idInTest - 1) % (USERS.agents.length - N_PASTERS)]); sleep(Math.random() * 2); }
  if (!csrf) { sleep(5); return; }
  const p = phone();
  http.get(`${BASE}/api/precheck?phone=${encodeURIComponent(p)}`, { tags: { kind: 'precheck', name: 'precheck' } });
  const connected = Math.random() < 0.17;
  const r = http.post(`${BASE}/api/activities`, JSON.stringify({ name: `Load Practice ${Math.floor(Math.random() * 1e6)}`, phone: p, called: true,
    connected, text_sent: connected && Math.random() < 0.15 }), { headers: json(), tags: { kind: 'write', name: 'save entry' } });
  check(r, { 'saved': (x) => x.status === 200 });
  const i = exec.vu.iterationInScenario;
  if (i % 5 === 0) http.get(`${BASE}/api/activities?limit=50`, { tags: { kind: 'read', name: 'my day' } });
  if (i % 10 === 0) http.get(`${BASE}/api/dashboard/summary?preset=today`, { tags: { kind: 'read', name: 'my dashboard' } });
  if (i % 20 === 0) http.get(`${BASE}/api/dashboard/leaderboard?preset=today&page_size=25`, { tags: { kind: 'read', name: 'leaderboard' } });
  sleep(THINK * (0.5 + Math.random()));
}

export function supervisor() {
  if (!csrf) login(USERS.supervisors[(exec.vu.idInTest - 1) % USERS.supervisors.length]);
  if (!csrf) { sleep(5); return; }
  for (const [path, name] of [['/api/dashboard/summary?preset=today&scope=team', 'team summary'], ['/api/dashboard/team-board', 'team board'],
    ['/api/dashboard/funnel?preset=mtd&scope=team', 'funnel mtd'], ['/api/dashboard/summary?preset=mtd&scope=team', 'team mtd']]) {
    http.get(`${BASE}${path}`, { tags: { kind: 'read', name } });
  }
  sleep(15 * (0.5 + Math.random()));
}

export function paste() {
  if (!csrf) login(USERS.agents[USERS.agents.length - 1 - ((exec.vu.idInTest - 1) % N_PASTERS)]);
  if (!csrf) { sleep(5); return; }
  const rows = Array.from({ length: 1000 }, (_, k) => ({ name: `paste clinic ${k} llc`, phone: phone(), called: 'Yes', connected: k % 6 === 0 ? 'Yes' : 'No' }));
  const key = `load-${exec.vu.idInTest}-${exec.vu.iterationInScenario}-${Date.now()}`;
  const r = http.post(`${BASE}/api/activities/batch`, JSON.stringify({ request_key: key, rows, source: 'grid' }),
    { headers: json(), tags: { kind: 'paste', name: 'refine & save 1000' }, timeout: '60s' });
  check(r, { 'paste saved': (x) => x.status === 200 && x.json('saved') + x.json('rejected') + x.json('review') === 1000 });
  sleep(20 * (0.5 + Math.random()));
}
