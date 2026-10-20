import { useEffect, useState, useRef } from "react";
import type { ReactNode } from "react";
import {
  X,
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Search,
  Loader2,
  Clock,
  ArrowUpRight,
  Inbox,
} from "lucide-react";
import { api, dateTime, label, rows } from "./api";
import type { Data } from "./api";
export function useResource(path: string, revision = 0) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    api(path)
      .then((d) => {
        if (live) setData(d);
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [path, revision]);
  return { data, error, loading };
}
export function usePagedResource(path:string,revision=0){
  const [state,setState]=useState<{path:string,stack:string[]}>({path,stack:['']});
  const stack=state.path===path?state.stack:[''];
  const cursor=stack[stack.length-1];
  const resource=useResource(path+(path.includes('?')?'&':'?')+'cursor='+encodeURIComponent(cursor),revision);
  return {...resource,page:stack.length,next:()=>{if(resource.data?.next_cursor)setState({path,stack:[...stack,resource.data.next_cursor]});},previous:()=>setState({path,stack:stack.slice(0,-1)}),hasPrevious:stack.length>1};
}
export function CursorControls({paging}:{paging:ReturnType<typeof usePagedResource>}){return <div className="pagination"><span>Page {paging.page} · {rows(paging.data).length} records</span><div><button className="btn secondary tiny" disabled={!paging.hasPrevious||paging.loading} onClick={paging.previous}><ChevronLeft size={15}/>Previous</button><button className="btn secondary tiny" disabled={!paging.data?.next_cursor||paging.loading} onClick={paging.next}>Next<ChevronRight size={15}/></button></div></div>;}
export function Alert({
  children,
  success = false,
}: {
  children: ReactNode;
  success?: boolean;
}) {
  return (
    <div
      className={`alert ${success ? "success" : ""}`}
      role={success ? "status" : "alert"}
    >
      {success ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
      <span>{children}</span>
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading">
      <Loader2 className="spin" size={22} /> Loading your workspace…
    </div>
  );
}
export function Empty({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Inbox size={27} />
      </div>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </div>
  );
}
export function Badge({ value, tone }: { value: any; tone?: string }) {
  const state = String(value || "research");
  const color =
    tone ||
    (/sold|sign|qualif|active|approved|connected|success/.test(state)
      ? "green"
      : /conflict|locked|lost|error|block/.test(state)
        ? "red"
        : /strong|pending|follow|review/.test(state)
          ? "amber"
          : "blue");
  return <span className={`badge ${color}`}>{label(state)}</span>;
}
export function Field({
  label: caption,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{caption}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function TriState({
  value,
  onChange,
  disabled = false,
  label: caption = "Outcome",
}: {
  value: any;
  onChange: (v: boolean | null) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <div className="tristate" role="group" aria-label={caption}>
      {[
        [null, "—"],
        [true, "Yes"],
        [false, "No"],
      ].map(([v, t]) => (
        <button
          type="button"
          key={String(t)}
          aria-pressed={value === v}
          disabled={disabled}
          className={
            value === v
              ? `selected ${v === true ? "yes" : v === false ? "no" : ""}`
              : ""
          }
          onClick={() => onChange(v as boolean | null)}
        >
          {String(t)}
        </button>
      ))}
    </div>
  );
}
export function Modal({
  title,
  subtitle,
  children,
  onClose,
  wide = false,
  closeOnBackdrop = true,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  closeOnBackdrop?: boolean;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const original = document.body.style.overflow;
    const previousFocus = document.activeElement as HTMLElement | null;
    document.body.style.overflow = "hidden";
    const focusables = () => Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex="0"]'
    ) || []).filter(element => element.getClientRects().length > 0);
    const frame = requestAnimationFrame(() => {
      const input = dialogRef.current?.querySelector<HTMLElement>('input:not([type="hidden"]),select,textarea');
      (input || focusables()[0] || dialogRef.current)?.focus();
    });
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {e.preventDefault(); closeRef.current();}
      if (e.key === 'Tab') {
        const fields = focusables();
        const first = fields[0], last = fields[fields.length - 1];
        if (!first) {e.preventDefault(); dialogRef.current?.focus();}
        else if (e.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {e.preventDefault(); last.focus();}
        else if (!e.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) {e.preventDefault(); first.focus();}
      }
    };
    window.addEventListener("keydown", key);
    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = original;
      window.removeEventListener("keydown", key);
      previousFocus?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        tabIndex={-1}
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            className="icon-btn"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}
export function SearchBox({
  value,
  onChange,
  placeholder = "Search records…",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="search-box">
      <Search size={17} />
      <input
        aria-label={placeholder}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
export function SectionTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="section-title">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
export function Pagination({
  page,
  onPage,
  count,
  total,
}: {
  page: number;
  onPage: (p: number) => void;
  count: number;
  total?: number;
}) {
  return (
    <div className="pagination">
      <span>
        {total != null
          ? `${total.toLocaleString()} records`
          : `${count} records on this page`}{" "}
        · Page {page + 1}
      </span>
      <div>
        <button
          className="icon-btn"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
          aria-label="Previous page"
        >
          <ChevronLeft size={18} />
        </button>
        <button
          className="icon-btn"
          disabled={count < 50 || (total != null && (page + 1) * 50 >= total)}
          onClick={() => onPage(page + 1)}
          aria-label="Next page"
        >
          <ChevronRight size={18} />
        </button>
      </div>
    </div>
  );
}
export function DetailRows({
  record,
  keys,
}: {
  record: Data;
  keys: [string, string][];
}) {
  return (
    <dl className="detail-rows">
      {keys.map(([k, caption]) => (
        <div key={k}>
          <dt>{caption}</dt>
          <dd>
            {record[k] == null
              ? "—"
              : typeof record[k] === "boolean"
                ? record[k]
                  ? "Yes"
                  : "No"
                : String(record[k])}
          </dd>
        </div>
      ))}
    </dl>
  );
}
export function AuditList({
  path = "/audit",
  revision = 0,
}: {
  path?: string;
  revision?: number;
}) {
  const paging=usePagedResource(path,revision);
  const { data, loading, error } = paging;
  return loading ? (
    <Loading />
  ) : error ? (
    <Alert>{error}</Alert>
  ) : rows(data).length ? (
    <><div className="timeline">
      {rows(data).map((r, i) => (
        <div className="timeline-row" key={r.id || i}>
          <span className="timeline-dot" />
          <div>
            <strong>{label(r.action || r.event)}</strong>
            <p>
              {r.actor_name || r.user_name || "System"}
              {r.reason && ` · ${r.reason}`}
            </p>
            <small>{dateTime(r.created || r.created_at || r.timestamp)} Pacific</small>
          </div>
        </div>
      ))}
    </div><CursorControls paging={paging}/></>
  ) : (
    <Empty
      title="No activity yet"
      detail="Recorded changes and decisions will appear here."
    />
  );
}
const clockZones = [
  ["Pacific", "America/Los_Angeles"],
  ["Mountain", "America/Denver"],
  ["Arizona", "America/Phoenix"],
  ["Central", "America/Chicago"],
  ["Eastern", "America/New_York"],
  ["Alaska", "America/Anchorage"],
  ["Hawaii", "Pacific/Honolulu"],
  ["Pakistan", "Asia/Karachi"],
];
function zoneMinutes(time: Date, zone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    timeZoneName: "longOffset",
  }).formatToParts(time);
  const offset = parts.find((p) => p.type === "timeZoneName")?.value || "GMT";
  const m = offset.match(/GMT([+-])(\d+)(?::(\d+))?/);
  return m
    ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3] || 0))
    : 0;
}
export function WorldClocks({ compact = false }: { compact?: boolean }) {
  const [now, setNow] = useState(new Date());
  const [skew, setSkew] = useState(0);
  useEffect(() => {
    api("/clocks")
      .then((r) => {
        const ts = r.server_time || r.now || r.utc;
        if (ts) setSkew(new Date(ts).valueOf() - Date.now());
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    setNow(new Date(Date.now() + skew));
    const t = window.setInterval(
      () => setNow(new Date(Date.now() + skew)),
      1000,
    );
    return () => clearInterval(t);
  }, [skew]);
  if (compact)
    return (
      <div className="clock-compact">
        <Clock size={15} />
        <span>
          {new Intl.DateTimeFormat("en-US", {
            timeZone: "America/Los_Angeles",
            hour: "2-digit",
            minute: "2-digit",
            hour12: true,
          }).format(now)}{" "}
          <b>Pacific</b>
        </span>
      </div>
    );
  return (
    <div className="world-clocks">
      {clockZones.map(([name, zone]) => {
        const diff =
          (zoneMinutes(now, "Asia/Karachi") - zoneMinutes(now, zone)) / 60;
        return (
          <div key={zone}>
            <div>
              <span>{name}</span>
              <small>
                {new Intl.DateTimeFormat("en-US", {
                  timeZone: zone,
                  month: "short",
                  day: "numeric",
                  timeZoneName: "short",
                }).format(now)}
              </small>
            </div>
            <strong>
              {new Intl.DateTimeFormat("en-US", {
                timeZone: zone,
                hour: "2-digit",
                minute: "2-digit",
                hour12: true,
              }).format(now)}
            </strong>
            <small>
              {diff === 0
                ? "Reference time"
                : `PK ${diff > 0 ? "+" : ""}${diff} hrs`}
            </small>
          </div>
        );
      })}
      <p className="muted">
        Live timezone offsets adjust with daylight saving time.
      </p>
    </div>
  );
}
export function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a
      className="btn secondary"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
      <ArrowUpRight size={16} />
    </a>
  );
}
