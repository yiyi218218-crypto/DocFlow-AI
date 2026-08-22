'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useAuth } from '@/app/providers';
import { AuthShell, Field, FormError, SubmitButton } from '@/components/auth/AuthShell';
import { GoogleButton, googleSignInAvailable, OrDivider } from '@/components/auth/GoogleButton';
import { cn } from '@/lib/utils';
import { ApiError, register } from '@/lib/api';

const MIN_PASSWORD = 10;

export default function SignUpPage() {
  const router = useRouter();
  const { setSession } = useAuth();

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});

    try {
      // Register creates the workspace and signs them in, so the response is
      // already the session — no second trip through /login.
      setSession(await register(email, password, fullName || undefined));
      router.push('/documents');
    } catch (err) {
      if (err instanceof ApiError) {
        // Field-level errors belong next to the field that caused them.
        if (err.problem.errors?.length) {
          setFieldErrors(Object.fromEntries(err.problem.errors.map((e) => [e.field, e.message])));
          setError(null);
        } else {
          setError(err.problem.detail);
        }
      } else {
        setError('Could not reach the server. Please try again.');
      }
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Create your workspace"
      subtitle="Upload a circular and get its deadlines, requirements and tasks — each traceable to the line it came from."
      footer={
        <>
          Already have an account?{' '}
          <Link href="/login" className="font-medium text-accent hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      {googleSignInAvailable && (
        <>
          <GoogleButton
            label="Sign up with Google"
            disabled={busy}
            onError={setError}
            onBusyChange={setGoogleBusy}
          />
          <OrDivider />
        </>
      )}

      <form
        className={cn('flex flex-col gap-3', !googleSignInAvailable && 'mt-8')}
        onSubmit={submit}
      >
        <Field
          label="Name"
          type="text"
          autoComplete="name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          hint={fieldErrors.full_name}
        />
        <Field
          label="Email"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          hint={fieldErrors.email}
        />
        <Field
          label="Password"
          type="password"
          required
          minLength={MIN_PASSWORD}
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={
            fieldErrors.password ??
            `At least ${MIN_PASSWORD} characters. Length matters more than symbols.`
          }
        />

        <FormError message={error} />

        <SubmitButton busy={busy || googleBusy}>
          {busy ? 'Creating…' : 'Create account'}
        </SubmitButton>
      </form>
    </AuthShell>
  );
}
