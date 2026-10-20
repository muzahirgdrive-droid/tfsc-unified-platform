import { useQuery } from '@tanstack/react-query';
import { Navigate } from '@tanstack/react-router';
import { useState, type FormEvent } from 'react';
import { call, client } from '@/lib/api';
import { useAuth } from '@/lib/auth';

export function Login() {
  const { me, setSession } = useAuth();
  const status = useQuery({ queryKey: ['auth-status'], queryFn: () => call(client.GET('/api/auth/status')) });
  const [form, setForm] = useState({ email: '', password: '', name: '', setup_key: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [breakGlass, setBreakGlass] = useState(false);
  const ssoError = SSO_ERRORS[new URLSearchParams(location.search).get('sso_error') ?? ''] ?? null;
  if (me) return <Navigate to="/" />;
  const sso = !!status.data?.oidc && !status.data?.setup_required;
  const showPassword = !sso || breakGlass;
  const setup = status.data?.setup_required;
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError('');
    try {
      const s = setup
        ? await call(client.POST('/api/auth/setup', { body: { setup_key: form.setup_key, name: form.name, email: form.email, password: form.password } }))
        : await call(client.POST('/api/auth/login', { body: { email: form.email, password: form.password } }));
      setSession(s);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  const field = (k: keyof typeof form, label: string, type = 'text', auto?: string) => (
    <div><label className="label" htmlFor={k}>{label}</label>
      <input id={k} className="input h-11" type={type} autoComplete={auto} required value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></div>
  );
  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      <div className="relative hidden overflow-hidden bg-gradient-to-br from-navy via-navy-2 to-royal p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="w-fit rounded-2xl bg-white p-3"><img src="/assets/logo.png" alt="Twenty Four Seven Consultancy" className="h-10" /></div>
        <div>
          <div className="eyebrow mb-3 text-cyan">TFSC Sales CRM</div>
          <h1 className="text-5xl font-bold leading-tight">Every conversation,<br />counted and protected.</h1>
          <p className="mt-4 max-w-md text-white/70">From the first call to a signed client — one secure workspace for the whole team, on the Pacific business clock.</p>
        </div>
        <div className="absolute -right-24 -top-24 h-96 w-96 rounded-full bg-cyan/20 blur-3xl" aria-hidden />
        <p className="text-xs text-white/50">Authorized users only. Activity is audited.</p>
      </div>
      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="card w-full max-w-md space-y-4 p-8">
          <div><h2 className="text-2xl font-bold">{setup ? 'Create the first Super Admin' : 'Sign in'}</h2>
            <p className="mt-1 text-sm text-muted">{setup ? 'Use the one-time setup key from the server data folder.' : sso ? 'Use your company account. Supervisors and above confirm with a second factor.' : 'Use your work email and password.'}</p></div>
          {ssoError && <p role="alert" className="rounded-lg bg-danger-bg px-3 py-2 text-sm text-danger">{ssoError}</p>}
          {sso && <a href="/api/auth/oidc/start" className="btn-primary h-11 w-full">Sign in with company SSO</a>}
          {sso && !breakGlass && <button type="button" className="w-full text-center text-xs text-muted underline-offset-2 hover:underline" onClick={() => setBreakGlass(true)}>Emergency sign-in with a password</button>}
          {showPassword && <>
            {setup && field('setup_key', 'Setup key', 'password', 'off')}
            {setup && field('name', 'Full name', 'text', 'name')}
            {field('email', 'Email', 'email', 'username')}
            {field('password', 'Password', 'password', setup ? 'new-password' : 'current-password')}
            {error && <p role="alert" className="rounded-lg bg-danger-bg px-3 py-2 text-sm text-danger">{error}</p>}
            <button className={sso ? 'btn-secondary h-11 w-full' : 'btn-primary h-11 w-full'} disabled={busy}>{busy ? 'Please wait…' : setup ? 'Create account' : 'Sign in'}</button>
          </>}
        </form>
      </div>
    </div>
  );
}

const SSO_ERRORS: Record<string, string> = {
  cancelled: 'Sign-in was cancelled.',
  state: 'This sign-in link expired or was opened in another browser. Try again.',
  expired: 'This sign-in link expired. Try again.',
  token: 'Your sign-in could not be verified. Try again or contact an administrator.',
  unknown_user: 'Your company account is not set up in the CRM yet. Ask your manager to add you.',
  identity_mismatch: 'This CRM account is linked to a different company login. Contact an administrator.',
  inactive: 'Your CRM access is suspended. Contact your manager.',
  mfa_required: 'Your role needs two-factor sign-in. Set up an authenticator in your company account, then try again.',
  unavailable: 'Single sign-on is unavailable right now. Try again shortly.',
};
