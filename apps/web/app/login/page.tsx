'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/app/providers';
import { ApiError, getSession, login, loginWithGoogle } from '@/lib/api';
import { clearFirebaseSession, googleSignInAvailable, signInWithGoogle } from '@/lib/firebase';

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

  async function continueWithGoogle() {
    setGoogleBusy(true);
    setError(null);
    try {
      const idToken = await signInWithGoogle();
      await loginWithGoogle(idToken);
      // Firebase's own session is not used once we hold a DocFlow token.
      void clearFirebaseSession();
      setSession(await getSession());
      router.push('/documents');
    } catch (err) {
      const code = (err as { code?: string })?.code;
      if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') {
        setGoogleBusy(false);
        return; // The user changed their mind; that is not an error.
      }
      setError(
        err instanceof ApiError
          ? err.problem.detail
          : 'Google sign-in did not complete. Try again, or use your email.',
      );
      setGoogleBusy(false);
    }
  }

  const anyBusy = busy || googleBusy;

  return (
    <main id="main" className="grid min-h-dvh lg:grid-cols-2">
      <div className="flex items-center justify-center px-5 py-12 sm:px-6 sm:py-16">
        <div className="w-full max-w-sm">
          <h1 className="text-[21px] font-semibold tracking-[-0.02em]">Sign in to DocFlow</h1>
          <p className="mt-1.5 text-[13.5px] text-ink-2">
            Turn documents into deadlines, tasks and actions.
          </p>

          {googleSignInAvailable && (
            <>
              <button
                type="button"
                onClick={() => void continueWithGoogle()}
                disabled={anyBusy}
                className="mt-7 flex h-10 w-full items-center justify-center gap-2.5 rounded-[--radius-inner] border border-line-strong bg-surface text-[13.5px] font-medium transition-all duration-200 hover:border-ink-4 hover:bg-raised active:scale-[0.99] disabled:opacity-50"
              >
                <GoogleMark />
                {googleBusy ? 'Opening Google…' : 'Continue with Google'}
              </button>

              <div className="my-5 flex items-center gap-3">
                <span className="h-px flex-1 bg-line" />
                <span className="font-mono text-[11px] text-ink-4">or</span>
                <span className="h-px flex-1 bg-line" />
              </div>
            </>
          )}

          <form
            className={cn('flex flex-col gap-3', !googleSignInAvailable && 'mt-8')}
            onSubmit={(event) => {
              event.preventDefault();
              void signIn(email, password);
            }}
          >
            <label className="flex flex-col gap-1.5">
              <span className="text-[12px] font-medium text-ink-2">Email</span>
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="h-11 rounded-[--radius-inner] border border-line bg-surface px-3 text-[16px] outline-none transition-colors duration-200 focus:border-accent sm:h-9 sm:text-[13.5px]"
              />
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="text-[12px] font-medium text-ink-2">Password</span>
              <input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-11 rounded-[--radius-inner] border border-line bg-surface px-3 text-[16px] outline-none transition-colors duration-200 focus:border-accent sm:h-9 sm:text-[13.5px]"
              />
            </label>

            {error && (
              <p role="alert" className="text-[12px] text-crit">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={anyBusy}
              className="mt-1 h-11 rounded-[--radius-inner] bg-accent text-[14px] font-medium text-white shadow-[var(--shadow-raise)] transition-all duration-200 hover:bg-accent-hover active:scale-[0.98] disabled:opacity-50 sm:h-10"
            >
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <div className="mt-7 rounded-[--radius-card] border border-line bg-raised p-3.5">
            <p className="text-[12.5px] font-medium">Try the demo workspace</p>
            <p className="mt-1 text-[11.5px] text-ink-2">
              Three processed circulars with deadlines, requirements and tasks.
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void signIn(DEMO_EMAIL, DEMO_PASSWORD)}
              className="mt-2.5 h-10 w-full rounded-[--radius-inner] border border-line-strong bg-surface text-[13px] font-medium transition-all duration-200 hover:border-accent hover:text-accent active:scale-[0.99] disabled:opacity-50 sm:h-9"
            >
              Open the demo
            </button>
          </div>
        </div>
      </div>

      {/* Right panel states the thesis rather than decorating. */}
      <div className="hidden border-l border-line bg-raised lg:flex lg:items-center lg:justify-center lg:p-16">
        <div className="max-w-sm">
          <p className="eyebrow text-accent">
            The provenance chain
          </p>
          <p className="mt-4 max-w-[46ch] text-[15px] leading-relaxed">
            Every extracted fact carries a verified quote. If the quote cannot be
            matched against the source text, the fact is not shown as confirmed.
          </p>
          <ol className="mt-7 flex flex-col gap-2 font-mono text-[11.5px] text-ink-2">
            {[
              'document',
              'page + word boxes',
              'character span',
              'rectangle',
              'fact',
              'task',
            ].map((step, index) => (
              <li key={step} className="flex items-center gap-2">
                <span className="w-5 text-ink-4">{String(index + 1).padStart(2, '0')}</span>
                <span className={index === 2 || index === 3 ? 'text-accent' : undefined}>
                  {step}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </main>
  );
}

function GoogleMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 18 18" aria-hidden focusable="false">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.9 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z"
      />
    </svg>
  );
}
