'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';
import { useAuth } from '@/app/providers';
import { AuthShell, Field, FormError, SubmitButton } from '@/components/auth/AuthShell';
import { ApiError, completePasswordReset, getSession } from '@/lib/api';

const MIN_PASSWORD = 10;

function ResetForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setSession } = useAuth();

  const token = searchParams.get('token');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }

    setBusy(true);
    setError(null);
    try {
      await completePasswordReset(token!, password);
      // The reset signs them in, so there is no reason to bounce back to a
      // login form having just proved control of the address.
      setSession(await getSession());
      router.push('/documents');
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.problem.detail
          : 'Could not reach the server. Please try again.',
      );
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthShell
        title="This link is incomplete"
        subtitle="The reset link is missing its token. It may have been cut short by your email client — try copying the whole URL, or request a new one."
        footer={
          <Link href="/forgot-password" className="font-medium text-accent hover:underline">
            Request a new link
          </Link>
        }
      >
        <span />
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Choose a new password"
      subtitle="This link works once, and every other session on your account will be signed out."
      footer={
        <Link href="/login" className="font-medium text-accent hover:underline">
          Back to sign in
        </Link>
      }
    >
      <form className="mt-8 flex flex-col gap-3" onSubmit={submit}>
        <Field
          label="New password"
          type="password"
          required
          minLength={MIN_PASSWORD}
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={`At least ${MIN_PASSWORD} characters.`}
        />
        <Field
          label="Confirm password"
          type="password"
          required
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <FormError message={error} />
        <SubmitButton busy={busy}>{busy ? 'Saving…' : 'Set password and sign in'}</SubmitButton>
      </form>
    </AuthShell>
  );
}

export default function ResetPasswordPage() {
  // useSearchParams needs a Suspense boundary to keep the route static.
  return (
    <Suspense fallback={<div className="min-h-dvh" />}>
      <ResetForm />
    </Suspense>
  );
}
