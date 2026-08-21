/**
 * Firebase, used only as an identity provider.
 *
 * The config below is public by design — it ships in the browser bundle and
 * Google documents it as non-secret. What protects the account is the server:
 * `POST /api/v1/auth/google` verifies the returned ID token against Google's
 * public keys before issuing a DocFlow session. The client is never trusted to
 * assert who it is.
 *
 * Loaded lazily so the SDK stays out of every route that doesn't sign in.
 */

import type { FirebaseApp } from 'firebase/app';

const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

export const googleSignInAvailable = Boolean(config.apiKey && config.projectId);

let app: FirebaseApp | null = null;

async function getApp(): Promise<FirebaseApp> {
  const { getApp: get, getApps, initializeApp } = await import('firebase/app');
  if (!app) app = getApps().length ? get() : initializeApp(config as Record<string, string>);
  return app;
}

/** Run the Google popup and return the Firebase ID token for our API. */
export async function signInWithGoogle(): Promise<string> {
  const { GoogleAuthProvider, getAuth, signInWithPopup } = await import('firebase/auth');
  const auth = getAuth(await getApp());
  auth.useDeviceLanguage();

  const provider = new GoogleAuthProvider();
  // Always show the chooser: silently reusing a session is confusing on a
  // shared machine, which is common for the students this is built for.
  provider.setCustomParameters({ prompt: 'select_account' });

  const credential = await signInWithPopup(auth, provider);
  return credential.user.getIdToken();
}

/** Firebase's own session is not used after the exchange, so drop it. */
export async function clearFirebaseSession(): Promise<void> {
  if (!app) return;
  const { getAuth, signOut } = await import('firebase/auth');
  await signOut(getAuth(app)).catch(() => undefined);
}
