import { chromium } from '@playwright/test';
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
p.on('console', m => { const t = m.text(); if (/error|auth\//i.test(t)) console.log('CONSOLE:', t.slice(0, 240)); });
p.on('pageerror', e => console.log('PAGEERROR:', String(e).slice(0, 240)));
p.on('response', r => { if (/identitytoolkit|googleapis|auth\/google/.test(r.url())) console.log('  HTTP', r.status(), r.url().slice(0, 100)); });

await p.goto('https://docflow-ai-six.vercel.app/login');
await p.waitForTimeout(2500);

// Surface the raw Firebase error without needing a real Google account.
const result = await p.evaluate(async () => {
  try {
    const { initializeApp, getApps, getApp } = await import('https://www.gstatic.com/firebasejs/11.0.2/firebase-app.js');
    const { getAuth, GoogleAuthProvider, signInWithPopup } = await import('https://www.gstatic.com/firebasejs/11.0.2/firebase-auth.js');
    const cfg = {
      apiKey: 'AIzaSyA3262A47kUr9D4t58VQOk-EEpX8OVKHvU',
      authDomain: 'docflow-ai-23f5d.firebaseapp.com',
      projectId: 'docflow-ai-23f5d',
      appId: '1:618899931523:web:8fa3bc3be78ed3b69676b6',
    };
    const app = getApps().length ? getApp() : initializeApp(cfg);
    await signInWithPopup(getAuth(app), new GoogleAuthProvider());
    return 'unexpectedly succeeded';
  } catch (e) {
    return `${e.code || 'no-code'} :: ${(e.message || '').slice(0, 200)}`;
  }
});
console.log('FIREBASE ERROR:', result);
await b.close();
