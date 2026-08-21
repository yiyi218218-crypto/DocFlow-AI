import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Deadlines are what this product is for, so they get relative phrasing —
 * "in 3 days" is actionable in a way "15 Sep 2026" is not.
 */
export function formatDeadline(iso: string | null): string {
  if (!iso) return 'No date';
  const date = new Date(iso);
  const days = Math.ceil((date.getTime() - Date.now()) / 86_400_000);

  const absolute = date.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });

  if (days < 0) return `${absolute} · overdue`;
  if (days === 0) return `${absolute} · today`;
  if (days === 1) return `${absolute} · tomorrow`;
  if (days <= 30) return `${absolute} · in ${days} days`;
  return absolute;
}

export function deadlineUrgency(iso: string | null): 'overdue' | 'soon' | 'later' | 'none' {
  if (!iso) return 'none';
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
  if (days < 0) return 'overdue';
  if (days <= 7) return 'soon';
  return 'later';
}

export function relativeTime(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

/** Bangla needs a different font stack and more line height. */
export function isBangla(text: string): boolean {
  let bengali = 0;
  let letters = 0;
  for (const char of text) {
    if (/\p{L}/u.test(char)) {
      letters += 1;
      if (char >= 'ঀ' && char <= '৿') bengali += 1;
    }
  }
  return letters > 0 && bengali / letters > 0.3;
}

/** Compact date for dense rails: "15 Sep". */
export function formatDayMonth(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}
