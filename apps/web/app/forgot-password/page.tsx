'use client';

import Link from 'next/link';
import { useState } from 'react';
import { AuthShell, Field, FormError, SubmitButton } from '@/components/auth/AuthShell';
import { ApiError, requestPasswordReset } from '@/lib/api';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await requestPasswordReset(email);
      // Confirmed the same way whether or not the address is registered —
      // anything else would let someone test which emails have accounts.
      setSent(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.problem.detail : 'Could not reach the server. Try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title={sent ? 'Check your email' : 'Reset your password'}
      subtitle={
        sent
          ? `If ${email} has an account, a link to choose a new password is on its way. It expires in an hour.`
          : 'Enter the address you signed up with and we will send you a link.'
      }
      footer={
        <Link href="/login" className="font-medium text-accent hover:underline">
          Back to sign in
        </Link>
      }
    >
      {sent ? (
        <div className="mt-8 rounded-[--radius-card] border border-line bg-raised p-4">
          <p className="text-[12.5px] text-ink-2">
            Nothing arrived? Check spam, or{' '}
            <button
              type="button"
              onClick={() => setSent(false)}
              className="font-medium text-accent hover:underline"
            >
              try another address
            </button>
            .
          </p>
        </div>
      ) : (
        <form className="mt-8 flex flex-col gap-3" onSubmit={submit}>
          <Field
            label="Email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <FormError message={error} />
          <SubmitButton busy={busy}>{busy ? 'Sending…' : 'Send reset link'}</SubmitButton>
        </form>
      )}
    </AuthShell>
  );
}
