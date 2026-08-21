'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useState } from 'react';
import { ApiError, bootstrapSession, type SessionResponse } from '@/lib/api';

interface AuthState {
  session: SessionResponse | null;
  loading: boolean;
  setSession: (session: SessionResponse | null) => void;
}

const AuthContext = createContext<AuthState>({
  session: null,
  loading: true,
  setSession: () => {},
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          // Never retry a client error — a 404 will still be a 404, and
          // retrying a 401 fights the token-refresh logic in lib/api.
          if (error instanceof ApiError && error.status < 500) return false;
          return failureCount < 2;
        },
      },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [loading, setLoading] = useState(true);

  // The access token lives in memory, so a reload has none. Restore it from the
  // httpOnly refresh cookie before rendering anything that needs auth.
  useEffect(() => {
    let cancelled = false;
    bootstrapSession()
      .then((restored) => {
        if (!cancelled) setSession(restored);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session, loading, setSession }}>
        {children}
      </AuthContext.Provider>
    </QueryClientProvider>
  );
}
