// Browser error reporting (§4.2): the Sentry SDK loads only when the server reports a web DSN (CRM_SENTRY_DSN_WEB),
// and no personal data leaves the browser: no cookies, headers, bodies, query strings, user or console output.
import type { ErrorEvent } from '@sentry/browser';

export function scrub(e: ErrorEvent): ErrorEvent {
  if (e.request) {
    delete e.request.cookies;
    delete e.request.headers;
    delete e.request.data;
    delete e.request.query_string;
    if (e.request.url) e.request.url = e.request.url.split('?')[0];
  }
  delete e.user;
  return e;
}

export async function startErrorReporting(): Promise<void> {
  try {
    const status = (await (await fetch('/api/auth/status')).json()) as { sentry_dsn?: string | null };
    if (!status.sentry_dsn) return;
    const Sentry = await import('@sentry/browser');
    Sentry.init({
      dsn: status.sentry_dsn,
      dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false },
      tracesSampleRate: 0,
      beforeSend: scrub,
      beforeBreadcrumb: (b) => (b.category === 'console' || b.category === 'ui.input' ? null : b),
    });
  } catch {
    /* error reporting is optional */
  }
}
