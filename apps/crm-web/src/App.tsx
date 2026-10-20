import { useEffect, useState } from "react";
import {
  LayoutDashboard,
  Phone,
  Table2,
  CalendarClock,
  Target,
  Building2,
  Users,
  Settings,
  History,
  LogOut,
  Plus,
  ArrowUpRight,
  ChevronRight,
  ShieldCheck,
  Search,
  Check,
  RefreshCw,
  Menu,
  Copy,
  Upload,
  LockKeyhole,
  CheckCircle2,
  ArrowRight,
  CalendarDays,
  Network,
  AlertTriangle,
  GripVertical,
  ChevronUp,
  ChevronDown,
} from "lucide-react";
import Profile from './Profile';
import ClientHistory from './ClientHistory';
import FollowupTask from './FollowupTask';
import Meetings from './Meetings';
import RecordPicker from './RecordPicker';
import {
  api,
  send,
  rows,
  label,
  nameOf,
  num,
  pct,
  dateTime,
  pacificDate,
  parseSheet,
  initials,
} from "./api";
import type { Data } from "./api";
import {
  Alert,
  Loading,
  Empty,
  Field,
  TriState,
  Modal,
  SectionTitle,
  Badge,
  WorldClocks,
  SearchBox,
  useResource,
  AuditList,
  usePagedResource,
  CursorControls,
} from "./components";

type View =
  | "overview"
  | "work"
  | "sheet"
  | "followups"
  | "leads"
  | "clients"
  | "meetings"
  | "users"
  | "teams"
  | "conflicts"
  | "audit"
  | "settings";
const menu: [View, string, any][] = [
  ["overview", "Overview", LayoutDashboard],
  ["work", "My work", Phone],
  ["sheet", "Sheet view", Table2],
  ["followups", "Follow-ups", CalendarClock],
  ["leads", "Lead pipeline", Target],
  ["clients", "Clients", Building2],
  ["meetings", "Meetings", CalendarDays],
  ["users", "People", Users],
  ["teams", "Teams", Network],
  ["conflicts", "Conflict review", AlertTriangle],
  ["audit", "Activity trail", History],
  ["settings", "Settings", Settings],
];
const management = ["meetings", "teams", "conflicts"];
export default function App() {
  const [user, setUser] = useState<Data | null>(null),
    [config, setConfig] = useState<Data>({}),
    [boot, setBoot] = useState(true),
    [view, setView] = useState<View>("overview"),
    [rev, setRev] = useState(0),
    [open, setOpen] = useState(false),
    [toast, setToast] = useState(""),
    [profileOpen, setProfileOpen] = useState(false),
    [sale, setSale] = useState<Data | null>(null);
  async function refreshIdentity() {
    try {
      const me = await api("/auth/me");
      setUser(me.user);
      if(me.user.reset_required){setConfig({});setProfileOpen(true);}else setConfig(await api("/config"));
    } catch {
      setUser(null);
    } finally {
      setBoot(false);
    }
  }
  useEffect(() => {
    refreshIdentity();
  }, []);
  useEffect(() => {
    if (!user || user.reset_required) return;
    let since = Date.now() / 1000;
    const seen = new Set<string>();
    const t = setInterval(() => {
      api("/events?since=" + since)
        .then((r) => {
          since = r.server_time;
          for (const event of rows(r)) {
            if (!seen.has(event.id)) {
              seen.add(event.id);
              setSale(event.payload);
            }
          }
        })
        .catch(() => {});
    }, 15000);
    return () => clearInterval(t);
  }, [user?.id]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(t);
  }, [toast]);
  if (boot) return <Loading />;
  if (!user) return <Auth onDone={refreshIdentity} />;
  if(user.reset_required)return <div className="required-password"><Profile user={user} required onClose={()=>setProfileOpen(false)} onUpdated={refreshIdentity}/></div>;
  const manager = user.role !== "sales_agent";
  const update = (message = "Saved successfully") => {
    setRev((r) => r + 1);
    setToast(message);
  };
  const permitted = (id: string) => !management.includes(id) || manager;
  return (
    <div className="shell">
      <aside className={"sidebar " + (open ? "open" : "")}>
        <a className="brand" href="/" aria-label="TFSC Sales CRM">
          <img src="/assets/logo.png" alt="Twenty Four Seven Consultancy" />
          <strong>
            TFSC <span>Sales CRM</span>
          </strong>
          <small>THE RELATIONSHIP WORKSPACE</small>
        </a>
        <div className="nav-caption">WORKSPACE</div>
        <nav>
          {menu
            .filter(
              ([id]) =>
                permitted(id) &&
                (id !== "users" || manager) &&
                (id !== "settings" ||
                  ["super_admin", "director_admin"].includes(user.role)),
            )
            .map(([id, title, Icon]) => (
              <button
                key={id}
                className={view === id ? "active" : ""}
                onClick={() => {
                  setView(id);
                  setOpen(false);
                }}
              >
                <Icon size={18} />
                <span>{title}</span>
                {view === id && <i />}
              </button>
            ))}
        </nav>
        {user.agreements_access && (
          <a
            className="agreements-link"
            href={config.agreements_url}
            target="_blank"
            rel="noreferrer"
          >
            <ShieldCheck size={20} />
            <div>
              TFSC Compliance<small>Open agreements workspace</small>
            </div>
            <ArrowUpRight size={17} />
          </a>
        )}
        <div className="profile">
          <button className="avatar profile-trigger" aria-label="Open your profile and security" onClick={()=>setProfileOpen(true)}>{user.has_photo?<img src={'/api/profile/photo/'+user.id+'?v='+rev} alt=""/>:initials(user.name)}</button>
          <button className="profile-name" onClick={()=>setProfileOpen(true)}>
            <strong>{user.name}</strong>
            <small>{label(user.role)}</small>
          </button>
          <button
            className="icon-btn"
            aria-label="Sign out"
            onClick={async () => {
              await send("/auth/logout", {});
              setUser(null);
            }}
          >
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <button
            className="icon-btn mobile-menu"
            aria-label="Open navigation"
            onClick={() => setOpen(!open)}
          >
            <Menu />
          </button>
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <b>{menu.find((v) => v[0] === view)?.[1]}</b>
          </div>
          <WorldClocks compact />
          <div className="live-dot">Connected workspace</div>
        </header>
        <main>
          {view === "overview" && (
            <Dashboard
              rev={rev}
              manager={manager}
              onNew={() => setView("work")}
            />
          )}
          {view === "work" && (
            <Activities rev={rev} user={user} updated={update} />
          )}
          {view === "sheet" && <Sheet updated={update} />}
          {["followups", "leads", "clients"].includes(view) && (
            <Leads kind={view} rev={rev} user={user} updated={update} />
          )}
          {view === "meetings" && <Meetings rev={rev} updated={update} />}
          {view === "users" && (
            <People rev={rev} config={config} user={user} updated={update} />
          )}
          {view === "teams" && <Teams rev={rev} user={user} updated={update} />}
          {view === "conflicts" && <Conflicts rev={rev} updated={update} />}
          {view === "audit" && (
            <>
              <SectionTitle
                eyebrow="ACCOUNTABILITY"
                title="Activity trail"
                description="A clear record of changes, decisions and the people behind them."
              />
              <section className="panel">
                <AuditList revision={rev} />
              </section>
            </>
          )}
          {view === "settings" && (
            <SettingsView
              rev={rev}
              user={user}
              updated={(s) => {
                update(s);
                refreshIdentity();
              }}
            />
          )}
        </main>
        <footer>
          TFSC Sales CRM{" "}
          <span>Pacific business dates · Protected workspace</span>
          <span>Version {config.version || "1.0.0"}</span>
        </footer>
      </div>
      {profileOpen&&<Profile user={user} onClose={()=>setProfileOpen(false)} onUpdated={()=>{setRev(r=>r+1);refreshIdentity();}}/>}
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={20} />
          {toast}
        </div>
      )}
      {sale && (
        <Modal
          title="A new success worth celebrating"
          onClose={() => setSale(null)}
        >
          <div className="celebration">
            <div>✦</div>
            <h2>Congratulations, {sale.name}!</h2>
            <p>A successful sale, and another great team effort.</p>
            <p>Thank you, {sale.supervisor}, and the supporting team.</p>
            <button className="btn" onClick={() => setSale(null)}>
              Keep the momentum going
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
function Auth({ onDone }: { onDone: () => void }) {
  const [sso, setSso] = useState(false);
  useEffect(() => {
    api("/auth/sso/status")
      .then((r) => setSso(r.enabled))
      .catch(() => {});
  }, []);
  const [setup, setSetup] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [f, setF] = useState<Data>({
      email: "",
      password: "",
      name: "",
      setup_key: "",
    });
  useEffect(() => {
    api("/auth/status")
      .then((r) => setSetup(r.setup_required))
      .catch((e) => setError(e.message));
  }, []);
  return (
    <div className="auth-shell">
      <section className="auth-story">
        <img src="/assets/logo.png" />
        <div className="eyebrow light">TFSC SALES CRM</div>
        <h1>
          Every conversation.
          <br />A clearer opportunity.
        </h1>
        <p>
          Your people, follow-ups and client relationships.
          <br />
          Connected in one thoughtful workspace.
        </p>
        <div className="auth-orbit">
          <span />
          <i />
          <b>Built around your team.</b>
        </div>
        <small>TWENTY FOUR SEVEN CONSULTANCY</small>
      </section>
      <section className="auth-form">
        <div className="eyebrow">YOUR PRIVATE WORKSPACE</div>
        <h1>{setup ? "Create your workspace" : "Welcome back"}</h1>
        <p>
          {setup
            ? "Set up the first Super Admin account to begin."
            : "Sign in to pick up where your team left off."}
        </p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              await send("/auth/" + (setup ? "setup" : "login"), f);
              onDone();
            } catch (e: any) {
              setError(e.message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {setup && (
            <>
              <Field label="Your name">
                <input
                  required
                  value={f.name}
                  onChange={(e) => setF({ ...f, name: e.target.value })}
                />
              </Field>
              <Field
                label="Workspace setup key"
                hint="Stored in the CRM .data/setup-key.txt file on the server."
              >
                <input
                  required
                  type="password"
                  autoComplete="off"
                  value={f.setup_key}
                  onChange={(e) => setF({ ...f, setup_key: e.target.value })}
                />
              </Field>
            </>
          )}
          <Field label="Work email">
            <input
              required
              type="email"
              autoComplete="username"
              value={f.email}
              onChange={(e) => setF({ ...f, email: e.target.value })}
            />
          </Field>
          <Field label="Password" hint={setup ? "At least 12 characters." : ""}>
            <input
              required
              type="password"
              minLength={setup ? 12 : undefined}
              autoComplete={setup ? "new-password" : "current-password"}
              value={f.password}
              onChange={(e) => setF({ ...f, password: e.target.value })}
            />
          </Field>
          {error && <Alert>{error}</Alert>}
          <button className="btn full" disabled={busy}>
            {busy ? "Please wait…" : setup ? "Create workspace" : "Sign in"}
            <ArrowRight size={17} />
          </button>
        </form>
        {sso && (
          <a className="btn secondary full" href="/api/auth/sso/login">
            Sign in with your organization
          </a>
        )}
        <div className="auth-security">
          <ShieldCheck size={18} />
          Access is assigned by your administrator.
        </div>
      </section>
    </div>
  );
}
function Dashboard({
  rev,
  manager,
  onNew,
}: {
  rev: number;
  manager: boolean;
  onNew: () => void;
}) {
  const end = pacificDate();
  const [from, setFrom] = useState(end.slice(0, 8) + "01"),
    [to, setTo] = useState(end),
    [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 30000);
    return () => clearInterval(t);
  }, []);
  const { data, loading, error } = useResource(
    `/dashboard?from=${from}&to=${to}`,
    rev + tick,
  );
  const m = data?.metrics || {};
  const [order, setOrder] = useState([
    "trend",
    "pipeline",
    "leaderboard",
    "clocks",
  ]);
  const [dragged,setDragged]=useState<string|null>(null),[layoutMessage,setLayoutMessage]=useState('');
  useEffect(() => {
    if (manager)
      api("/dashboard/layout")
        .then((r) => {
          const allowed=['trend','pipeline','leaderboard','clocks'];
          const saved=Array.isArray(r.layout)?[...new Set<string>(r.layout.filter((id:any)=>allowed.includes(id)))]:[];
          if (saved.length) setOrder([...saved,...allowed.filter(id=>!saved.includes(id))]);
        })
        .catch(() => {});
  }, [manager]);
  async function move(id: string,target:number) {
    const next=order.filter(x=>x!==id);next.splice(Math.max(0,Math.min(target,next.length)),0,id);
    if(next.join()===order.join())return;
    const previous=order;setOrder(next);setLayoutMessage('Saving dashboard arrangement…');
    try{await send("/dashboard/layout", { layout: next });setLayoutMessage('Dashboard arrangement saved.');}
    catch(e:any){setOrder(previous);setLayoutMessage('Arrangement could not be saved: '+e.message);}
  }
  return (
    <>
      <SectionTitle
        eyebrow="A BETTER VIEW OF YOUR DAY"
        title="Your team. Moving forward."
        description="From the first conversation to the next client relationship."
        actions={
          <button className="btn" onClick={onNew}>
            <Plus size={17} />
            New activity
          </button>
        }
      />
      <div className="dashboard-banner">
        <div>
          <span className="eyebrow light">PERFORMANCE AT A GLANCE</span>
          <h2>Make your next conversation count.</h2>
          <p>Keep your follow-ups close and your progress in view.</p>
        </div>
        <div className="date-filter">
          <label>
            From
            <input
              aria-label="Report from"
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </label>
          <span>→</span>
          <label>
            To
            <input
              aria-label="Report to"
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </label>
        </div>
      </div>
      {error && <Alert>{error}</Alert>}
      {loading && !data ? (
        <Loading />
      ) : (
        <>
          <div className="metric-grid">
            {[
              ["Call attempts", num(m.attempts), "Activity this period", Phone],
              [
                "Connection rate",
                pct(m.connection_rate),
                `${num(m.connections)} conversations`,
                Network,
              ],
              [
                "Quality score",
                pct(m.quality),
                "Highest stage per attempted activity",
                Target,
              ],
              [
                "Confirmed sales",
                num(m.sales),
                "Supervisor-confirmed sign-ups",
                CheckCircle2,
              ],
            ].map(([title, value, sub, Icon]: any) => (
              <article className="metric-card" key={title}>
                <div>
                  <span>{title}</span>
                  <Icon size={19} />
                </div>
                <strong>{value}</strong>
                <small>{sub}</small>
              </article>
            ))}
          </div>
          <div className="dashboard-grid">
            {order.map((id) => (
              <section
                className={
                  "panel widget " +
                  (id === "trend" || id === "leaderboard" ? "wide-widget" : "")+(dragged===id?' dragging':'')
                }
                key={id}
                onDragOver={e=>{if(manager&&dragged){e.preventDefault();e.dataTransfer.dropEffect='move';}}}
                onDrop={e=>{e.preventDefault();if(manager&&dragged&&dragged!==id)void move(dragged,order.indexOf(id));setDragged(null);}}
              >
                <div className="panel-heading">
                  <div>
                    <h2>
                      {
                        {
                          trend: "Activity over time",
                          pipeline: "Your opportunity pipeline",
                          leaderboard: "Performance overview",
                          clocks: "Across the time zones",
                        }[id]
                      }
                    </h2>
                    <small>
                      {id === "trend"
                        ? "Attempted calls and connected conversations"
                        : id === "pipeline"
                          ? "Current stages for records in this period"
                          : id === "leaderboard"
                            ? "Derived metrics. Consistent definitions."
                            : "Always aligned with Pacific business time"}
                    </small>
                  </div>
                  {manager && (
                    <div className="widget-controls" aria-label={'Arrange '+id+' widget'}>
                      <button className="icon-btn drag-handle" draggable aria-label={'Drag '+id+' widget. Use adjacent buttons to reorder with the keyboard.'} title="Drag to rearrange" onDragStart={e=>{setDragged(id);e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',id);}} onDragEnd={()=>setDragged(null)}><GripVertical size={16}/></button>
                      <button className="icon-btn" disabled={order.indexOf(id)===0} aria-label={'Move '+id+' widget earlier'} onClick={()=>move(id,order.indexOf(id)-1)}><ChevronUp size={15}/></button>
                      <button className="icon-btn" disabled={order.indexOf(id)===order.length-1} aria-label={'Move '+id+' widget later'} onClick={()=>move(id,order.indexOf(id)+1)}><ChevronDown size={15}/></button>
                    </div>
                  )}
                </div>
                {id === "trend" &&
                  (data?.trend?.length ? (
                    <div className="chart-bars">
                      {data.trend.map((r: Data) => (
                        <div
                          key={r.date}
                          title={`${r.date}: ${r.attempts} attempts, ${r.connections} connected`}
                        >
                          <div className="bar-area">
                            <i
                              style={{
                                height: Math.max(
                                  3,
                                  (Number(r.attempts) /
                                    Math.max(
                                      ...data.trend.map((x: Data) =>
                                        Number(x.attempts),
                                      ),
                                      1,
                                    )) *
                                    140,
                                ),
                              }}
                            />
                            <b
                              style={{
                                height: Math.max(
                                  2,
                                  (Number(r.connections) /
                                    Math.max(
                                      ...data.trend.map((x: Data) =>
                                        Number(x.attempts),
                                      ),
                                      1,
                                    )) *
                                    140,
                                ),
                              }}
                            />
                          </div>
                          <small>{r.date.slice(5)}</small>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty
                      title="Your progress starts here"
                      detail="Add your first call activity to see the trend develop."
                    />
                  ))}
                {id === "pipeline" && (
                  <>
                    <div
                      className="score-ring"
                      style={
                        {
                          "--score": `${Number(m.composite || 0) * 360}deg`,
                        } as any
                      }
                    >
                      <div>
                        <strong>{pct(m.composite)}</strong>
                        <small>Composite score</small>
                      </div>
                    </div>
                    <div className="pipeline-list">
                      {data?.pipeline?.length ? (
                        data.pipeline.map((r: Data) => (
                          <div key={r.stage}>
                            <Badge value={r.stage} />
                            <strong>{num(r.count)}</strong>
                          </div>
                        ))
                      ) : (
                        <p className="muted">
                          No opportunities in this period.
                        </p>
                      )}
                    </div>
                  </>
                )}
                {id === "leaderboard" &&
                  (data?.leaderboard?.length ? (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Team member</th>
                            <th>Attempts</th>
                            <th>Connections</th>
                            <th>Quality</th>
                            <th>Composite</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.leaderboard.map((r: Data, i: number) => (
                            <tr key={i}>
                              <td>
                                <div className="person-cell">
                                  <span className="avatar small">
                                    {initials(r.name)}
                                  </span>
                                  <b>{r.name}</b>
                                </div>
                              </td>
                              <td>{num(r.attempts)}</td>
                              <td>{num(r.connections)}</td>
                              <td>{pct(r.quality)}</td>
                              <td>
                                <Badge value={r.band} />
                                <span className="cell-score">
                                  {pct(r.composite)}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <Empty
                      title="No activity to compare yet"
                      detail="Performance appears as your team records its work."
                    />
                  ))}
                {id === "clocks" && <WorldClocks />}
              </section>
            ))}
          </div>
          <p className="data-note" aria-live="polite">{layoutMessage}</p>
          <p className="data-note">
            Updated {dateTime(data?.updated_at)} Pacific · Quality 50% + volume
            25% + connectivity 25% · Policy {data?.policy_version}
          </p>
        </>
      )}
    </>
  );
}
function Activities({
  rev,
  user,
  updated,
}: {
  rev: number;
  user: Data;
  updated: (s?: string) => void;
}) {
  const [search, setSearch] = useState(""),
    [cursor, setCursor] = useState(""),
    [edit, setEdit] = useState<Data | null>(null),
    [details, setDetails] = useState<Data | null>(null);
  const { data, error, loading } = useResource(
    "/activities?q=" +
      encodeURIComponent(search) +
      "&cursor=" +
      encodeURIComponent(cursor),
    rev,
  );
  return (
    <>
      <SectionTitle
        eyebrow="CONVERSATIONS THAT MOVE THINGS FORWARD"
        title="My work"
        description="Research first. Record the outcome when your outreach is complete."
        actions={
          <button className="btn" onClick={() => setEdit({})}>
            <Plus size={17} />
            New activity
          </button>
        }
      />
      <section className="panel">
        <div className="toolbar">
          <SearchBox
            value={search}
            onChange={(v) => {
              setSearch(v);
              setCursor("");
            }}
          />
          <span className="muted">Yes · No · Unset</span>
        </div>
        {error && <Alert>{error}</Alert>}
        {loading ? (
          <Loading />
        ) : rows(data).length ? (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Practice / provider</th>
                    <th>Phone</th>
                    <th>Called</th>
                    <th>Connected</th>
                    <th>Stage</th>
                    <th>Next follow-up</th>
                    <th>Record</th>
                  </tr>
                </thead>
                <tbody>
                  {rows(data).map((r) => (
                    <tr key={r.id}>
                      <td>
                        <button
                          className="text-btn"
                          onClick={() => setDetails(r)}
                        >
                          {nameOf(r)}
                        </button>
                        <small>{r.email || r.business_date}</small>
                      </td>
                      <td>
                        <a href={"tel:" + r.phone}>{r.phone || "—"}</a>
                      </td>
                      <td>
                        {r.called === null ? "—" : r.called ? "Yes" : "No"}
                      </td>
                      <td>
                        {r.connected === null
                          ? "—"
                          : r.connected
                            ? "Yes"
                            : "No"}
                      </td>
                      <td>
                        <Badge value={r.stage} />
                      </td>
                      <td>{r.next_followup || "—"}</td>
                      <td>
                        <button
                          className="btn tiny secondary"
                          disabled={r.locked && user.role === "sales_agent"}
                          onClick={() => setEdit(r)}
                        >
                          {r.locked ? (
                            <>
                              <LockKeyhole size={13} />
                              Correct
                            </>
                          ) : (
                            "Edit"
                          )}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <button
                className="btn secondary tiny"
                disabled={!cursor}
                onClick={() => setCursor("")}
              >
                First page
              </button>
              <button
                className="btn secondary tiny"
                disabled={!data.next_cursor}
                onClick={() => setCursor(data.next_cursor)}
              >
                Next page
                <ChevronRight size={14} />
              </button>
            </div>
          </>
        ) : (
          <Empty
            title="Ready for the first conversation"
            detail="Add a provider, record research or paste your daily list in Sheet view."
          />
        )}
      </section>
      {edit && (
        <ActivityForm
          initial={edit}
          onClose={() => setEdit(null)}
          onSaved={() => {
            setEdit(null);
            updated();
          }}
        />
      )}
      {details && (
        <Modal
          title={nameOf(details)}
          subtitle="Client activity and follow-up"
          onClose={() => setDetails(null)}
        >
          <p>
            <a href={"tel:" + details.phone}>{details.phone}</a> ·{" "}
            {details.email}
          </p>
          <p>{details.remarks || "No notes recorded."}</p>
          <Badge value={details.stage} />
          <div className="form-actions">
            <button
              className="btn"
              onClick={async () => {
                try {
                  await send("/activities/" + details.id + "/followup", {});
                  updated("Follow-up added");
                  setDetails(null);
                } catch (e: any) {
                  alert(e.message);
                }
              }}
            >
              Add to follow-ups
            </button>
          </div>
          <AuditList path={"/audit?resource_id=" + details.id} />
        </Modal>
      )}
    </>
  );
}
function ActivityForm({
  initial,
  onClose,
  onSaved,
}: {
  initial: Data;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [f, setF] = useState<Data>({
      provider_name: "",
      phone: "",
      email: "",
      address: "",
      called: null,
      connected: null,
      text_sent: null,
      email_sent: null,
      next_followup: "",
      remarks: "",
      business_date: pacificDate(),
      ...initial,
    }),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const field = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal
      title={initial.id ? "Update activity" : "A new conversation starts here"}
      subtitle="Dates follow U.S. Pacific Time. Unset outcomes stay as research."
      onClose={onClose}
      wide
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            const r = await send(
              "/activities" + (f.id ? "/" + f.id : ""),
              f,
              f.id ? "PATCH" : "POST",
            );
            if (r.duplicate_severity) {
              setError(
                "Saved. This contact needs supervisor review: " +
                  label(r.duplicate_severity),
              );
              setTimeout(onSaved, 1800);
            } else onSaved();
          } catch (e: any) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-grid">
          <Field label="Provider or practice name">
            <input
              required
              value={f.provider_name}
              onChange={field("provider_name")}
            />
          </Field>
          <Field label="Business date · Pacific">
            <input
              type="date"
              required
              value={f.business_date}
              onChange={field("business_date")}
            />
          </Field>
          <Field label="Phone">
            <input
              value={f.phone}
              onChange={field("phone")}
              placeholder="(212) 555-0123"
            />
          </Field>
          <Field label="Email">
            <input type="email" value={f.email} onChange={field("email")} />
          </Field>
          <Field label="Address">
            <input value={f.address} onChange={field("address")} />
          </Field>
          <Field label="Next follow-up">
            <input
              type="date"
              value={(f.next_followup || "").slice(0, 10)}
              onChange={field("next_followup")}
            />
          </Field>
        </div>
        <div className="outcome-grid">
          {["called", "connected", "text_sent", "email_sent"].map((k) => (
            <Field label={label(k)} key={k}>
              <TriState
                value={f[k]}
                onChange={(v) => setF({ ...f, [k]: v })}
                label={label(k)}
              />
            </Field>
          ))}
        </div>
        <Field label="Conversation notes">
          <textarea rows={4} value={f.remarks} onChange={field("remarks")} />
        </Field>
        {initial.id && (
          <Field
            label="Reason for change"
            hint="Required for locked records or corrections on behalf of a team member."
          >
            <input value={f.reason || ""} onChange={field("reason")} />
          </Field>
        )}
        {error && <Alert>{error}</Alert>}
        <div className="form-actions">
          <span className="muted">
            {initial.edit_deadline
              ? "Edit deadline: " + dateTime(initial.edit_deadline) + " Pacific"
              : "The edit window starts with the first committed outcome."}
          </span>
          <button className="btn" disabled={busy}>
            {busy ? "Saving…" : "Save activity"}
            <Check size={16} />
          </button>
        </div>
      </form>
    </Modal>
  );
}
function Sheet({ updated }: { updated: (s?: string) => void }) {
  const [text, setText] = useState(""),
    [preview, setPreview] = useState<Data[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [key, setKey] = useState(crypto.randomUUID());
  async function refine() {
    setBusy(true);
    setError("");
    try {
      const r = await send("/refine", { rows: parseSheet(text) });
      setPreview(rows(r));
      return rows(r);
    } catch (e: any) {
      setError(e.message);
      return [];
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    let p = preview.length ? preview : await refine();
    if (!p.length) return;
    if (p.some((r) => !r.valid)) {
      setError(
        "Correct the flagged rows before saving. No rows have been saved.",
      );
      return;
    }
    setBusy(true);
    try {
      const r = await send("/import", {
        rows: p.map((r) => r.refined),
        idempotency_key: key,
      });
      updated(`${r.saved} records saved`);
      setText("");
      setPreview([]);
      setKey(crypto.randomUUID());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SectionTitle
        eyebrow="FAMILIAR SPEED. BETTER CONTROL."
        title="Sheet view"
        description="Copy rows from Excel, paste them here, then review the refinements before saving."
      />
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Bring your daily list</h2>
            <small>
              Paste with column headers. Up to 1,000 rows in each batch.
            </small>
          </div>
          <label className="btn secondary">
            <Upload size={16} />
            Load sheet
            <input
              type="file"
              accept=".xlsx,.csv,.tsv,.txt"
              hidden
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (f) {
                  try {
                    if (f.name.endsWith(".xlsx")) {
                      const form = new FormData();
                      form.append("file", f);
                      const r = await api("/import/file-preview", {
                        method: "POST",
                        body: form,
                      });
                      setText(r.tsv);
                      setError(
                        r.has_more
                          ? "Only the first 1,000 rows are previewed. Use the documented bulk importer for a larger workbook."
                          : "",
                      );
                    } else setText(await f.text());
                    setPreview([]);
                    setKey(crypto.randomUUID());
                  } catch (e: any) {
                    setError(e.message);
                  }
                }
              }}
            />
          </label>
        </div>
        <div className="sheet-columns">
          Date · Provider Name · Phone · Email · Call Attempt · Connected · Text
          Sent · Email Sent · … · Remarks
        </div>
        <textarea
          className="paste-area"
          aria-label="Paste spreadsheet rows"
          placeholder={
            "Provider Name\tPhone\tEmail\tCall Attempt\tConnected\nPaste your spreadsheet rows here…"
          }
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPreview([]);
            setKey(crypto.randomUUID());
          }}
        />
        {error && <Alert>{error}</Alert>}
        <div className="form-actions">
          <span className="muted">
            Refine previews your changes. Your original values remain part of
            the record.
          </span>
          <button
            className="btn secondary"
            disabled={!text || busy}
            onClick={refine}
          >
            Refine
          </button>
          <button className="btn" disabled={!text || busy} onClick={save}>
            {busy ? "Working…" : "Refine and save"}
            <ArrowRight size={16} />
          </button>
        </div>
      </section>
      {preview.length > 0 && (
        <section className="panel">
          <div className="panel-heading">
            <h2>Review {preview.length} rows</h2>
            <Badge
              value={preview.every((r) => r.valid) ? "ready" : "needs_review"}
            />
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Provider / practice</th>
                  <th>Phone</th>
                  <th>Email</th>
                  <th>Review</th>
                </tr>
              </thead>
              <tbody>
                {preview.map((r) => (
                  <tr key={r.row}>
                    <td>{r.row}</td>
                    <td>{nameOf(r.refined || r.original)}</td>
                    <td>{r.refined?.phone || r.original?.phone}</td>
                    <td>{r.refined?.email || r.original?.email}</td>
                    <td>{r.error || r.warnings?.join(" ") || "Ready"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
function Leads({
  kind,
  rev,
  user,
  updated,
}: {
  kind: string;
  rev: number;
  user: Data;
  updated: (s?: string) => void;
}) {
  const paging = usePagedResource(
    kind === "clients"
      ? "/clients"
      : kind === "followups"
        ? "/followups"
        : "/leads",
    rev,
  );
  const {data,error,loading}=paging;
  const [selected, setSelected] = useState<Data | null>(null),
    [decision, setDecision] = useState(""),
    [reason, setReason] = useState(""),
    [failure, setFailure] = useState(""),
    [handoff, setHandoff] = useState<Data | null>(null),
    [history, setHistory] = useState<Data | null>(null),
    [task, setTask] = useState<Data | null>(null);
  const manager = user.role !== "sales_agent";
  const filtered = rows(data).filter(
    (r) => kind !== "followups" || r.stage.includes("follow_up"),
  );
  return (
    <>
      <SectionTitle
        eyebrow={
          kind === "clients"
            ? "RELATIONSHIPS, READY FOR THE NEXT STEP"
            : "KEEP THE RIGHT CONVERSATIONS CLOSE"
        }
        title={
          kind === "clients"
            ? "Client relationships"
            : kind === "followups"
              ? "Follow-up workspace"
              : "Lead pipeline"
        }
        description="Ownership, qualification and every next step stay connected to their original activity."
      />
      {error && <Alert>{error}</Alert>}
      {loading ? (
        <Loading />
      ) : filtered.length ? (
        <div className="lead-grid">
          {filtered.map((r) => (
            <article className="panel lead-card" key={r.id}>
              <div className="lead-card-top">
                <div className="client-icon">
                  <Building2 size={22} />
                </div>
                <Badge value={r.stage} />
              </div>
              <h2>{nameOf(r)}</h2>
              <a href={"tel:" + r.phone}>{r.phone || "No phone recorded"}</a>
              <p>{r.email || "No email recorded"}</p>
              <div className="lead-meta">
                <CalendarClock size={16} />{" "}
                {r.next_followup || "No follow-up date"}
              </div>
              <div className="lead-actions">
                {kind==='followups'&&<button className="text-btn" onClick={()=>setTask(r)}>Schedule follow-up</button>}
                {kind==='clients'&&user.agreements_access&&manager&&<button className="text-btn" onClick={()=>setHistory(r)}>Contract history</button>}
                <button
                  className="btn secondary small"
                  onClick={() => {
                    setSelected(r);
                    setDecision("");
                    setFailure("");
                  }}
                >
                  View & review
                  <ArrowUpRight size={15} />
                </button>
                {kind === "clients" && user.agreements_access && manager && (
                  <button className="text-btn" onClick={() => setHandoff(r)}>
                    Agreements →
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <section className="panel">
          <Empty
            title={
              kind === "clients"
                ? "Your next client starts with a conversation"
                : "No records in this workspace yet"
            }
            detail="Add a follow-up to an activity. Your supervisor can confirm its potential and qualify the lead."
          />
        </section>
      )}
      <CursorControls paging={paging}/>
      {selected && (
        <Modal
          title={nameOf(selected)}
          subtitle="Every decision is recorded in the activity trail."
          onClose={() => setSelected(null)}
        >
          <Badge value={selected.stage} />
          <p>
            {selected.phone} · {selected.email}
          </p>
          <Field label="Next action">
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
            >
              <option value="">Select an action</option>
              {selected.stage === "follow_up" && (
                <option value="request_strong">
                  Request strong follow-up review
                </option>
              )}
              {manager && (
                <>
                  {["follow_up", "strong_follow_up_pending"].includes(
                    selected.stage,
                  ) && (
                    <option value="confirm_strong">
                      Confirm strong follow-up
                    </option>
                  )}
                  {selected.stage === "strong_follow_up_confirmed" && (
                    <option value="qualify">
                      Qualify and lock lead ownership
                    </option>
                  )}
                  {["qualified_lead", "meeting"].includes(selected.stage) && (
                    <option value="confirm_sale">
                      Confirm successful sale
                    </option>
                  )}
                  {selected.stage !== "signed_up" &&
                    selected.stage !== "lost" && (
                      <option value="lost">Mark lost / disqualified</option>
                    )}
                  {selected.stage === "lost" && (
                    <option value="reopen">Reopen follow-up</option>
                  )}
                  {selected.stage === "signed_up" && (
                    <option value="reverse_sale">
                      Reverse sale confirmation
                    </option>
                  )}
                </>
              )}
            </select>
          </Field>
          <Field label="Reason / supporting notes">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </Field>
          {failure && <Alert>{failure}</Alert>}
          <div className="form-actions">
            <button
              className="btn"
              disabled={!decision}
              onClick={async () => {
                try {
                  await send("/leads/" + selected.id + "/transition", {
                    action: decision,
                    version: selected.version,
                    reason,
                  });
                  updated("Lead decision saved");
                  setSelected(null);
                  setReason("");
                } catch (e: any) {
                  setFailure(e.message);
                }
              }}
            >
              Save decision
              <Check size={16} />
            </button>
          </div>
          <AuditList path={"/audit?resource_id=" + selected.id} />
        </Modal>
      )}
      {handoff && <Handoff lead={handoff} onClose={() => setHandoff(null)} />}
      {history&&<ClientHistory lead={history} onClose={()=>setHistory(null)}/>}
      {task&&<FollowupTask lead={task} onClose={()=>setTask(null)} onSaved={()=>updated('Follow-up scheduled')}/>}
    </>
  );
}
function Handoff({ lead, onClose }: { lead: Data; onClose: () => void }) {
  const [f, setF] = useState<Data>({
      entity: "SCRIBE ALIGN LLC",
      client_name: nameOf(lead),
      address: lead.address || "",
      client_email: lead.email || "",
      effective_date: pacificDate(),
      document_types: ["baa"],
      idempotency_key: crypto.randomUUID(),
    }),
    [result, setResult] = useState<Data | null>(null),
    [error, setError] = useState("");
  return (
    <Modal
      title="Continue in TFSC Compliance"
      subtitle="Review client details, then open the protected agreements workspace."
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setResult(await send("/leads/" + lead.id + "/agreements", f));
          } catch (e: any) {
            setError(e.message);
          }
        }}
      >
        <Field label="Contracting company">
          <select
            value={f.entity}
            onChange={(e) => setF({ ...f, entity: e.target.value })}
          >
            {[
              "SCRIBE ALIGN LLC",
              "EXTENDED HEALTH SERVICES INC.",
              "24-7 CONSULTANCY LLC",
            ].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </Field>
        <Field label="Legal client name">
          <input
            required
            value={f.client_name}
            onChange={(e) => setF({ ...f, client_name: e.target.value })}
          />
        </Field>
        <Field label="Legal address">
          <textarea
            required
            value={f.address}
            onChange={(e) => setF({ ...f, address: e.target.value })}
          />
        </Field>
        <Field label="Effective date">
          <input
            type="date"
            value={f.effective_date}
            onChange={(e) => setF({ ...f, effective_date: e.target.value })}
          />
        </Field>
        <p className="muted">
          The BAA draft is transferred first. Select services and fees in TFSC
          before submitting the final agreement for review.
        </p>
        {error && <Alert>{error}</Alert>}
        <button className="btn">
          Prepare agreement draft
          <ArrowRight size={16} />
        </button>
      </form>
      {result && (
        <div className="handoff-result">
          <Alert success>
            Draft prepared. Sign in to TFSC and redeem this one-time code.
          </Alert>
          <code>{result.redeem_token}</code>
          <div className="form-actions">
            <button
              className="btn secondary"
              onClick={() =>
                navigator.clipboard.writeText(result.redeem_token || "")
              }
            >
              Copy code
              <Copy size={16} />
            </button>
            <a
              className="btn"
              target="_blank"
              rel="noreferrer"
              href={result.agreements_url}
            >
              Open TFSC
              <ArrowUpRight size={16} />
            </a>
          </div>
        </div>
      )}
    </Modal>
  );
}
function People({
  rev,
  config,
  user,
  updated,
}: {
  rev: number;
  config: Data;
  user: Data;
  updated: (s?: string) => void;
}) {
  const [search,setSearch]=useState('');
  const paging=usePagedResource('/users?q='+encodeURIComponent(search),rev);
  const {data,loading,error}=paging;
  const [form, setForm] = useState<Data | null>(null),
    [failure, setFailure] = useState("");
  const roles = ["sales_agent", "supervisor", "manager", "director_admin"];
  return (
    <>
      <SectionTitle
        eyebrow="PEOPLE AND PERMISSIONS"
        title="Your team, connected"
        description="Accounts, reporting relationships and access, managed together."
        actions={
          user.can_manage_users && (
            <button
              className="btn"
              onClick={() => {
                setFailure("");
                setForm({
                  name: "",
                  email: "",
                  password: "",
                  role: "sales_agent",
                  manager_id: user.id,
                  domain_id: "scribe_align",
                  project_id: "",
                  agent_id: "",
                  routing_view: false,
                });
              }}
            >
              <Plus size={17} />
              Add person
            </button>
          )
        }
      />
      <section className="panel">
        <div className="toolbar"><SearchBox value={search} onChange={setSearch} placeholder="Search people by name…"/><span className="muted">People in your reporting scope</span></div>
        {error && <Alert>{error}</Alert>}
        {loading ? (
          <Loading />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Role</th>
                  <th>Agent ID</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows(data).map((r) => (
                  <tr key={r.id}>
                    <td>
                      <strong>{r.name}</strong>
                      <small>{r.email}</small>
                    </td>
                    <td>{label(r.role)}</td>
                    <td>{r.agent_id || "—"}</td>
                    <td>
                      <Badge value={r.active ? "active" : "inactive"} />
                    </td>
                    <td>
                      {user.can_manage_users && r.id !== user.id && (
                        <button
                          className="btn secondary tiny"
                          onClick={() => {
                            setFailure("");
                            setForm(r);
                          }}
                        >
                          Manage
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <CursorControls paging={paging}/>
      {form && (
        <Modal
          title={form.id ? "Manage team member" : "Add a team member"}
          onClose={() => setForm(null)}
          closeOnBackdrop={false}
          wide
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await send(
                  "/users" + (form.id ? "/" + form.id : ""),
                  form,
                  form.id ? "PATCH" : "POST",
                );
                setForm(null);
                updated("Team member saved");
              } catch (e: any) {
                setFailure(e.message);
              }
            }}
          >
            <div className="form-grid">
              {[
                ["name", "Full name"],
                ["email", "Email"],
                ["agent_id", "Agent ID"],
                [
                  "password",
                  form.id ? "New password (optional)" : "Initial password",
                ],
              ].map(([k, l]) => (
                <Field key={k} label={l}>
                  <input
                    required={
                      !form.id && ["name", "email", "password"].includes(k)
                    }
                    type={
                      k === "password"
                        ? "password"
                        : k === "email"
                          ? "email"
                          : "text"
                    }
                    minLength={k === "password" ? 12 : undefined}
                    value={form[k] || ""}
                    onChange={(e) => setForm({ ...form, [k]: e.target.value })}
                  />
                </Field>
              ))}
              <Field label="CRM role">
                <select
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                >
                  {roles.map((r) => (
                    <option key={r} value={r}>
                      {label(r)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Reports to">
                <select
                  value={form.manager_id}
                  onChange={(e) =>
                    setForm({ ...form, manager_id: e.target.value })
                  }
                >
                  {rows(data)
                    .filter((r) => r.role !== "sales_agent" && r.id !== form.id)
                    .map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} · {label(r.role)}
                      </option>
                    ))}
                </select>
              </Field>
              {config.user?.can_view_routing && (
                <>
                  <Field label="Sales domain · private">
                    <select
                      value={form.domain_id}
                      onChange={(e) =>
                        setForm({ ...form, domain_id: e.target.value })
                      }
                    >
                      {config.domains?.map((r: Data) => (
                        <option value={r.id} key={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Project · private">
                    <select
                      value={form.project_id || ""}
                      onChange={(e) =>
                        setForm({ ...form, project_id: e.target.value })
                      }
                    >
                      <option value="">Unassigned</option>
                      {config.projects?.map((r: Data) => (
                        <option value={r.id} key={r.id}>
                          {r.id}
                        </option>
                      ))}
                    </select>
                  </Field>
                </>
              )}
            </div>
            {form.role === "supervisor" && (
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.routing_view}
                  onChange={(e) =>
                    setForm({ ...form, routing_view: e.target.checked })
                  }
                />
                Permit private routing metadata within assigned scope
              </label>
            )}
            {form.id && (
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.active}
                  onChange={(e) =>
                    setForm({ ...form, active: e.target.checked })
                  }
                />
                Account active
              </label>
            )}
            {user.role === "super_admin" && (
              <Field
                label="Existing TFSC user ID (optional)"
                hint="Creates an explicit agreements entitlement. The same CRM actor must be mapped in TFSC settings."
              >
                <input
                  value={form.agreements_user_id || ""}
                  onChange={(e) =>
                    setForm({ ...form, agreements_user_id: e.target.value })
                  }
                />
              </Field>
            )}
            {failure && <Alert>{failure}</Alert>}
            <div className="form-actions">
              <button className="btn">
                Save person
                <Check size={16} />
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
function Teams({
  rev,
  user,
  updated,
}: {
  rev: number;
  user: Data;
  updated: (s?: string) => void;
}) {
  const { data, error } = useResource("/teams", rev);
  const people = useResource("/users", rev);
  const [f, setF] = useState<Data | null>(null),
    [failure, setFailure] = useState("");
  return (
    <>
      <SectionTitle
        eyebrow="SMALL GROUPS. SHARED MOMENTUM."
        title="Teams & groups"
        description="Group leaders coordinate follow-ups. Supervisors retain qualification authority."
        actions={
          <button
            className="btn"
            onClick={() =>
              setF({
                name: "",
                supervisor_id: user.id,
                member_ids: [],
                leader_id: "",
              })
            }
          >
            <Plus size={17} />
            Create group
          </button>
        }
      />
      {error && <Alert>{error}</Alert>}
      <div className="lead-grid">
        {rows(data).map((r) => (
          <section className="panel" key={r.id}>
            <Network className="cyan" />
            <h2>{r.name}</h2>
            <p>{r.member_ids.length} assigned members</p>
            <small className="muted">
              Group leader:{" "}
              {rows(people.data).find((p) => p.id === r.leader_id)?.name ||
                "Not assigned"}
            </small>
          </section>
        ))}
      </div>
      {!rows(data).length && (
        <section className="panel">
          <Empty
            title="Bring your group together"
            detail="Create a group from people in your reporting scope."
          />
        </section>
      )}
      {f && (
        <Modal title="Create a group" onClose={() => setF(null)}>
          <Field label="Group name">
            <input
              value={f.name}
              onChange={(e) => setF({ ...f, name: e.target.value })}
            />
          </Field>
          <Field label="Supervisor">
            <select
              value={f.supervisor_id}
              onChange={(e) => setF({ ...f, supervisor_id: e.target.value })}
            >
              {rows(people.data)
                .filter((r) => r.role !== "sales_agent")
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
            </select>
          </Field>
          <div className="member-list">
            {rows(people.data)
              .filter((r) => r.role === "sales_agent")
              .map((r) => (
                <label className="checkbox" key={r.id}>
                  <input
                    type="checkbox"
                    checked={f.member_ids.includes(r.id)}
                    onChange={(e) =>
                      setF({
                        ...f,
                        member_ids: e.target.checked
                          ? [...f.member_ids, r.id]
                          : f.member_ids.filter((id: string) => id !== r.id),
                      })
                    }
                  />
                  {r.name}
                </label>
              ))}
          </div>
          <Field label="Follow-up coordinator">
            <select
              value={f.leader_id}
              onChange={(e) => setF({ ...f, leader_id: e.target.value })}
            >
              <option value="">No coordinator</option>
              {rows(people.data)
                .filter((r) => f.member_ids.includes(r.id))
                .map((r) => (
                  <option value={r.id} key={r.id}>
                    {r.name}
                  </option>
                ))}
            </select>
          </Field>
          {failure && <Alert>{failure}</Alert>}
          <div className="form-actions">
            <button
              className="btn"
              onClick={async () => {
                try {
                  await send("/teams", f);
                  setF(null);
                  updated("Group created");
                } catch (e: any) {
                  setFailure(e.message);
                }
              }}
            >
              Create group
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
function Conflicts({
  rev,
  updated,
}: {
  rev: number;
  updated: (s?: string) => void;
}) {
  const { data, error, loading } = useResource("/conflicts", rev);
  return (
    <>
      <SectionTitle
        eyebrow="PROTECT ATTRIBUTION"
        title="Conflict review"
        description="Review shared numbers and ownership before progressing overlapping opportunities."
      />
      <section className="panel">
        {error && <Alert>{error}</Alert>}
        {loading ? (
          <Loading />
        ) : rows(data).length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Severity</th>
                  <th>Created</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {rows(data).map((r) => (
                  <tr key={r.id}>
                    <td>{r.id.slice(0, 8)}</td>
                    <td>
                      <Badge value={r.severity} />
                    </td>
                    <td>{dateTime(r.created)}</td>
                    <td>
                      {r.requires_higher_review ? (
                        "Escalated: requires wider scope"
                      ) : (
                        <button
                          className="btn secondary tiny"
                          onClick={async () => {
                            const reason = prompt(
                              "Document the review and resolution reason.",
                            );
                            if (!reason) return;
                            try {
                              await send("/conflicts/" + r.id + "/resolve", {
                                reason,
                              });
                              updated("Conflict resolved");
                            } catch (e: any) {
                              alert(e.message);
                            }
                          }}
                        >
                          Resolve case
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="No open conflicts"
            detail="Potential duplicates and ownership conflicts will be routed here."
          />
        )}
      </section>
    </>
  );
}
function SettingsView({
  rev,
  user,
  updated,
}: {
  rev: number;
  user: Data;
  updated: (s?: string) => void;
}) {
  const { data, error } = useResource("/settings", rev);
  const [f, setF] = useState<Data>({}),
    [failure, setFailure] = useState(""),
    [catalog, setCatalog] = useState(""),
    [entry, setEntry] = useState<Data>({});
  useEffect(() => {
    if (data) setF(data);
  }, [data]);
  return (
    <>
      <SectionTitle
        eyebrow="WORKSPACE CONTROLS"
        title="Settings"
        description="Configure targets and private routing. Changes are recorded in the audit trail."
      />
      {error && <Alert>{error}</Alert>}
      <div className="settings-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>Activity policy</h2>
            <ShieldCheck size={20} />
          </div>
          <Field label="Daily call target">
            <input
              type="number"
              min="1"
              max="1000"
              value={f.call_target || 170}
              onChange={(e) =>
                setF({ ...f, call_target: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Connection target (0.01 to 1)">
            <input
              type="number"
              min="0.01"
              max="1"
              step="0.01"
              value={f.connection_target || 0.6}
              onChange={(e) =>
                setF({ ...f, connection_target: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Imported-record edit window (minutes)">
            <input
              type="number"
              min="10"
              max="20"
              value={f.import_edit_minutes || 15}
              onChange={(e) =>
                setF({ ...f, import_edit_minutes: Number(e.target.value) })
              }
            />
          </Field>
          <p className="muted">
            Manual outcomes have a 60-minute edit window. The original deadline
            never resets.
          </p>
          {failure && <Alert>{failure}</Alert>}
          <button
            className="btn"
            onClick={async () => {
              try {
                await send("/settings", f, "PATCH");
                updated("Policy saved");
              } catch (e: any) {
                setFailure(e.message);
              }
            }}
          >
            Save policy
          </button>
        </section>
        <section className="panel">
          <h2>Integration setup</h2>
          <p className="muted">
            Provider connections are configured on the server. Unconfigured
            adapters do not send messages or invitations.
          </p>
          <p>Google Calendar: account connection required</p>
          <p>Vonage: system telephone handler</p>
          <p>TFSC: explicit account mapping and a shared bridge secret</p>
          {user.role === "super_admin" && (
            <>
              <Field label="Link your TFSC account ID">
                <input
                  placeholder="Existing TFSC user ID"
                  value={f.link_id || ""}
                  onChange={(e) => setF({ ...f, link_id: e.target.value })}
                />
              </Field>
              <button
                className="btn secondary"
                onClick={async () => {
                  try {
                    await send("/settings/agreements-link", {
                      tfsc_user_id: f.link_id,
                    });
                    updated(
                      "TFSC entitlement linked. Configure the matching actor mapping in TFSC.",
                    );
                  } catch (e: any) {
                    setFailure(e.message);
                  }
                }}
              >
                Link account
              </button>
              <small className="muted">Your CRM actor ID: {user.id}</small>
            </>
          )}
        </section>
      </div>
      <div className="settings-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>Sales domains</h2>
            <button
              className="btn secondary tiny"
              onClick={() => {
                setCatalog("domains");
                setEntry({ id: "", name: "" });
              }}
            >
              Add domain
            </button>
          </div>
          {f.domains?.map((r: Data) => (
            <div className="catalog-row" key={r.id}>
              <span>{r.name}</span>
              <Badge value={r.active ? "active" : "inactive"} />
            </div>
          ))}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>Projects</h2>
            <button
              className="btn secondary tiny"
              onClick={() => {
                setCatalog("projects");
                setEntry({
                  id: "",
                  owner_name: "",
                  owner_email: "",
                  domain_id: "",
                });
              }}
            >
              Add project
            </button>
          </div>
          <div className="project-list">
            {f.projects?.map((r: Data) => (
              <button
                className="catalog-row text-btn"
                key={r.id}
                onClick={() => {
                  setCatalog("projects");
                  setEntry(r);
                }}
              >
                <strong>{r.id}</strong>
                <span>{r.owner_name || "Owner pending"}</span>
                <ChevronRight size={14} />
              </button>
            ))}
          </div>
        </section>
      </div>
      {catalog && (
        <Modal
          title={
            catalog === "domains" ? "Add sales domain" : "Configure project"
          }
          onClose={() => setCatalog("")}
        >
          <Field
            label={catalog === "domains" ? "Stable domain ID" : "Project ID"}
          >
            <input
              value={entry.id || ""}
              onChange={(e) => setEntry({ ...entry, id: e.target.value })}
            />
          </Field>
          {catalog === "domains" ? (
            <Field label="Domain name">
              <input
                value={entry.name || ""}
                onChange={(e) => setEntry({ ...entry, name: e.target.value })}
              />
            </Field>
          ) : (
            <>
              <Field label="Project owner">
                <input
                  value={entry.owner_name || ""}
                  onChange={(e) =>
                    setEntry({ ...entry, owner_name: e.target.value })
                  }
                />
              </Field>
              <Field label="Owner email">
                <input
                  type="email"
                  value={entry.owner_email || ""}
                  onChange={(e) =>
                    setEntry({ ...entry, owner_email: e.target.value })
                  }
                />
              </Field>
              <Field label="Associated sales domain">
                <select
                  value={entry.domain_id || ""}
                  onChange={(e) =>
                    setEntry({ ...entry, domain_id: e.target.value })
                  }
                >
                  <option value="">Pending assignment</option>
                  {f.domains?.map((r: Data) => (
                    <option value={r.id} key={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </Field>
            </>
          )}
          {failure && <Alert>{failure}</Alert>}
          <div className="form-actions">
            <button
              className="btn"
              onClick={async () => {
                try {
                  await send("/settings/" + catalog, entry);
                  setCatalog("");
                  updated("Catalog saved");
                } catch (e: any) {
                  setFailure(e.message);
                }
              }}
            >
              Save
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
