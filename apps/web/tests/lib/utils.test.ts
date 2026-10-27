import { afterEach, describe, expect, it, vi } from 'vitest';

import { cn, deadlineUrgency, formatBytes, formatDeadline, formatDayMonth, isBangla } from '../../lib/utils';

describe('cn', () => {
  it('merges conflicting Tailwind utility classes', () => {
    expect(cn('px-2 text-sm', 'px-4')).toBe('text-sm px-4');
  });
});

describe('formatBytes', () => {
  it('formats byte, kilobyte, and megabyte values', () => {
    expect(formatBytes(12)).toBe('12 B');
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1.5 * 1024 * 1024)).toBe('1.5 MB');
  });
});

describe('deadline display timezone', () => {
  const endOfDayDhaka = '2026-09-15T23:59:59+06:00';
  const explicitTimeDhaka = '2026-09-15T17:00:00+06:00';

  it('keeps the source calendar date stable across viewer timezones', () => {
    expect(formatDayMonth(endOfDayDhaka)).toBe('15 Sept');
    expect(formatDayMonth(endOfDayDhaka, 'Asia/Dhaka')).toBe('15 Sept');
    expect(formatDayMonth(endOfDayDhaka, 'Asia/Shanghai')).toBe('16 Sept');
    expect(formatDayMonth(explicitTimeDhaka)).toBe('15 Sept');
  });

  it('uses the source timezone for the absolute deadline label', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-20T00:00:00.000Z'));
    expect(formatDeadline(endOfDayDhaka)).toBe('15 Sept 2026 · overdue');
  });
});

describe('deadlineUrgency', () => {
  afterEach(() => vi.useRealTimers());

  it('handles missing, overdue, near, and later deadlines', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-27T00:00:00.000Z'));

    expect(deadlineUrgency(null)).toBe('none');
    expect(deadlineUrgency('2026-10-26T00:00:00.000Z')).toBe('overdue');
    expect(deadlineUrgency('2026-11-03T00:00:00.000Z')).toBe('soon');
    expect(deadlineUrgency('2026-11-04T00:00:00.000Z')).toBe('later');
  });
});

describe('isBangla', () => {
  it('recognizes Bengali text without classifying English text', () => {
    expect(isBangla('বাংলা ভাষা')).toBe(true);
    expect(isBangla('English text')).toBe(false);
  });
});
