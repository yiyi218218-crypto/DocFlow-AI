'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { useAuth } from '@/app/providers';
import { logout } from '@/lib/api';
import { cn } from '@/lib/utils';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { session, loading, setSession } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !session) router.replace('/login');
  }, [loading, session, router]);

  if (loading || !session) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <div className="size-2 animate-pulse rounded-full bg-accent" />
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center justify-between border-b border-border bg-bg px-4">
        <div className="flex items-center gap-5">
          <Link href="/documents" className="text-[13px] font-semibold tracking-tight">
            DocFlow
          </Link>
          <nav className="flex items-center gap-1">
            <NavLink href="/documents" active={pathname.startsWith('/documents')}>
              Documents
            </NavLink>
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden font-mono text-[11px] text-faint sm:inline">
            {session.workspace.name}
          </span>
          <button
            type="button"
            onClick={async () => {
              await logout();
              setSession(null);
              router.push('/login');
            }}
            className="h-7 rounded-[--radius] border border-border px-2.5 text-xs text-muted hover:bg-surface-2"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

function NavLink({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'rounded-[--radius] px-2.5 py-1 text-[13px] transition-colors',
        active ? 'bg-surface-2 text-fg' : 'text-muted hover:text-fg',
      )}
    >
      {children}
    </Link>
  );
}
