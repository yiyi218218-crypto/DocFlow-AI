'use client';

/**
 * A single extracted fact.
 *
 * Two rules this component exists to enforce:
 *
 * 1. Confidence is shown as a labelled bucket, never a number. "0.73" implies a
 *    precision we do not have and invites users to reason about it.
 * 2. A fact whose citation could not be verified never renders as confirmed and
 *    never offers a highlight. A confidently wrong highlight destroys more trust
 *    than an absent one, because it teaches the user the system lies.
 */

import { useViewerStore } from '@/lib/stores/viewer';
import type { Fact } from '@/lib/types';
import { cn, formatDeadline, isBangla } from '@/lib/utils';

const CONFIDENCE_STYLES = {
  high: { dot: 'bg-ok', label: 'High confidence' },
  medium: { dot: 'bg-warn', label: 'Review suggested' },
  low: { dot: 'bg-ink-4', label: 'Low confidence' },
} as const;

export function FactCard({ fact }: { fact: Fact }) {
  const focusFact = useViewerStore((s) => s.focusFact);
  const activeId = useViewerStore((s) => s.activeHighlight?.factId);

  const isActive = activeId === fact.id;
  const canHighlight = fact.source_rects.length > 0 && fact.citation_status !== 'unverified';
  const confidence = CONFIDENCE_STYLES[fact.confidence_bucket];

  const title = factTitle(fact);
  const detail = factDetail(fact);

  function navigate() {
    focusFact({
      factId: fact.id,
      page: fact.source_page,
      rects: canHighlight ? fact.source_rects : [],
    });
  }

  return (
    <button
      type="button"
      onClick={navigate}
      aria-current={isActive ? 'true' : undefined}
      className={cn(
        'group relative w-full rounded-[--radius-inner] border p-3 pl-3.5 text-left transition-all duration-200',
        isActive
          ? 'border-accent-line bg-accent-wash'
          : 'border-transparent bg-surface hover:border-line hover:bg-raised',
      )}
    >
      {/* A left edge marks the active fact without a ring, which on a dense
          list reads as noise. */}
      <span
        aria-hidden
        className={cn(
          'absolute inset-y-1.5 left-0 w-[2px] rounded-full transition-all duration-200',
          isActive ? 'bg-accent' : 'bg-transparent group-hover:bg-line-strong',
        )}
      />

      <div className="flex items-start justify-between gap-3">
        <p
          className={cn('text-[13px] font-medium leading-snug', isBangla(title) && 'bn')}
          lang={isBangla(title) ? 'bn' : undefined}
        >
          {title}
        </p>
        <span
          className="mt-1 flex shrink-0 items-center gap-1.5"
          title={confidence.label}
        >
          <span className={cn('size-1.5 rounded-full', confidence.dot)} aria-hidden />
          {/* Never colour alone — the label is what makes this accessible. */}
          <span className="sr-only">{confidence.label}</span>
        </span>
      </div>

      {detail && <p className="mt-1 text-[12px] text-ink-2">{detail}</p>}

      {fact.normalization_status && fact.normalization_status !== 'ok' && (
        <p className="mt-1.5 text-[11px] text-warn">
          {normalizationMessage(fact)}
        </p>
      )}

      <div className="mt-2 flex items-center gap-2 font-mono text-[10.5px] text-ink-4">
        {canHighlight ? (
          <span className="inline-flex items-center gap-1 transition-colors duration-200 group-hover:text-accent">
            Page {fact.source_page}
            <span aria-hidden>↗</span>
          </span>
        ) : fact.citation_status === 'page_only' ? (
          <span title="This page was read without position data">
            Page {fact.source_page} · no highlight
          </span>
        ) : (
          <span className="text-ink-4">Source not verified</span>
        )}
      </div>
    </button>
  );
}

function factTitle(fact: Fact): string {
  const p = fact.payload;
  switch (fact.kind) {
    case 'deadline':
      return String(p.label ?? 'Deadline');
    case 'requirement':
    case 'action_item':
      return String(p.description ?? fact.source_quote.slice(0, 90));
    case 'entity':
      return String(p.name ?? '');
    case 'contact':
      return String(p.value ?? '');
    case 'link':
      return String(p.url ?? '');
    case 'risk':
      return String(p.description ?? '');
    default:
      return fact.source_quote.slice(0, 90);
  }
}

function factDetail(fact: Fact): string | null {
  const p = fact.payload;
  if (fact.kind === 'deadline') {
    const formatted = formatDeadline(fact.due_at);
    const raw = p.raw_date_text ? String(p.raw_date_text) : null;
    // Always show what the document actually said, so a misparse is visible
    // and correctable rather than silently wrong.
    return raw && !formatted.startsWith(raw) ? `${formatted}  ·  “${raw}”` : formatted;
  }
  if (fact.kind === 'requirement') {
    const parts: string[] = [];
    if (p.is_mandatory === false) parts.push('Optional');
    if (p.quantity) parts.push(String(p.quantity));
    if (p.applies_to) parts.push(`Applies to ${String(p.applies_to)}`);
    return parts.length ? parts.join(' · ') : null;
  }
  if (fact.kind === 'contact') return String(p.contact_type ?? '');
  if (fact.kind === 'entity') return String(p.entity_type ?? '');
  return null;
}

function normalizationMessage(fact: Fact): string {
  switch (fact.normalization_status) {
    case 'ambiguous':
      return 'This date could be read two ways — confirm which is correct.';
    case 'relative_unresolved':
      return 'This date is relative and could not be resolved.';
    case 'implausible':
      return 'This date looks implausible and was not applied.';
    default:
      return 'This date could not be read.';
  }
}
