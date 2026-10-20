import { createContext, useContext, useEffect, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { call, client, setCsrf, type Schemas } from './api';
import { setServerOffset } from './time';

export type Me = Schemas['UserMe'];
type Ctx = { me: Me | null; loading: boolean; setSession: (s: { user: Me; csrf: string }) => void; logout: () => Promise<void>; can: (cap: string) => boolean };
const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  useEffect(() => {
    const t0 = Date.now();
    fetch('/api/time').then((r) => r.json() as Promise<{ server_time: string }>).then((t) => setServerOffset(t.server_time, t0, Date.now())).catch(() => undefined);
    const onUnauthorized = () => { qc.clear(); qc.setQueryData(['me'], null); };
    window.addEventListener('crm:unauthorized', onUnauthorized);
    return () => window.removeEventListener('crm:unauthorized', onUnauthorized);
  }, [qc]);
  // `me` is derived from the query itself (not copied into state by an effect) so the gate never
  // sees a "loaded but signed out" frame and bounces a deep link through /login.
  const q = useQuery({
    queryKey: ['me'], retry: false, staleTime: 60_000,
    queryFn: async () => {
      const r = await client.GET('/api/auth/me');
      if (r.response.status === 401) return null;
      const s = await call(Promise.resolve(r));
      setCsrf(s.csrf);
      return s;
    },
  });
  const me: Me | null = q.data?.user ?? null;
  const value: Ctx = {
    me, loading: q.isLoading,
    setSession: ({ user, csrf }) => {
      setCsrf(csrf);
      qc.setQueryData(['me'], { user, csrf });
    },
    logout: async () => {
      await call(client.POST('/api/auth/logout')).catch(() => undefined);
      try { indexedDB.deleteDatabase('crm-drafts'); } catch { /* ignore */ }
      qc.clear(); location.assign('/login');
    },
    can: (cap) => !!me?.capabilities.includes(cap),
  };
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  const v = useContext(AuthCtx);
  if (!v) throw new Error('AuthProvider missing');
  return v;
}

export const RANK: Record<string, number> = { none: -1, sales_agent: 0, supervisor: 1, manager: 2, director_admin: 3, super_admin: 4 };
export const rankOf = (me: Me | null) => (me ? RANK[me.role] ?? -1 : -1);
