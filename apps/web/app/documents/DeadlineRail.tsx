'use client';

/**
 * Upcoming deadlines, as a time rail rather than three equal cards.
 *
 * A vertical rail with a dot per deadline reads as a *sequence*, which is what
 * a deadline list actually is. Urgency is carried by the dot and the day count,
 * so nothing depends on colour alone.
 */

import Link from 'next/link';
import type { UpcomingDeadline } from './dashboard-types';
import { cn, formatDayMonth, isBangla } from '@/lib/utils';

function urgency(days: number): { tone: string; dot: string; label: string } {
  if (days < 0) return { tone: 'text-crit', dot: 'bg-crit', label: 'overdue' };
  if (days === 0) return { tone: 'text-crit', dot: 'bg-crit', label: 'today' };
  if (days <= 7) return { tone: 'text-warn', dot: 'bg-warn', label: `${days}d left` };
  if (days <= 30) return { tone: 'text-ink-2', dot: 'bg-ink-4', label: `${days}d left` };
  return { tone: 'text-ink-3', dot: 'bg-line-strong', label: `${days}d left` };
}

export function DeadlineRail({ deadlines }: { deadlines: UpcomingDeadline[] }) {
  if (deadlines.length === 0) {
    return (
      <p className="rounded-[--radius-inner] border border-dashed border-line px-4 py-8 text-center text-[13px] text-ink-3">
        No deadlines in the next 60 days.
      </p>
    );
  }

  return (
    <ol className="relative">
      {/* The rail itself. Stops at the last dot rather than running past it. */}
      <span
        aria-hidden
        className="absolute left-[5px] top-2 w-px bg-line"
        style={{ height: `calc(100% - ${deadlines.length > 1 ? '2.25rem' : '100%'})` }}
      />

      {deadlines.map((d, i) => {
        const u = urgency(d.days_remaining);
        const unresolved = d.normalization_status && d.normalization_status !== 'ok';

        return (
          <li key={d.fact_id} className="rise" style={{ animationDelay: `${i * 45}ms` }}>
            <Link
              href={`/documents/${d.document_id}?fact=${d.fact_id}&page=${d.source_page}`}
              className="group relative flex gap-3 rounded-[--radius-inner] py-2.5 pl-6 pr-3 transition-colors duration-200 hover:bg-raised"
            >
              <span
                aria-hidden
                className={cn(
                  'absolute left-0 top-[1.15rem] size-[11px] rounded-full border-[2.5px] border-bg transition-transform duration-200 group-hover:scale-125',
                  u.dot,
                )}
              />

              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <p
                    className={cn(
                      'truncate text-[13px] font-medium',
                      isBangla(d.label) && 'bn',
                    )}
                    lang={isBangla(d.label) ? 'bn' : undefined}
                  >
                    {d.label}
                  </p>
                  {d.is_primary && (
                    <span className="shrink-0 border-l border-accent-line pl-1.5 font-mono text-[10px] text-accent">
                      primary
                    </span>
                  )}
                </div>

                <p className="truncate text-[11.5px] text-ink-3">{d.document_title}</p>

                {unresolved && (
                  <p className="mt-1 text-[11px] text-warn">
                    {d.normalization_status === 'ambiguous'
                      ? 'Date could be read two ways — confirm it'
                      : 'Date could not be resolved'}
                  </p>
                )}
              </div>

              <div className="shrink-0 text-right">
                <p className={cn('font-mono text-[12px]', u.tone)}>{formatDayMonth(d.due_at)}</p>
                <p className="font-mono text-[10.5px] text-ink-4">{u.label}</p>
              </div>
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
