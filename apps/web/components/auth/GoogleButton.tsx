'use client';

/**
 * "Continue with Google", and everything the flow needs behind it.
 *
 * Sign-in and sign-up are the same operation on Google's side — the backend
 * upserts the account either way — so both pages mount this rather than
 * carrying their own copy of the popup/redirect dance, which is the part most
 * likely to drift out of sync.
 */

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useAuth } from '@/app/providers';
import { ApiError, getSession, loginWithGoogle } from '@/lib/api';
import {
  clearFirebaseSession,
  completeGoogleRedirect,
  GoogleSignInError,
  signInWithGoogle,
} from '@/lib/firebase';

export { googleSignInAvailable } from '@/lib/firebase';

export function GoogleButton({
  label,
  disabled,
  onError,
  onBusyChange,
}: {
  label: string;
  /** The parent's own in-flight state — an email submit should lock this too. */
  disabled?: boolean;
  onError: (message: string | null) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const router = useRouter();
  const { setSession } = useAuth();
  const [busy, setBusy] = useState(false);

  function setBusyState(next: boolean) {
    setBusy(next);
    onBusyChange?.(next);
  }

  async function exchangeAndEnter(idToken: string) {
    await loginWithGoogle(idToken);
    // Firebase's own session is not used once we hold a DocFlow token.
    void clearFirebaseSession();
    setSession(await getSession());
    router.push('/documents');
  }

  function reportFailure(err: unknown) {
    // Say what actually went wrong. A generic "did not complete" is unhelpful
    // to the user AND hides the cause from whoever has to debug it.
    if (err instanceof ApiError) {
      onError(err.problem.detail);
    } else if (err instanceof GoogleSignInError) {
      onError(err.message);
    } else {
      onError('Google sign-in did not complete. Try again, or use your email.');
    }
    setBusyState(false);
  }

  // Finish a sign-in that fell back to a full-page redirect.
  useEffect(() => {
    let cancelled = false;
    completeGoogleRedirect()
      .then(async (idToken) => {
        if (!idToken || cancelled) return;
        setBusyState(true);
        await exchangeAndEnter(idToken);
      })
      .catch((err) => {
        if (!cancelled) reportFailure(err);
      });
    return () => {
      cancelled = true;
    };
    // Runs once on mount; the redirect result is only ever available then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function continueWithGoogle() {
    setBusyState(true);
    onError(null);
    try {
      const idToken = await signInWithGoogle();
      // null means the flow escalated to a redirect — the page is navigating
      // away, so there is nothing more to do here.
      if (idToken === null) return;
      await exchangeAndEnter(idToken);
    } catch (err) {
      reportFailure(err);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void continueWithGoogle()}
      disabled={busy || disabled}
      className="mt-7 flex h-11 w-full items-center justify-center gap-2.5 rounded-[--radius-inner] border border-line-strong bg-surface text-[13.5px] font-medium transition-all duration-200 hover:border-ink-4 hover:bg-raised active:scale-[0.99] disabled:opacity-50 sm:h-10"
    >
      <GoogleMark />
      {busy ? 'Opening Google…' : label}
    </button>
  );
}

export function OrDivider() {
  return (
    <div className="my-5 flex items-center gap-3">
      <span className="h-px flex-1 bg-line" />
      <span className="font-mono text-[11px] text-ink-4">or</span>
      <span className="h-px flex-1 bg-line" />
    </div>
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
