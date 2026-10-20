import { Navigate, createRootRoute, createRoute, createRouter, Outlet, useRouterState, type RouteComponent } from '@tanstack/react-router';
import { lazy, Suspense, type ComponentType } from 'react';
import { AppShell } from '@/components/layout/AppShell';
import { Skeleton } from '@/components/ui/bits';
import { useAuth } from '@/lib/auth';
import { Login } from './routes/Login';

const page = (load: () => Promise<{ default: ComponentType }>) => {
  const C = lazy(load);
  return function Page() {
    return <Suspense fallback={<div className="space-y-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-40 w-full" /><Skeleton className="h-64 w-full" /></div>}><C /></Suspense>;
  };
};

function Gate() {
  const { me, loading } = useAuth();
  const path = useRouterState({ select: (s) => s.location.pathname });
  if (loading) return <div className="p-8"><Skeleton className="h-12 w-64" /></div>;
  if (!me) return <Navigate to="/login" />;
  if (me.reset_required && path !== '/me') return <Navigate to="/me" />;
  return <AppShell />;
}

const root = createRootRoute({ component: () => <Outlet /> });
const login = createRoute({ getParentRoute: () => root, path: '/login', component: Login });
const app = createRoute({ getParentRoute: () => root, id: 'app', component: Gate });
const r = <P extends string>(path: P, component: RouteComponent) => createRoute({ getParentRoute: () => app, path, component });

const routeTree = root.addChildren([
  login,
  app.addChildren([
    r('/', page(() => import('./routes/Overview'))),
    r('/my-work', page(() => import('./routes/MyWork'))),
    r('/sheet', page(() => import('./routes/Sheet'))),
    r('/followups', page(() => import('./routes/Followups'))),
    r('/strong', page(() => import('./routes/Strong'))),
    r('/leads', page(() => import('./routes/Leads'))),
    r('/meetings', page(() => import('./routes/Meetings'))),
    r('/wall-of-fame', page(() => import('./routes/WallOfFame'))),
    r('/clients', page(() => import('./routes/Clients'))),
    r('/flags', page(() => import('./routes/Flags'))),
    r('/people', page(() => import('./routes/People'))),
    r('/reports', page(() => import('./routes/Reports'))),
    r('/settings', page(() => import('./routes/Settings'))),
    r('/security', page(() => import('./routes/Security'))),
    r('/me', page(() => import('./routes/Me'))),
  ]),
]);

export const router = createRouter({ routeTree, defaultPreload: 'intent' });
declare module '@tanstack/react-router' { interface Register { router: typeof router } }
