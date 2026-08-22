'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/app/providers';
import { AuthShell, Field, FormError, SubmitButton } from '@/components/auth/AuthShell';
import { GoogleButton, googleSignInAvailable, OrDivider } from '@/components/auth/GoogleButton';
import { ApiError, getSession, login } from '@/lib/api';

const DEMO_EMAIL = 'demo@docflow.ai';
const DEMO_PASSWORD = 'demo-workspace-2026';

export default function LoginPage() {
  const router = useRouter();
  const { setSession } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);

  async function signIn(withEmail: string, withPassword: string) {
    setBusy(true);
    setError(null);
    try {
      await login(withEmail, withPassword);
      setSession(await getSession());
      router.push('/documents');
    } catch (err) {
      // Errors explain what went wrong and what to do; no apologies, no
      // "Something went wrong."
      setError(
        err instanceof ApiError
          ? err.problem.detail
          : 'Could not reach the server. Is the API running?',
      );
      setBusy(false);
    }
  }

  const anyBusy = busy || googleBusy;

  return (
    <AuthShell
      title="Sign in to DocFlow"
      subtitle="Turn documents into deadlines, tasks and actions."
      footer={
        <>
          New here?{' '}
          <Link href="/signup" className="font-medium text-accent hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      {googleSignInAvailable && (
        <>
          <GoogleButton
            label="Continue with Google"
            disabled={busy}
            onError={setError}
            onBusyChange={setGoogleBusy}
          />
          <OrDivider />
        </>
      )}

      <form
        className={cn('flex flex-col gap-3', !googleSignInAvailable && 'mt-8')}
        onSubmit={(event) => {
          event.preventDefault();
          void signIn(email, password);
        }}
      >
        <Field
          label="Email"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <Field
          label="Password"
          type="password"
          required
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          action={
            <Link
              href="/forgot-password"
              className="text-[11.5px] text-ink-3 transition-colors hover:text-accent"
            >
              Forgot password?
            </Link>
          }
        />

        <FormError message={error} />

        <SubmitButton busy={anyBusy}>{busy ? 'Signing in…' : 'Sign in'}</SubmitButton>
      </form>

      <div className="mt-7 rounded-[--radius-card] border border-line bg-raised p-3.5">
        <p className="text-[12.5px] font-medium">Try the demo workspace</p>
        <p className="mt-1 text-[11.5px] text-ink-2">
          Three processed circulars with deadlines, requirements and tasks.
        </p>
        <button
          type="button"
          disabled={anyBusy}
          onClick={() => void signIn(DEMO_EMAIL, DEMO_PASSWORD)}
          className="mt-2.5 h-10 w-full rounded-[--radius-inner] border border-line-strong bg-surface text-[13px] font-medium transition-all duration-200 hover:border-accent hover:text-accent active:scale-[0.99] disabled:opacity-50 sm:h-9"
        >
          Open the demo
        </button>
      </div>
    </AuthShell>
  );
}
