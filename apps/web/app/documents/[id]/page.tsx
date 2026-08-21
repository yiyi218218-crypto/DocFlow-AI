'use client';

/**
 * The split viewer — the interaction the whole product is built around.
 *
 * Click a fact on the right, the PDF on the left scrolls to its page and the
 * exact source sentence highlights. The active fact is mirrored into the URL
 * (?fact=…&page=…) so the state is shareable, survives a refresh, and is
 * directly assertable in an end-to-end test.
 */

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import dynamic from 'next/dynamic';
import { useEffect, useMemo } from 'react';
import { FactCard } from '@/components/facts/FactCard';
import { apiFetch } from '@/lib/api';
import { useViewerStore } from '@/lib/stores/viewer';
import {
  FACT_LABELS,
  STAGE_LABELS,
  isTerminal,
  type DocumentAnalysis,
  type FactKind,
} from '@/lib/types';
import { cn } from '@/lib/utils';

// pdfjs is ~340 kB — never in the shared bundle, and it needs `window`.
const PdfViewer = dynamic(
  () => import('@/components/viewer/PdfViewer').then((m) => m.PdfViewer),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center bg-surface-2">
        <div className="size-2 animate-pulse rounded-full bg-accent" />
      </div>
    ),
  },
);

const KIND_ORDER: FactKind[] = [
  'deadline',
  'requirement',
  'action_item',
  'risk',
  'contact',
  'link',
  'entity',
];

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const focusFact = useViewerStore((s) => s.focusFact);
  const activeFactId = useViewerStore((s) => s.activeHighlight?.factId);

  const { data, isLoading } = useQuery({
    queryKey: ['documents', 'analysis', id],
    queryFn: () => apiFetch<DocumentAnalysis>(`/api/v1/documents/${id}/analysis`),
    // Back off as processing drags on, and stop entirely once terminal.
    refetchInterval: (query) => {
      const status = query.state.data?.document.status;
      if (!status) return 2000;
      return isTerminal(status) ? false : 2000;
    },
  });

  const facts = useMemo(() => data?.facts ?? [], [data]);

  // Restore the highlight from the URL, so a shared link opens on the fact.
  useEffect(() => {
    const factId = searchParams.get('fact');
    if (!factId || facts.length === 0 || activeFactId === factId) return;
    const fact = facts.find((f) => f.id === factId);
    if (fact) {
      focusFact({ factId: fact.id, page: fact.source_page, rects: fact.source_rects });
    }
  }, [searchParams, facts, activeFactId, focusFact]);

  // Mirror the active fact back into the URL.
  useEffect(() => {
    if (!activeFactId || searchParams.get('fact') === activeFactId) return;
    const fact = facts.find((f) => f.id === activeFactId);
    if (!fact) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set('fact', fact.id);
    params.set('page', String(fact.source_page));
    router.replace(`?${params.toString()}`, { scroll: false });
  }, [activeFactId, facts, router, searchParams]);

  if (isLoading || !data) {
    return (
      <div className="flex h-[calc(100dvh-3rem)] items-center justify-center">
        <div className="size-2 animate-pulse rounded-full bg-accent" />
      </div>
    );
  }

  const { document, pages } = data;
  const processing = !isTerminal(document.status);

  if (processing) {
    return <ProcessingView analysis={data} />;
  }

  if (document.status === 'failed' || document.status === 'quarantined') {
    return <FailedView analysis={data} />;
  }

  const grouped = KIND_ORDER.map((kind) => ({
    kind,
    items: facts.filter((f) => f.kind === kind),
  })).filter((group) => group.items.length > 0);

  return (
    <div className="flex h-[calc(100dvh-3rem)] flex-col">
      <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border px-4">
        <Link href="/documents" className="text-muted hover:text-fg" aria-label="Back">
          ←
        </Link>
        <p className="truncate text-[13px] font-medium">
          {document.title ?? document.original_filename}
        </p>
        <span className="hidden font-mono text-[11px] text-faint sm:inline">
          {document.document_type?.replace(/_/g, ' ')} · {document.language} ·{' '}
          {document.page_count}p
        </span>

        {data.citation_verification_rate !== null && (
          <span
            className="ml-auto font-mono text-[11px] text-faint"
            title="Share of facts whose quote was matched against the source text"
          >
            {Math.round(data.citation_verification_rate * 100)}% citations verified
          </span>
        )}
      </div>

      {document.security_flags.includes('injection_suspected') && (
        <div
          role="alert"
          className="shrink-0 border-b border-medium/30 bg-medium/10 px-4 py-2 text-xs text-medium"
        >
          This document contains text that attempts to manipulate AI systems.
          Results may be unreliable.
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-rows-2 lg:grid-cols-[1fr_400px] lg:grid-rows-1">
        <div className="min-h-0 border-b border-border lg:border-b-0 lg:border-r">
          <PdfViewer fileUrl={`/api/v1/documents/${id}/file`} pages={pages} />
        </div>

        <aside className="min-h-0 overflow-y-auto bg-bg">
          <div className="flex flex-col gap-5 p-4">
            {document.summary && (
              <section>
                <h2 className="font-mono text-[10px] uppercase tracking-[0.12em] text-faint">
                  Summary
                </h2>
                <p className="mt-2 text-[13px] leading-relaxed text-muted">
                  {document.summary}
                </p>
              </section>
            )}

            {grouped.map(({ kind, items }) => (
              <section key={kind}>
                <h2 className="flex items-baseline gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-faint">
                  {FACT_LABELS[kind]}
                  <span className="text-faint/70">{items.length}</span>
                </h2>
                <div className="mt-2 flex flex-col gap-1.5">
                  {items.map((fact) => (
                    <FactCard key={fact.id} fact={fact} />
                  ))}
                </div>
              </section>
            ))}

            {data.low_confidence_hidden > 0 && (
              <p className="rounded-[--radius] border border-dashed border-border px-3 py-2 text-[11px] text-faint">
                {data.low_confidence_hidden} low-confidence item
                {data.low_confidence_hidden === 1 ? '' : 's'} hidden
              </p>
            )}

            {grouped.length === 0 && (
              <p className="text-xs text-muted">
                No facts were extracted from this document.
              </p>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function ProcessingView({ analysis }: { analysis: DocumentAnalysis }) {
  const { document } = analysis;
  const done = document.stages.filter((s) => s.status === 'completed').length;
  const running = document.stages.find((s) => s.status === 'running');
  const progress = document.stages.length ? done / document.stages.length : 0;

  return (
    <main className="mx-auto flex max-w-md flex-col items-center px-4 py-24 text-center">
      <div className="size-2 animate-pulse rounded-full bg-accent" />
      <h1 className="mt-4 text-sm font-medium">{document.original_filename}</h1>
      {/* Honest per-stage progress. Users tolerate 90 seconds if they can see
          movement; a fake spinner reads as a hang. */}
      <p className="mt-1 font-mono text-xs text-muted">
        {running ? STAGE_LABELS[running.stage] ?? running.stage : 'Queued'}
      </p>

      <div className="mt-5 h-1 w-full overflow-hidden rounded-full bg-surface-2">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500"
          style={{ width: `${Math.max(4, progress * 100)}%` }}
        />
      </div>

      <ol className="mt-6 w-full space-y-1">
        {document.stages.map((stage) => (
          <li
            key={stage.stage}
            className={cn(
              'flex items-center justify-between font-mono text-[11px]',
              stage.status === 'completed' && 'text-muted',
              stage.status === 'running' && 'text-accent',
              stage.status === 'pending' && 'text-faint',
              stage.status === 'failed' && 'text-danger',
            )}
          >
            <span>{STAGE_LABELS[stage.stage] ?? stage.stage}</span>
            <span>
              {stage.status === 'completed'
                ? `${stage.duration_ms ?? 0}ms`
                : stage.status === 'skipped'
                  ? 'skipped'
                  : stage.status}
            </span>
          </li>
        ))}
      </ol>
    </main>
  );
}

function FailedView({ analysis }: { analysis: DocumentAnalysis }) {
  const { document } = analysis;
  return (
    <main className="mx-auto flex max-w-md flex-col items-center px-4 py-24 text-center">
      <h1 className="text-sm font-medium">This document could not be processed</h1>
      {/* The API returns messages a user can act on, not stack traces. */}
      <p className="mt-2 text-xs text-muted">{document.error_detail}</p>
      <p className="mt-3 font-mono text-[11px] text-faint">{document.error_code}</p>
      <Link
        href="/documents"
        className="mt-6 h-8 rounded-[--radius] border border-border-strong px-3 text-xs font-medium leading-8 hover:bg-surface-2"
      >
        Back to documents
      </Link>
    </main>
  );
}
