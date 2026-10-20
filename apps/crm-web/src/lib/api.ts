/** Typed API client generated from the CRM OpenAPI contract (openapi-typescript + openapi-fetch). */
import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './schema';

let csrf = '';
export const setCsrf = (v: string) => { csrf = v; };

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) { super(message); }
}

const middleware: Middleware = {
  onRequest({ request }) {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) request.headers.set('X-CSRF-Token', csrf);
    return request;
  },
};

export const client = createClient<paths>({ baseUrl: '', credentials: 'same-origin' });
client.use(middleware);

/** Unwrap an openapi-fetch result or throw ApiError with the server's message. */
export async function call<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (response.status === 401 && !location.pathname.startsWith('/login')) window.dispatchEvent(new CustomEvent('crm:unauthorized'));
  if (error !== undefined || !response.ok) {
    const e = error as { error?: string | { message?: string }[] } | undefined;
    const msg = typeof e?.error === 'string' ? e.error : `Request failed (${response.status})`;
    throw new ApiError(response.status, msg, error);
  }
  return data as T;
}

/** Plain JSON helper for endpoints outside the typed client (e.g. multipart). */
export async function raw<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== 'GET') headers.set('X-CSRF-Token', csrf);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const r = await fetch(path, { ...init, headers, credentials: 'same-origin' });
  const body: unknown = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, (body as { error?: string }).error ?? `Request failed (${r.status})`, body);
  return body as T;
}

export type Schemas = import('./schema').components['schemas'];
