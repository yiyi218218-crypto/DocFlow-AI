'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useAuth } from '@/app/providers';
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

  return (
    <main id="main" className="grid min-h-dvh lg:grid-cols-2">
      <div className="flex items-center justify-center px-6 py-16">
        <div className="w-full max-w-sm">
          <h1 className="text-[21px] font-semibold tracking-[-0.02em]">Sign in to DocFlow</h1>
          <p className="mt-1.5 text-[13.5px] text-ink-2">
            Turn documents into deadlines, tasks and actions.
          </p>

          <form
            className="mt-8 flex flex-col gap-3"
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
                className="h-9 rounded-[--radius] border border-line bg-surface px-3 text-[13.5px] outline-none transition-colors duration-200 focus:border-accent"
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
                className="h-9 rounded-[--radius] border border-line bg-surface px-3 text-[13.5px] outline-none transition-colors duration-200 focus:border-accent"
              />
            </label>

            {error && (
              <p role="alert" className="text-[12px] text-crit">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="mt-1 h-9 rounded-[--radius] bg-accent text-[13.5px] font-medium text-white shadow-[var(--shadow-raise)] transition-all duration-200 hover:bg-accent-hover active:scale-[0.98] disabled:opacity-50"
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
              className="mt-2.5 h-8 w-full rounded-[--radius-inner] border border-line-strong bg-surface text-[12.5px] font-medium transition-all duration-200 hover:border-accent hover:text-accent active:scale-[0.99] disabled:opacity-50"
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
