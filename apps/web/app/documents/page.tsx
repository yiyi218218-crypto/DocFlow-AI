'use client';

/**
 * The dashboard.
 *
 * Layout notes, against the redesign audit:
 *   * Not three equal feature cards — an asymmetric 5/3 grid, deadlines given
 *     the wide column because they are what the product is for.
 *   * Stats are a hairline-divided strip, not four bordered boxes. The numbers
 *     are the content; boxes around them are decoration.
 *   * No left sidebar. Top nav only.
 *   * Upload is a real dropzone, so the primary action has a target the size of
 *     the intent behind it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useCallback, useRef, useState } from 'react';
import { useAuth } from '@/app/providers';
import { ApiError, apiFetch } from '@/lib/api';
import type { DocumentSummary, Paginated } from '@/lib/types';
import { cn, formatBytes, formatDayMonth, isBangla, relativeTime } from '@/lib/utils';
import { DeadlineRail } from './DeadlineRail';
import type { Dashboard } from './dashboard-types';

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { session } = useAuth();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data: dash, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => apiFetch<Dashboard>('/api/v1/dashboard'),
    refetchInterval: (q) => ((q.state.data?.documents.processing ?? 0) > 0 ? 2500 : false),
  });

  const { data: docs } = useQuery({
    queryKey: ['documents', 'list'],
    queryFn: () => apiFetch<Paginated<DocumentSummary>>('/api/v1/documents?limit=50'),
    refetchInterval: (q) =>
      (q.state.data?.data ?? []).some((d) => d.status === 'queued' || d.status === 'processing')
        ? 2500
        : false,
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return apiFetch<{ id: string }>('/api/v1/documents', { method: 'POST', body: form });
    },
    onSuccess: () => {
      setUploadError(null);
      void queryClient.invalidateQueries();
    },
    onError: (e) =>
      setUploadError(e instanceof ApiError ? e.problem.detail : 'Upload failed. Try again.'),
  });

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragging(false);
      const file = event.dataTransfer.files?.[0];
      if (file) upload.mutate(file);
    },
    [upload],
  );

  const documents = docs?.data ?? [];
  const firstName = session?.user.full_name?.split(' ')[0];

  return (
    <main id="main" className="mx-auto max-w-[1180px] px-5 py-9 lg:px-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-semibold tracking-[-0.02em]">
            {firstName ? `Welcome back, ${firstName}` : 'Your workspace'}
          </h1>
          <p className="mt-1 max-w-[52ch] text-[13.5px] text-ink-2">
            Every deadline below traces back to the exact line it came from.
          </p>
        </div>

        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = '';
          }}
        />
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
          className="h-9 rounded-[--radius-inner] bg-accent px-4 text-[13px] font-medium text-white shadow-[var(--shadow-raise)] transition-all duration-200 hover:bg-accent-hover active:scale-[0.98] disabled:opacity-50"
        >
          {upload.isPending ? 'Uploading…' : 'Upload a document'}
        </button>
      </header>

      <StatStrip dash={dash} loading={isLoading} />

      {uploadError && (
        <p
          role="alert"
          className="mt-5 rounded-[--radius-inner] border border-crit/25 bg-crit/[0.04] px-3.5 py-2.5 text-[12.5px] text-crit"
        >
          {uploadError}
        </p>
      )}

      <div className="mt-8 grid gap-x-10 gap-y-9 lg:grid-cols-8">
        {/* Deadlines get the wide column: they are the product. */}
        <section className="lg:col-span-5">
          <SectionHead
            title="Coming up"
            hint={dash ? `${dash.facts.deadlines} deadline${dash.facts.deadlines === 1 ? '' : 's'} found` : undefined}
          />
          {isLoading ? <RailSkeleton /> : <DeadlineRail deadlines={dash?.upcoming_deadlines ?? []} />}
        </section>

        <section className="lg:col-span-3">
          <SectionHead
            title="To do"
            hint={dash?.tasks.overdue ? `${dash.tasks.overdue} overdue` : undefined}
            hintTone={dash?.tasks.overdue ? 'crit' : undefined}
          />
          {isLoading ? <RailSkeleton rows={3} /> : <TaskList dash={dash} />}
        </section>
      </div>

      <section className="mt-10">
        <SectionHead
          title="Documents"
          hint={documents.length ? `${documents.length} total` : undefined}
        />

        {documents.length === 0 && !isLoading ? (
          <Dropzone
            dragging={dragging}
            onDrop={onDrop}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onPick={() => fileInput.current?.click()}
          />
        ) : (
          <ul className="mt-1 divide-y divide-line">
            {documents.map((d, i) => (
              <DocumentRow key={d.id} document={d} index={i} />
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

/* ------------------------------------------------------------------ stats */

function StatStrip({ dash, loading }: { dash?: Dashboard; loading: boolean }) {
  const rate = dash?.facts.citation_verification_rate;

  const items = [
    { label: 'Documents', value: dash?.documents.total ?? 0, sub: `${dash?.documents.pages ?? 0} pages` },
    { label: 'Deadlines', value: dash?.facts.deadlines ?? 0, sub: `${dash?.facts.requirements ?? 0} requirements` },
    { label: 'Open tasks', value: dash?.tasks.open ?? 0, sub: `${dash?.tasks.done ?? 0} done` },
    {
      label: 'Citations verified',
      value: rate === null || rate === undefined ? '—' : `${Math.round(rate * 100)}%`,
      sub: `${dash?.facts.total ?? 0} facts checked`,
      accent: true,
    },
  ];

  return (
    <dl className="mt-7 grid grid-cols-2 gap-y-6 border-y border-line py-5 sm:grid-cols-4 sm:divide-x sm:divide-line">
      {items.map((it, i) => (
        <div key={it.label} className={cn('px-0 sm:px-5', i === 0 && 'sm:pl-0')}>
          <dt className="eyebrow">{it.label}</dt>
          <dd
            className={cn(
              'mt-1.5 text-[26px] font-semibold leading-none tracking-[-0.03em]',
              it.accent && 'text-accent',
              loading && 'animate-pulse text-ink-4',
            )}
          >
            {loading ? '—' : it.value}
          </dd>
          <dd className="mt-1 text-[11.5px] text-ink-3">{it.sub}</dd>
        </div>
      ))}
    </dl>
  );
}

function SectionHead({
  title,
  hint,
  hintTone,
}: {
  title: string;
  hint?: string;
  hintTone?: 'crit';
}) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3 border-b border-line pb-2">
      <h2 className="text-[13px] font-semibold tracking-[-0.01em]">{title}</h2>
      {hint && (
        <span
          className={cn('font-mono text-[11px]', hintTone === 'crit' ? 'text-crit' : 'text-ink-3')}
        >
          {hint}
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ tasks */

function TaskList({ dash }: { dash?: Dashboard }) {
  const tasks = dash?.open_tasks ?? [];

  if (tasks.length === 0) {
    return (
      <p className="rounded-[--radius-inner] border border-dashed border-line px-4 py-8 text-center text-[13px] text-ink-3">
        Nothing outstanding.
      </p>
    );
  }

  return (
    <ul className="space-y-px">
      {tasks.map((t, i) => {
        const overdue = t.due_at ? new Date(t.due_at).getTime() < Date.now() : false;
        const body = (
          <>
            <span
              aria-hidden
              className="mt-[3px] size-3.5 shrink-0 rounded-[3px] border border-line-strong transition-colors duration-200 group-hover:border-accent"
            />
            <span className="min-w-0 flex-1">
              <span
                className={cn('block text-[13px] leading-snug', isBangla(t.title) && 'bn')}
                lang={isBangla(t.title) ? 'bn' : undefined}
              >
                {t.title}
              </span>
              {(t.document_title || t.due_at) && (
                <span className="mt-0.5 flex items-baseline gap-1.5 font-mono text-[10.5px] text-ink-4">
                  {t.due_at && (
                    // nowrap: "30 Sept" was breaking across two lines in this
                    // narrow column, which reads as a layout bug.
                    <span className={cn('whitespace-nowrap', overdue && 'text-crit')}>
                      {formatDayMonth(t.due_at)}
                    </span>
                  )}
                  {t.document_title && (
                    <span className="truncate">
                      {t.due_at ? '· ' : ''}
                      {t.document_title}
                    </span>
                  )}
                </span>
              )}
            </span>
            {t.origin === 'ai' && (
              <span
                className="mt-0.5 shrink-0 font-mono text-[10px] text-ink-4"
                title="Generated from an extracted fact"
              >
                auto
              </span>
            )}
          </>
        );

        return (
          <li key={t.id} className="rise" style={{ animationDelay: `${i * 40}ms` }}>
            {t.document_id ? (
              <Link
                href={`/documents/${t.document_id}`}
                className="group flex gap-2.5 rounded-[--radius-inner] px-2 py-2 transition-colors duration-200 hover:bg-raised"
              >
                {body}
              </Link>
            ) : (
              <div className="group flex gap-2.5 px-2 py-2">{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/* -------------------------------------------------------------- documents */

function DocumentRow({ document, index }: { document: DocumentSummary; index: number }) {
  const processing = document.status === 'queued' || document.status === 'processing';
  const failed = document.status === 'failed' || document.status === 'quarantined';
  const title = document.title ?? document.original_filename;

  return (
    <li className="rise" style={{ animationDelay: `${Math.min(index, 8) * 35}ms` }}>
      <Link
        href={`/documents/${document.id}`}
        data-testid="document-row"
        data-filename={document.original_filename}
        className="group flex items-center gap-4 px-2 py-3 transition-colors duration-200 hover:bg-raised"
      >
        <span
          aria-hidden
          className={cn(
            'size-1.5 shrink-0 rounded-full',
            processing ? 'animate-pulse bg-accent' : failed ? 'bg-crit' : 'bg-ok/70',
          )}
        />

        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span
              className={cn(
                'truncate text-[13.5px] font-medium transition-colors duration-200 group-hover:text-accent',
                isBangla(title) && 'bn',
              )}
              lang={isBangla(title) ? 'bn' : undefined}
            >
              {title}
            </span>
            {document.security_flags.includes('injection_suspected') && (
              <span
                className="shrink-0 border border-warn/40 px-1 font-mono text-[10px] text-warn"
                title="Contains text addressed to an AI system"
              >
                flagged
              </span>
            )}
          </span>
          <span className="mt-0.5 flex items-center gap-2 font-mono text-[10.5px] text-ink-4">
            <span>{document.language}</span>
            <span>· {formatBytes(document.size_bytes)}</span>
            {document.page_count ? <span>· {document.page_count}p</span> : null}
            <span>· {relativeTime(document.created_at)}</span>
          </span>
        </span>

        {processing ? (
          <span className="font-mono text-[11px] text-ink-3">processing…</span>
        ) : failed ? (
          <span className="font-mono text-[11px] text-crit">{document.error_code}</span>
        ) : (
          <span className="flex items-baseline gap-1 font-mono text-[11px] text-ink-3">
            <span className="text-[13px] font-medium text-ink">{document.fact_count}</span>
            facts
          </span>
        )}
      </Link>
    </li>
  );
}

function Dropzone({
  dragging,
  onDrop,
  onDragOver,
  onDragLeave,
  onPick,
}: {
  dragging: boolean;
  onDrop: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      className={cn(
        'mt-1 flex w-full flex-col items-center rounded-[--radius-card] border border-dashed px-6 py-14 text-center transition-all duration-200',
        dragging
          ? 'border-accent bg-accent-wash'
          : 'border-line-strong hover:border-accent hover:bg-raised',
      )}
    >
      <span className="text-[14px] font-medium">Drop a circular here</span>
      <span className="mt-1.5 max-w-[46ch] text-[12.5px] text-ink-2">
        A university circular, scholarship notice, or any PDF. DocFlow finds the
        deadlines and required documents, and shows you where each one came from.
      </span>
      <span className="mt-4 font-mono text-[11px] text-ink-4">
        PDF · DOCX · TXT · PNG · JPG — up to 4 MB
      </span>
    </button>
  );
}

/* ---------------------------------------------------------------- loading */

function RailSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <ul className="space-y-3.5" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <li key={i} className="flex gap-3">
          <span className="mt-1.5 size-[11px] shrink-0 rounded-full bg-sunken" />
          <span className="flex-1">
            <span className="block h-3 w-2/3 rounded bg-sunken" />
            <span className="mt-2 block h-2.5 w-1/3 rounded bg-sunken" />
          </span>
        </li>
      ))}
    </ul>
  );
}
