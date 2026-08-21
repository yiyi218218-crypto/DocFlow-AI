'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { useAuth } from './providers';

export default function Home() {
  const { session, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    router.replace(session ? '/documents' : '/login');
  }, [session, loading, router]);

  return (
    <main className="flex min-h-dvh items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="size-2 animate-pulse rounded-full bg-accent" />
        <p className="font-mono text-xs text-faint">DocFlow AI</p>
      </div>
    </main>
  );
}
