export type Data = Record<string, any>;
let token = "";
export function setCsrf(value: string) {
  token = value || "";
}
export class ApiError extends Error {
  status: number;
  details: any;
  constructor(message: string, status: number, details?: any) {
    super(message);
    this.status = status;
    this.details = details;
  }
}
export async function api(
  path: string,
  options: RequestInit = {},
): Promise<any> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (options.body && !(options.body instanceof FormData))
    headers["Content-Type"] = "application/json";
  if (options.method && !["GET", "HEAD"].includes(options.method))
    headers["X-CSRF-Token"] = token;
  const response = await fetch("/api" + path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  const payload =
    response.status === 204
      ? {}
      : await response
          .json()
          .catch(() => ({
            detail: "The server returned an unreadable response.",
          }));
  if (!response.ok) {
    const detail = payload.error || payload.detail || payload.message;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: any) => d.msg || String(d)).join("; ")
          : "This request could not be completed.";
    throw new ApiError(message, response.status, payload);
  }
  if (payload.csrf) setCsrf(payload.csrf);
  return payload;
}
export const send = (path: string, body: any, method = "POST") =>
  api(path, { method, body: JSON.stringify(body) });
export const rows = (payload: any): Data[] =>
  Array.isArray(payload)
    ? payload
    : payload?.items ||
      payload?.activities ||
      payload?.leads ||
      payload?.clients ||
      payload?.users ||
      payload?.teams ||
      payload?.events ||
      [];
export const label = (value: any) =>
  String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
export const nameOf = (value: Data) =>
  value?.provider_name ||
  value?.name ||
  value?.practice_name ||
  value?.client_name ||
  value?.display_name ||
  "Unnamed record";
export const userName = (value: Data) =>
  value?.name || value?.display_name || value?.email || "Team member";
export const initials = (name: string) =>
  name
    .split(/\s+/)
    .slice(0, 2)
    .map((n) => n[0])
    .join("")
    .toUpperCase();
export const num = (n: any) => Number(n || 0).toLocaleString("en-US");
export const pct = (n: any) =>
  n == null
    ? "—"
    : `${(Number(n) > 1 ? Number(n) : Number(n) * 100).toFixed(1)}%`;
export const pacificDate = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
export function dateTime(value: any) {
  if (!value) return "—";
  const d = new Date(typeof value === "number" ? value * 1000 : value);
  return isNaN(d.valueOf())
    ? String(value)
    : new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Los_Angeles",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(d);
}
export function parseSheet(text: string): Data[] {
  const delimiter = text.includes("\t") ? "\t" : ",";
  const lines: string[][] = [];
  let row: string[] = [],
    cell = "",
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = !quoted;
    } else if (c === delimiter && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((c === "\n" || c === "\r") && !quoted) {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      if (row.some((v) => v.trim())) lines.push(row);
      row = [];
      cell = "";
    } else cell += c;
  }
  row.push(cell);
  if (row.some((v) => v.trim())) lines.push(row);
  if (!lines.length) return [];
  const aliases: Record<string, string> = {
    date: "business_date",
    "provider name": "provider_name",
    "practice name": "provider_name",
    name: "provider_name",
    phone: "phone",
    "phone number": "phone",
    email: "email",
    "call attempt": "called",
    called: "called",
    connected: "connected",
    "text sent": "text_sent",
    "email sent": "email_sent",
    "strong follow-up": "strong_followup_requested",
    "sign up": "sale_requested",
    "# follow-ups": "historical_followup_count",
    "last follow-up": "last_followup",
    "next follow-up": "next_followup",
    "call again?": "call_again",
    remarks: "remarks",
    notes: "remarks",
  };
  const header = lines[0].map((v) => aliases[v.trim().toLowerCase()]);
  const hasHeader = header.filter(Boolean).length >= 2;
  const cols = hasHeader
    ? header
    : [
        "business_date",
        "provider_name",
        "phone",
        "email",
        "called",
        "connected",
        "text_sent",
        "email_sent",
        "strong_followup_requested",
        "sale_requested",
        "historical_followup_count",
        "last_followup",
        "next_followup",
        "call_again",
        "remarks",
      ];
  return lines.slice(hasHeader ? 1 : 0).map((line) => {
    const item: Data = {};
    line.forEach((v, i) => {
      const k = cols[i];
      if (!k) return;
      const value = v.trim();
      if (
        [
          "called",
          "connected",
          "text_sent",
          "email_sent",
          "strong_followup_requested",
          "sale_requested",
          "call_again",
        ].includes(k)
      ) {
        item[k] =
          value === ""
            ? null
            : /^(yes|true|1)$/i.test(value)
              ? true
              : /^(no|false|0)$/i.test(value)
                ? false
                : value;
      } else item[k] = value;
    });
    return item;
  });
}
