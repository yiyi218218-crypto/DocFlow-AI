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
      <header className="sticky top-0 z-20 flex h-13 shrink-0 items-center justify-between border-b border-line bg-bg/85 px-5 backdrop-blur-md lg:px-8">
        <div className="flex items-center gap-5">
          <Link
            href="/documents"
            className="flex items-center gap-2 text-[14px] font-semibold tracking-[-0.02em]"
          >
            <span aria-hidden className="text-accent">◲</span>
            DocFlow
          </Link>
          <nav className="flex items-center gap-1">
            <NavLink href="/documents" active={pathname.startsWith('/documents')}>
              Documents
            </NavLink>
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden font-mono text-[11px] text-ink-4 sm:inline">
            {session.workspace.name}
          </span>
          <button
            type="button"
            onClick={async () => {
              await logout();
              setSession(null);
              router.push('/login');
            }}
            className="h-7 rounded-[--radius-inner] border border-line px-2.5 text-[12px] text-ink-2 transition-colors duration-200 hover:border-line-strong hover:text-ink"
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
        'rounded-[--radius-inner] px-2.5 py-1 text-[13px] transition-colors duration-200',
        active ? 'bg-raised text-ink' : 'text-ink-3 hover:text-ink',
      )}
    >
      {children}
    </Link>
  );
}
