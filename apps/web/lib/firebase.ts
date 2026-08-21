/**
 * Firebase, used only as an identity provider.
 *
 * The config below is public by design — it ships in the browser bundle and
 * Google documents it as non-secret. What protects the account is the server:
 * `POST /api/v1/auth/google` verifies the returned ID token against Google's
 * public keys before issuing a DocFlow session. The client is never trusted to
 * assert who it is.
 *
 * Popup first, redirect as fallback. `signInWithPopup` is the nicer flow but
 * fails in more situations than it looks: popup blockers, a strict
 * Cross-Origin-Opener-Policy severing the window handle, and browsers that
 * partition third-party storage for the `*.firebaseapp.com` auth domain.
 * `signInWithRedirect` survives all three, so a popup failure escalates to it
 * rather than dead-ending the user.
 *
 * Loaded lazily so the SDK stays out of every route that does not sign in.
 */

import type { FirebaseApp } from 'firebase/app';

/**
 * Trim every value.
 *
 * `echo "$v" | vercel env add` stores the trailing newline as part of the
 * value, and Next inlines it verbatim at build time. A newline inside
 * authDomain makes the auth handler URL invalid and the popup throws a plain
 * Error with no `code` — which surfaces as an unhelpful "unknown". Cheap to
 * defend against, silent and slow to diagnose otherwise.
 */
const clean = (value: string | undefined): string | undefined => value?.trim() || undefined;

const config = {
  apiKey: clean(process.env.NEXT_PUBLIC_FIREBASE_API_KEY),
  authDomain: clean(process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN),
  projectId: clean(process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID),
  appId: clean(process.env.NEXT_PUBLIC_FIREBASE_APP_ID),
};

export const googleSignInAvailable = Boolean(config.apiKey && config.projectId);

/** Set before a redirect so the login page knows to finish the exchange. */
const PENDING_KEY = 'docflow:google-redirect';

let app: FirebaseApp | null = null;

async function getApp(): Promise<FirebaseApp> {
  const { getApp: get, getApps, initializeApp } = await import('firebase/app');
  if (!app) app = getApps().length ? get() : initializeApp(config as Record<string, string>);
  return app;
}

async function getAuthInstance() {
  const { getAuth } = await import('firebase/auth');
  const auth = getAuth(await getApp());
  auth.useDeviceLanguage();
  return auth;
}

/** Error codes where a popup cannot work but a full redirect still can. */
const REDIRECT_WORTHY = new Set([
  'auth/popup-blocked',
  'auth/popup-closed-by-user',
  'auth/cancelled-popup-request',
  'auth/operation-not-supported-in-this-environment',
  'auth/web-storage-unsupported',
  'auth/internal-error',
]);

export class GoogleSignInError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'GoogleSignInError';
  }
}

/** Human, actionable text for the failures that actually happen. */
export function describeGoogleError(code: string): string {
  switch (code) {
    case 'auth/unauthorized-domain':
      return 'This site is not authorised for Google sign-in yet. Add it under Firebase → Authentication → Settings → Authorized domains.';
    case 'auth/operation-not-allowed':
      return 'Google sign-in is not enabled for this Firebase project.';
    case 'auth/popup-blocked':
      return 'Your browser blocked the sign-in window. Allow popups for this site, or use your email.';
    case 'auth/account-exists-with-different-credential':
      return 'An account already exists with that email. Sign in with your password instead.';
    case 'auth/network-request-failed':
      return 'Could not reach Google. Check your connection and try again.';
    case 'auth/invalid-api-key':
    case 'auth/api-key-not-valid':
      return 'The Firebase configuration on this site is invalid.';
    default:
      return `Google sign-in did not complete (${code}). Try again, or use your email.`;
  }
}

/**
 * Run the Google popup and return a Firebase ID token.
 *
 * Returns null when the flow has escalated to a full-page redirect — the caller
 * should simply stop, because the page is about to navigate away.
 */
export async function signInWithGoogle(): Promise<string | null> {
  const { GoogleAuthProvider, signInWithPopup, signInWithRedirect } = await import(
    'firebase/auth'
  );
  const auth = await getAuthInstance();

  const provider = new GoogleAuthProvider();
  // Always show the chooser: silently reusing a session is confusing on a
  // shared machine, which is common for the students this is built for.
  provider.setCustomParameters({ prompt: 'select_account' });

  try {
    const credential = await signInWithPopup(auth, provider);
    return credential.user.getIdToken();
  } catch (error) {
    // Fall back to the error's own name/message when there is no Firebase
    // code, so a non-Firebase failure is still diagnosable rather than
    // collapsing to "unknown".
    const raw = error as { code?: string; name?: string; message?: string };
    const code = raw.code ?? (raw.name ? `${raw.name}: ${raw.message ?? ''}`.trim() : 'unknown');

    if (REDIRECT_WORTHY.has(code)) {
      // The popup could not work here. Fall back to a redirect rather than
      // dead-ending; the login page picks the result up on the way back.
      try {
        sessionStorage.setItem(PENDING_KEY, '1');
      } catch {
        /* private mode — the redirect still works, we just cannot flag it */
      }
      await signInWithRedirect(auth, provider);
      return null;
    }

    throw new GoogleSignInError(code, describeGoogleError(code));
  }
}

/**
 * Complete a redirect sign-in, if one is in flight.
 *
 * Returns the ID token, or null when there is nothing to finish.
 */
export async function completeGoogleRedirect(): Promise<string | null> {
  if (!googleSignInAvailable) return null;

  let pending = false;
  try {
    pending = sessionStorage.getItem(PENDING_KEY) === '1';
  } catch {
    /* ignore */
  }
  if (!pending) return null;

  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* ignore */
  }

  const { getRedirectResult } = await import('firebase/auth');
  const auth = await getAuthInstance();
  const credential = await getRedirectResult(auth);
  return credential ? credential.user.getIdToken() : null;
}

/** Firebase's own session is not used after the exchange, so drop it. */
export async function clearFirebaseSession(): Promise<void> {
  if (!app) return;
  const { getAuth, signOut } = await import('firebase/auth');
  await signOut(getAuth(app)).catch(() => undefined);
}
